"""commit-msg gate: reject AI attribution and tooling trailers.

Usage (lefthook commit-msg hook): python3 scripts/gates/check_commit_msg.py <msg-file>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BANNED = [
    re.compile(r"co-authored-by\s*:", re.I),
    re.compile(r"\bgenerated with\b", re.I),
    re.compile(r"claude code", re.I),
    re.compile(r"noreply@anthropic\.com", re.I),
    re.compile(r"\bwith\s+claude\b", re.I),
    re.compile(r"claude[-_ ]?session\s*:", re.I),
    re.compile(r"claude\.ai/", re.I),
    re.compile(r"\bsession_[0-9A-Za-z]{16,}\b"),
]

SUBJECT_MAX = 72


def body_without_comments(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check_commit_msg.py <msg-file>")
        return 2
    body = body_without_comments(Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace"))
    lines = body.strip().splitlines()
    if not lines:
        print("Commit message is empty.")
        return 1
    hits = [p.pattern for p in BANNED if p.search(body)]
    if hits:
        print("Commit message rejected: attribution or tooling trailer found.")
        for hit in hits:
            print(f"  matched: {hit}")
        return 1
    if len(lines[0]) > SUBJECT_MAX:
        print(f"Subject is {len(lines[0])} characters; keep it at {SUBJECT_MAX} or fewer.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
