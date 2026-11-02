"""Tests for the durable switch history."""

from __future__ import annotations

from claude_swap import history


def test_record_and_read_back(tmp_path):
    history.record(
        {
            "kind": "switch",
            "source": "daemon",
            "trigger": "priority",
            "from": {"number": "1", "email": "a@example.com"},
            "to": {"number": "2", "email": "b@example.com"},
            "usage": {"1": {"5h": 100.0, "7d": 86.0}, "2": {"5h": 0.0, "7d": 21.0}},
        },
        root=tmp_path,
    )

    entries = history.recent(root=tmp_path)

    assert len(entries) == 1
    text = history.describe(entries[0])
    assert "switched #1 -> #2 (b@example.com)" in text
    assert "by daemon, priority" in text
    assert "#2: 5h 0% 7d 21%" in text


def test_empty_history(tmp_path):
    assert history.recent(root=tmp_path) == []


def test_history_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "MAX_ENTRIES", 5)
    for i in range(12):
        history.record({"kind": "daemon-start", "detail": str(i)}, root=tmp_path)

    entries = history.recent(100, root=tmp_path)

    assert [e["detail"] for e in entries] == ["7", "8", "9", "10", "11"]


def test_corrupt_line_is_skipped(tmp_path):
    history.record({"kind": "daemon-start"}, root=tmp_path)
    with history.history_path(tmp_path).open("a") as handle:
        handle.write("not json\n")

    assert len(history.recent(root=tmp_path)) == 1


def test_record_never_raises_on_an_unwritable_root(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")

    history.record({"kind": "daemon-start"}, root=blocker / "sub")
