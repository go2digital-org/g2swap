"""Check GitHub releases for newer versions of g2swap."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from claude_swap import daemon
from claude_swap.cache import CACHE_DIR, MISSING, read_cache, write_cache

CACHE_PATH = CACHE_DIR / "update_check.json"
CACHE_TTL = 24 * 3600  # A successful lookup is kept for a day.
FAILURE_TTL = 3600  # A failed one is retried after an hour.
RELEASES_URL = "https://api.github.com/repos/go2digital-org/g2swap/releases/latest"

REPO_URL = "https://github.com/go2digital-org/g2swap"
MANUAL_STEPS = "Update by hand with uv:\n  uv tool install --reinstall git+{repo}@{tag}"

_VERSION_RE = re.compile(
    r"(\d+(?:\.\d+)*)(?:[-_.]?(alpha|beta|preview|pre|rc|a|b|c)[-_.]?(\d+)?)?",
    re.IGNORECASE,
)
_PRE_RANKS = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "preview": 2, "pre": 2, "rc": 2, "c": 2}
_FINAL_RANK = 3


class _Version(NamedTuple):
    """A version as a sort key. NamedTuple so that comparing two of these
    compares the release first and only then the pre-release fields, which is
    what puts 0.27.0b1 below 0.27.0 and both below 0.28.0."""

    release: tuple[int, ...]
    pre_rank: int  # _FINAL_RANK when this is not a pre-release.
    pre_number: int


def _parse_version(v: str) -> _Version:
    """Parse a release number with an optional PEP 440 pre-release suffix.

    Pre-releases such as 1.1.0b1 sort below 1.1.0. Development and post
    releases are not modeled and collapse onto the release they belong to.
    """
    m = _VERSION_RE.match(v)
    if m is None:
        raise ValueError(f"unrecognized version: {v!r}")
    release = tuple(int(x) for x in m.group(1).split("."))
    # PEP 440 makes 0.27 and 0.27.0 the same release, and the pre-release rank
    # only means anything once the release segments line up.
    while len(release) > 1 and release[-1] == 0:
        release = release[:-1]
    if m.group(2) is None:
        return _Version(release, _FINAL_RANK, 0)
    return _Version(release, _PRE_RANKS[m.group(2).lower()], int(m.group(3) or 0))


def _is_newer(latest: str, current: str) -> bool:
    """Whether the latest release is an upgrade worth telling the user about."""
    latest_version = _parse_version(latest)
    current_version = _parse_version(current)
    # Nobody on a final release asked to be moved onto a pre-release, so we
    # stay quiet for them; people already running one still hear about later
    # pre-releases.
    if latest_version.pre_rank != _FINAL_RANK and current_version.pre_rank == _FINAL_RANK:
        return False
    return latest_version > current_version


def _detect_install_method() -> str | None:
    """Return 'uv', 'pipx', or None if we can't tell."""
    prefix = Path(sys.prefix)
    parts = tuple(p.lower() for p in prefix.parts)
    pairs = list(zip(parts, parts[1:]))

    if ("uv", "tools") in pairs:
        return "uv"
    if ("pipx", "venvs") in pairs:
        return "pipx"

    # Env-var override: only trust if sys.prefix is actually under it.
    for env_var, name in (("UV_TOOL_DIR", "uv"), ("PIPX_HOME", "pipx")):
        root = os.environ.get(env_var)
        if root:
            try:
                if prefix.is_relative_to(Path(root)):
                    return name
            except (ValueError, OSError):
                pass
    return None


def _latest_tag(timeout: float = 2) -> str | None:
    """The newest GitHub release tag exactly as published (for example 'v1.1.0'), or None."""
    try:
        req = urllib.request.Request(
            RELEASES_URL, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return str(data["tag_name"])
    except Exception:
        return None


def _latest_release() -> str | None:
    """The newest GitHub release version without its leading 'v', or None."""
    tag = _latest_tag()
    return None if tag is None else tag.lstrip("v")


def update_available(current_version: str) -> str | None:
    """The newest release version when it is newer than ``current_version``, else None.

    Reads the on-disk cache first, so this is cheap and never raises.
    """
    try:
        cached = read_cache(CACHE_PATH, CACHE_TTL)
        if cached is None:
            cached = read_cache(CACHE_PATH, FAILURE_TTL)
        latest = cached
        if cached is MISSING or not isinstance(cached, str | None):
            latest = _latest_release()
            write_cache(CACHE_PATH, latest)

        if latest and _is_newer(latest, current_version):
            return str(latest)
        return None
    except Exception:
        return None


def check_for_update(current_version: str) -> str | None:
    """Return a notification string if a newer release exists, else None."""
    latest = update_available(current_version)
    if latest is None:
        return None
    return (
        f"A newer version of g2swap is available ({latest}). "
        f"You are using {current_version}. Run `g2swap update` to install it."
    )


def _install_command(tag: str) -> list[str]:
    return ["uv", "tool", "install", "--reinstall", f"git+{REPO_URL}@{tag}"]


def _confirm(question: str, input_fn: Callable[[str], str]) -> bool:
    try:
        return input_fn(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run_self_upgrade(
    current_version: str,
    *,
    assume_yes: bool = False,
    check_only: bool = False,
    input_fn: Callable[[str], str] = input,
    interactive: bool | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    restart: Callable[[], list[str]] = daemon.restart_all,
    platform: str = sys.platform,
    spawn: Callable[..., object] = subprocess.Popen,
) -> int:
    """Look up the latest GitHub release and, on a yes, install exactly that tag.

    Exit code: 0 when up to date or updated, 1 when the check or install failed.
    """
    tag = _latest_tag(timeout=10)
    if tag is None:
        print("Could not reach GitHub to look up the latest release.")
        return 1
    latest = tag.lstrip("v")
    if not _is_newer(latest, current_version):
        print(f"g2swap is up to date ({current_version}).")
        return 0

    print(f"Update available: {current_version} -> {latest}")
    if check_only:
        return 0

    tty = sys.stdin.isatty() if interactive is None else interactive
    if not assume_yes and not tty:
        print("Run `g2swap update --yes` to install it without a prompt.")
        return 0
    if not assume_yes and not _confirm("Update now?", input_fn):
        print("Not updating.")
        return 0

    if which("uv") is None:
        print("uv was not found on PATH.")
        print(MANUAL_STEPS.format(repo=REPO_URL, tag=tag))
        return 1

    command = _install_command(tag)
    if "win32" == platform:
        # Windows locks a running executable, so the install runs after this
        # process exits.
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        spawn(command, creationflags=flags, close_fds=True)
        print(f"Installing {latest} in the background. Open a new terminal in a minute.")
        return 0

    result = run(command, check=False)
    if 0 != result.returncode:
        print("The update failed. Your current version is unchanged.")
        print(MANUAL_STEPS.format(repo=REPO_URL, tag=tag))
        return 1
    restarted = restart()
    print(f"Updated to {latest}.")
    if restarted:
        print(f"Restarted: {', '.join(restarted)}.")
    return 0
