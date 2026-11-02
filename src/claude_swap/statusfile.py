"""Live auto-switch state for other tools (the Claude Code status line).

The daemon rewrites this small file on every poll and marks it stopped on a clean
shutdown. A reader needs no network and no g2swap call: if the file is fresh the
daemon is running, if it is old or says stopped the user is in manual mode.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from claude_swap import paths
from claude_swap.lines import binding_window

FILE_NAME = "auto-status.json"


def status_path(root: Path | None = None) -> Path:
    return (root if root is not None else paths.get_backup_root()) / FILE_NAME


def _write(payload: dict, root: Path | None) -> None:
    """Atomic replace, never raises: a status file must not break the daemon."""
    try:
        path = status_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return


def build(event, interval_seconds: float, now: float | None = None) -> dict:
    """The payload for one poll event."""
    active = event.active or {}
    number = str(active.get("number", ""))
    payload: dict = {
        "ts": int(time.time() if now is None else now),
        "mode": "auto",
        "staleAfter": int(interval_seconds * 3 + 30),
        "active": {"number": number, "email": active.get("email", "")},
        "binding": None,
    }
    windows = event.windows.get(number)
    if windows:
        bound = binding_window(
            ((label, pct, None) for label, pct in windows.items()),
            event.threshold,
            event.weekly_threshold,
        )
        if bound is not None:
            payload["binding"] = {
                "window": bound.label,
                "used": round(bound.pct, 1),
                "line": round(bound.line, 1),
            }
    return payload


def write_poll(event, interval_seconds: float, *, root: Path | None = None) -> None:
    _write(build(event, interval_seconds), root)


def write_stopped(*, root: Path | None = None) -> None:
    _write({"ts": int(time.time()), "mode": "manual", "staleAfter": 0}, root)
