"""Durable switch history.

The daemon and watchdog logs are noisy, one line per poll. This file keeps only
what you need when asking "why did it switch (or not)": every switch with the
trigger, the accounts involved and the usage of every account at that moment,
plus daemon start/stop and errors. One JSON object per line, capped in size.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from claude_swap import paths

MAX_ENTRIES = 500
FILE_NAME = "switch-history.jsonl"


def history_path(root: Path | None = None) -> Path:
    return (root if root is not None else paths.get_backup_root()) / FILE_NAME


def record(entry: dict, *, root: Path | None = None) -> None:
    """Append one entry. Never raises: history must not break a switch."""
    try:
        path = history_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **entry})
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        _trim(path)
    except OSError:
        return


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= MAX_ENTRIES:
        return
    path.write_text("\n".join(lines[-MAX_ENTRIES:]) + "\n", encoding="utf-8")


def recent(count: int = 20, *, root: Path | None = None) -> list[dict]:
    path = history_path(root)
    if not path.exists():
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-count:]:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def _usage(windows: dict) -> str:
    parts = []
    for number, labels in sorted(windows.items()):
        parts.append(f"#{number}: " + " ".join(f"{k} {v:g}%" for k, v in labels.items()))
    return " | ".join(parts)


def describe(entry: dict) -> str:
    """One readable line for an entry."""
    stamp = str(entry.get("ts", ""))[:19].replace("T", " ")
    kind = entry.get("kind", "event")
    if "switch" == kind:
        src = entry.get("from") or {}
        dst = entry.get("to") or {}
        line = (
            f"{stamp}  switched #{src.get('number', '?')} -> #{dst.get('number', '?')} "
            f"({dst.get('email', '?')})  by {entry.get('source', '?')}, {entry.get('trigger', '?')}"
        )
        usage = _usage(entry.get("usage") or {})
        return line + (f"\n{'':21}usage then: {usage}" if usage else "")
    detail = entry.get("detail", "")
    return f"{stamp}  {kind}" + (f"  {detail}" if detail else "")
