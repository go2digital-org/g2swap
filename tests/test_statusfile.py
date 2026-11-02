"""Tests for the live auto-switch state file."""

from __future__ import annotations

import json
from types import SimpleNamespace

from claude_swap import statusfile


def _poll(windows, number="1", threshold=80.0, weekly=97.0):
    return SimpleNamespace(
        active={"number": number, "email": "a@example.com"},
        windows=windows,
        threshold=threshold,
        weekly_threshold=weekly,
    )


def test_poll_names_the_binding_window_and_its_line():
    payload = statusfile.build(_poll({"1": {"5h": 62.0, "7d": 40.0}}), 30, now=1000)

    assert payload["mode"] == "auto"
    assert payload["ts"] == 1000
    assert payload["staleAfter"] == 120
    assert payload["active"] == {"number": "1", "email": "a@example.com"}
    assert payload["binding"] == {"window": "5h", "used": 62.0, "line": 80.0}


def test_weekly_window_uses_the_weekly_line():
    payload = statusfile.build(_poll({"1": {"5h": 10.0, "7d": 91.0}}), 30, now=1000)

    assert payload["binding"] == {"window": "7d", "used": 91.0, "line": 97.0}


def test_unknown_usage_has_no_binding():
    assert statusfile.build(_poll({}), 30, now=1000)["binding"] is None


def test_write_then_stop_marks_manual(tmp_path):
    statusfile.write_poll(_poll({"1": {"5h": 50.0}}), 30, root=tmp_path)
    assert json.loads(statusfile.status_path(tmp_path).read_text())["mode"] == "auto"

    statusfile.write_stopped(root=tmp_path)
    assert json.loads(statusfile.status_path(tmp_path).read_text())["mode"] == "manual"


def test_unwritable_root_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")

    statusfile.write_poll(_poll({}), 30, root=blocker / "sub")
