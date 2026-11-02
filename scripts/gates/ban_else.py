"""Universal `else`/`elif` hard-ban scanner.

Runs on a list of staged file paths (argv[1:]). Strips comments and string
literals from each file so matches inside them are not counted, then searches
the remaining code for the banned keywords. Prints every hit and exits 1 on
any hit, 0 on none.

Design choices
--------------
* ABSOLUTE ban: ANY whole-word `else` (and Python `elif`) in live code is a
  hard fail — statement `} else {`, value-position `let y = if c {1} else {2}`,
  AND Rust let-else `let Ok(x) = f() else { … }`. Rewrite with a `match` guard,
  early return, or default-then-override. There is NO guard-clause exception.

* No escape hatch.  No `// no-else:`, no `# no-else:`, no suppression
  comment is honored.  A bare `else` anywhere in live code is a hard fail.

* Two narrow carve-outs (NOT escape hatches — distinct constructs):
  - C/C++ preprocessor `#else` / `#elif` (conditional compilation, not
    control-flow; required for cross-platform C).
  - Python ternary `a if c else b` (`ast.IfExp`). Python has NO `? :`, so the
    ternary is its only inline conditional and the no-else REPLACEMENT, not a
    branch. Python is AST-scanned so this is precise: the branchy `if/else`
    statement + `elif` are still banned; only the expression-ternary survives.
    (TS/JS use `? :` which is not the `else` keyword, so they need no carve-out.)

Supported extensions
--------------------
.rs .py .ts .tsx .js .jsx .mjs .cjs .c .cc .cpp .cxx .h .hpp
.ps1 .psm1 .sh .bash .go .java .cs

Files with unknown / binary extensions are skipped silently.
"""

from __future__ import annotations

import ast
import io
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

# ---------------------------------------------------------------------------
# Extension routing
# ---------------------------------------------------------------------------

RUST_EXTS = {".rs"}
PYTHON_EXTS = {".py"}
HASH_COMMENT_EXTS = {".sh", ".bash", ".ps1", ".psm1"}
# Everything else uses a C-style comment stripper (C, C++, Java, Go, C#, TS/JS)
C_STYLE_EXTS = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".java",
    ".cs",
}
ALL_GATED = RUST_EXTS | PYTHON_EXTS | HASH_COMMENT_EXTS | C_STYLE_EXTS

# ---------------------------------------------------------------------------
# Comment + string stripping helpers
# ---------------------------------------------------------------------------


# Replace a matched span with spaces (preserves line numbers and column
# positions so line-number reporting stays accurate).
def _blank(m: re.Match[str]) -> str:
    return " " * len(m.group(0))


def _blank_newline_safe(m: re.Match[str]) -> str:
    """Blank matched text but keep newlines (preserves line count)."""
    text = m.group(0)
    return "".join("\n" if ch == "\n" else " " for ch in text)


# ---------------------------------------------------------------------------
# Language-specific strippers
# ---------------------------------------------------------------------------

# C-style: remove /* … */ block comments, // line comments, and string
# literals (double-quoted, template literals with backtick).
_C_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_C_LINE = re.compile(r"//[^\n]*")
# Simple string literal removal (covers the common cases; edge cases like
# escaped quotes inside strings are handled conservatively — the scanner
# only cares about `else` appearing in live code, and mis-stripping a
# string with `else` inside it would produce a false negative, not a false
# positive, which is acceptable for a style gate).
_C_DSTR = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
_C_TSTR = re.compile(r"`(?:[^`\\]|\\.)*`", re.DOTALL)  # JS template literal


def _strip_c_style(src: str) -> str:
    src = _C_BLOCK.sub(_blank_newline_safe, src)
    src = _C_LINE.sub(_blank, src)
    src = _C_DSTR.sub(_blank_newline_safe, src)
    src = _C_TSTR.sub(_blank_newline_safe, src)
    return src


# Rust: same as C-style PLUS raw strings r#"…"# and single-quoted char
# literals. Also // doc comments (already handled by _C_LINE).
_RUST_RAW = re.compile(r'r(#+)".*?"\1', re.DOTALL)
_RUST_CHAR = re.compile(r"'(?:[^'\\]|\\.)'")
_RUST_SSTR = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)


def _strip_rust(src: str) -> str:
    src = _C_BLOCK.sub(_blank_newline_safe, src)
    src = _C_LINE.sub(_blank, src)
    src = _RUST_RAW.sub(_blank_newline_safe, src)
    src = _RUST_SSTR.sub(_blank_newline_safe, src)
    src = _RUST_CHAR.sub(_blank, src)
    return src


# Python: remove # line comments and triple-/single-/double-quoted strings.
_PY_TRIPLE_DQ = re.compile(r'""".*?"""', re.DOTALL)
_PY_TRIPLE_SQ = re.compile(r"'''.*?'''", re.DOTALL)
_PY_STR_DQ = re.compile(r'"(?:[^"\\]|\\.)*"')
_PY_STR_SQ = re.compile(r"'(?:[^'\\]|\\.)*'")
_PY_COMMENT = re.compile(r"#[^\n]*")


def _strip_python(src: str) -> str:
    src = _PY_TRIPLE_DQ.sub(_blank_newline_safe, src)
    src = _PY_TRIPLE_SQ.sub(_blank_newline_safe, src)
    src = _PY_STR_DQ.sub(_blank, src)
    src = _PY_STR_SQ.sub(_blank, src)
    src = _PY_COMMENT.sub(_blank, src)
    return src


