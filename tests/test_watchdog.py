"""Tests for the hard-limit watchdog."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from claude_swap.watchdog import COOLDOWN_S, Watchdog


def _result(payload: dict | str) -> subprocess.CompletedProcess[str]:
    out = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=out, stderr="")


class _Cswap:
    """Stands in for the g2swap subprocess: a status per call, a record of switches."""

    def __init__(self, statuses: list[str], email: str = "pro@example.com") -> None:
        self.statuses = statuses
        self.email = email
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args[0] != "status":
            return _result("")
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return _result({"active": {"usageStatus": status, "email": self.email}})

    def switches(self) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[0] == "switch"]


def _dog(g2swap, tmp_path, notes=None):
    notes = notes if notes is not None else []
    return Watchdog(run=g2swap, notifier=notes.append, log_dir=tmp_path / "logs"), notes


def test_ok_account_does_nothing(tmp_path):
    g2swap = _Cswap(["ok"])
    dog, notes = _dog(g2swap, tmp_path)

    assert dog.tick(1000.0) is False
    assert g2swap.switches() == []
    assert notes == []


@pytest.mark.parametrize("status", ["rate_limited", "exhausted"])
def test_limited_account_switches_and_notifies(tmp_path, status):
    g2swap = _Cswap([status, status, "ok"])
    dog, notes = _dog(g2swap, tmp_path)

    assert dog.tick(1000.0) is True
    assert g2swap.switches() == [("switch", "--strategy", "next-available")]
    assert len(notes) == 1
    assert "Switched to" in notes[0]


def test_cooldown_blocks_a_second_switch(tmp_path):
    g2swap = _Cswap(["rate_limited"])
    dog, _ = _dog(g2swap, tmp_path)

    assert dog.tick(1000.0) is True
    assert dog.tick(1000.0 + COOLDOWN_S - 1) is False
    assert dog.tick(1000.0 + COOLDOWN_S + 1) is True
    assert len(g2swap.switches()) == 2


def test_unreadable_status_is_treated_as_ok(tmp_path):
    def broken(*args):
        return _result("not json")

    dog = Watchdog(run=broken, notifier=lambda m: None, log_dir=tmp_path / "logs")

    assert dog.active() == ("ok", "")
    assert dog.tick(1000.0) is False


def _write_log(tmp_path, text, mtime):
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / "session.log"
    path.write_text(text)
    os.utime(path, (mtime, mtime))


def test_fresh_log_with_limit_text_switches(tmp_path):
    _write_log(tmp_path, "ok\nError: 5-hour limit reached\n", mtime=990.0)
    g2swap = _Cswap(["ok"])
    dog, notes = _dog(g2swap, tmp_path)

    assert dog.tick(1000.0) is True
    assert len(g2swap.switches()) == 1
    assert len(notes) == 1


def test_stale_log_is_ignored(tmp_path):
    _write_log(tmp_path, "Error: rate limit\n", mtime=100.0)
    g2swap = _Cswap(["ok"])
    dog, _ = _dog(g2swap, tmp_path)

    assert dog.tick(1000.0) is False


def test_fresh_log_without_limit_text_is_ignored(tmp_path):
    _write_log(tmp_path, "all good\n", mtime=995.0)
    g2swap = _Cswap(["ok"])
    dog, _ = _dog(g2swap, tmp_path)

    assert dog.tick(1000.0) is False


def test_missing_log_dir_is_fine(tmp_path):
    g2swap = _Cswap(["ok"])
    dog, _ = _dog(g2swap, tmp_path)

    assert dog.tick(1000.0) is False
