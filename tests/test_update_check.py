"""Tests for update_check module."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from claude_swap.update_check import (
    CACHE_TTL,
    FAILURE_TTL,
    UPDATE_STEPS,
    _detect_install_method,
    _is_newer,
    _parse_version,
    check_for_update,
    run_self_upgrade,
)


def _release_response(tag: str) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps({"tag_name": tag}).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _write_cache(path, version, timestamp=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"timestamp": timestamp if timestamp is not None else time.time(), "data": version}
        )
    )


@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    monkeypatch.setattr("claude_swap.update_check.CACHE_PATH", path)
    return path


@patch("claude_swap.update_check.urllib.request.urlopen")
class TestCheckForUpdate:
    def test_newer_release_is_announced(self, mock_urlopen, cache):
        mock_urlopen.return_value = _release_response("v1.1.0")

        result = check_for_update("1.0.0")

        assert result is not None
        assert "1.1.0" in result
        assert "g2swap" in result
        assert "cswap upgrade" in result

    def test_tag_without_v_prefix(self, mock_urlopen, cache):
        mock_urlopen.return_value = _release_response("1.1.0")

        assert "1.1.0" in (check_for_update("1.0.0") or "")

    def test_already_on_latest(self, mock_urlopen, cache):
        mock_urlopen.return_value = _release_response("v1.0.0")

        assert check_for_update("1.0.0") is None

    def test_network_error_is_quiet_and_cached(self, mock_urlopen, cache):
        mock_urlopen.side_effect = OSError("offline")

        assert check_for_update("1.0.0") is None
        assert json.loads(cache.read_text())["data"] is None

    def test_failed_lookup_is_not_retried_within_the_hour(self, mock_urlopen, cache):
        _write_cache(cache, None, time.time() - FAILURE_TTL + 60)

        assert check_for_update("1.0.0") is None
        mock_urlopen.assert_not_called()

    def test_failed_lookup_is_retried_after_an_hour(self, mock_urlopen, cache):
        _write_cache(cache, None, time.time() - FAILURE_TTL - 60)
        mock_urlopen.return_value = _release_response("v1.1.0")

        assert check_for_update("1.0.0") is not None

    def test_fresh_cache_skips_the_network(self, mock_urlopen, cache):
        _write_cache(cache, "1.1.0")

        assert check_for_update("1.0.0") is not None
        mock_urlopen.assert_not_called()

    def test_stale_cache_is_refetched(self, mock_urlopen, cache):
        _write_cache(cache, "1.0.5", time.time() - CACHE_TTL - 60)
        mock_urlopen.return_value = _release_response("v1.2.0")

        assert "1.2.0" in (check_for_update("1.0.0") or "")

    def test_unparseable_tag_stays_quiet(self, mock_urlopen, cache):
        mock_urlopen.return_value = _release_response("nightly")

        assert check_for_update("1.0.0") is None

    def test_asks_github_not_pypi(self, mock_urlopen, cache):
        mock_urlopen.return_value = _release_response("v1.0.0")

        check_for_update("1.0.0")

        url = mock_urlopen.call_args.args[0].full_url
        assert url.startswith("https://api.github.com/repos/go2digital-org/g2swap/")


class TestVersionOrdering:
    @pytest.mark.parametrize(
        ("latest", "current"),
        [("1.1.0", "1.0.0"), ("1.0.0", "1.0.0b1"), ("1.0.0b2", "1.0.0b1"), ("2.0", "1.9.9")],
    )
    def test_newer(self, latest, current):
        assert _is_newer(latest, current)

    @pytest.mark.parametrize(
        ("latest", "current"),
        [("1.0.0", "1.0.0"), ("1.0.0", "1.1.0"), ("1.1.0b1", "1.0.0"), ("1.0", "1.0.0")],
    )
    def test_not_newer(self, latest, current):
        assert not _is_newer(latest, current)

    def test_unrecognized_version_raises(self):
        with pytest.raises(ValueError):
            _parse_version("nightly")


class TestDetectInstallMethod:
    def _prefix(self, monkeypatch, path):
        monkeypatch.setattr("claude_swap.update_check.sys.prefix", str(path))
        monkeypatch.delenv("UV_TOOL_DIR", raising=False)
        monkeypatch.delenv("PIPX_HOME", raising=False)

    def test_uv_tool_default_path(self, monkeypatch):
        self._prefix(monkeypatch, "/home/u/.local/share/uv/tools/claude-swap")
        assert _detect_install_method() == "uv"

    def test_pipx_default_path(self, monkeypatch):
        self._prefix(monkeypatch, "/home/u/.local/pipx/venvs/claude-swap")
        assert _detect_install_method() == "pipx"

    def test_source_checkout_returns_none(self, monkeypatch):
        self._prefix(monkeypatch, "/home/u/projects/g2swap/.venv")
        assert _detect_install_method() is None

    def test_uv_tool_dir_env_override(self, monkeypatch, tmp_path):
        self._prefix(monkeypatch, tmp_path / "custom" / "claude-swap")
        monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "custom"))
        assert _detect_install_method() == "uv"


class TestRunSelfUpgrade:
    @patch("claude_swap.update_check.subprocess", create=True)
    def test_prints_steps_and_runs_nothing(self, mock_subprocess, capsys):
        assert run_self_upgrade() == 0

        out = capsys.readouterr().out
        assert out.strip() == UPDATE_STEPS
        assert "git pull" in out
        assert "install.sh" in out
        mock_subprocess.run.assert_not_called()

    def test_never_names_the_pypi_package(self, capsys):
        run_self_upgrade()

        assert "pip" not in capsys.readouterr().out.lower()