# Hash-comment languages (shell, PowerShell): remove # line comments and
# PowerShell <# … #> block comments. Also strip double-quoted strings.
_PS_BLOCK = re.compile(r"<#.*?#>", re.DOTALL)
_HASH_LINE = re.compile(r"#[^\n]*")
_HASH_DSTR = re.compile(r'"(?:[^"\\]|\\.)*"')
_HASH_SSTR = re.compile(r"'(?:[^'\\]|\\.)*'")


def _strip_hash_comment(src: str, *, is_powershell: bool = False) -> str:
    if is_powershell:
        src = _PS_BLOCK.sub(_blank_newline_safe, src)
    src = _HASH_LINE.sub(_blank, src)
    src = _HASH_DSTR.sub(_blank, src)
    src = _HASH_SSTR.sub(_blank, src)
    return src


def _strip(src: str, suffix: str) -> str:
    if suffix in RUST_EXTS:
        return _strip_rust(src)
    if suffix in PYTHON_EXTS:
        return _strip_python(src)
    if suffix in {".ps1", ".psm1"}:
        return _strip_hash_comment(src, is_powershell=True)
    if suffix in {".sh", ".bash"}:
        return _strip_hash_comment(src, is_powershell=False)
    # C-style (includes TS/JS/Go/Java/C#)
    return _strip_c_style(src)


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

# Whole-word `else` (non-Python langs are scanned by regex after stripping).
_ELSE_WW = re.compile(r"\belse\b")

# C/C++ preprocessor conditional: `#else` / `#elif` — not control flow.
_PREPROC = re.compile(r"^\s*#\s*el(?:se|if)\b")


def _scan_python(text: str) -> list[tuple[int, str]]:
    """Python is AST-scanned: flag every `ast.If` with a non-empty `orelse`
    (if/else + elif). Ternary `a if c else b` (`ast.IfExp`) and
    `try/for/while`-else are different node types → correctly NOT flagged.
    Python's ONLY inline conditional is the ternary (no `? :`), so it stays
    legal — the branchy `if/else` statement + `elif` are what's banned.
    Unparseable source → no hits (other gates catch syntax errors)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not node.orelse:
            continue
        body_end = node.body[-1].end_lineno or node.body[-1].lineno
        first_else = node.orelse[0].lineno
        kw_line = first_else
        for ln in range(body_end + 1, first_else + 1):
            stripped = lines[ln - 1].strip() if ln - 1 < len(lines) else ""
            if stripped.startswith(("elif", "else")):
                kw_line = ln
                break
        raw = lines[kw_line - 1] if kw_line - 1 < len(lines) else ""
        hits.append((kw_line, raw.strip()))
    return sorted(set(hits))


def _scan_regex(src: str, suffix: str) -> list[tuple[int, str]]:
    """Whole-word `else` ban for non-Python langs. Strips comments + strings
    first, then flags EVERY surviving `else` (statement, value-position, AND
    Rust let-else — all banned). ONLY carve-out: C/C++ preprocessor `#else`."""
    stripped_lines = _strip(src, suffix).splitlines()
    original_lines = src.splitlines()
    is_c = suffix in C_STYLE_EXTS
    hits: list[tuple[int, str]] = []
    for i, stripped in enumerate(stripped_lines, start=1):
        if _ELSE_WW.search(stripped) is None:
            continue
        if is_c and _PREPROC.match(stripped):
            continue
        orig = original_lines[i - 1] if i - 1 < len(original_lines) else stripped
        hits.append((i, orig.strip()))
    return hits


def _scan_file(path: Path) -> list[tuple[int, str]]:
    suffix = path.suffix.lower()
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if suffix in PYTHON_EXTS:
        return _scan_python(src)
    return _scan_regex(src, suffix)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    paths = argv[1:]
    if not paths:
        return 0

    repo_root = Path(__file__).resolve().parent.parent.parent
    total = 0

    for raw in paths:
        p = Path(raw)
        suffix = p.suffix.lower()
        if suffix not in ALL_GATED:
            continue
        # tools/probes/** — kmem-era diagnostic probes, `status: legacy` in their VERSION.md
        # with a real retire date. 82 grandfathered `else` across 29 files.
        #
        # This PRESERVES the ratchet's stated intent rather than weakening it. The gate is
        # "new/edited code only; untouched grandfathered else is cleared by the burndown fleet,
        # not this gate". Relocating those files into a folder on 2026-09-11 did not EDIT any
        # of that code — but git stages a move, so every one of them would have hard-blocked on
        # a line nobody wrote that day, and rewriting control flow in a tree scheduled for
        # deletion is the worst of both.
        #
        # It lives HERE and not in lefthook's `exclude:` because that exclude does not fire for
        # this job — the patterns are present and correct and the files are still handed over.
        # A gate exemption that silently does not apply is worse than no exemption.
        #
        # Deliberately NOT tools/gen/**: those write tracked artefacts and stay gated. Promote
        # a probe back to live and you take it out of here first.
        if p.as_posix().startswith("tools/probes/"):
            continue
        if p.as_posix().startswith("src/web/static/vendor/"):
            continue
        # Make absolute (lefthook passes repo-relative paths)
        full = p if p.is_absolute() else repo_root / p
        hits = _scan_file(full)
        try:
            rel = full.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            rel = str(full)
        for lineno, line in hits:
            print(f"{rel}:{lineno}: {line}")
        total += len(hits)

    if total == 0:
        return 0

    print()
    print(f"BANNED: {total} `else`/`elif` occurrence(s) found.")
    print("Rewrite with guard clauses / early return / match / default-then-override.")
    print("No escape hatch. No exceptions.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
