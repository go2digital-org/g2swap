"""State and control of the background auto-switch daemon.

The daemon is a launchd agent (macOS), a systemd user service (Linux) or a
Task Scheduler job (Windows), registered by the installer. The TUI uses this to
show the real state instead of guessing, and to turn it on and off.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

LAUNCHD_LABEL = "g2swap.auto"
SYSTEMD_UNIT = "g2swap-auto.service"
TASK_NAME = "g2swap-auto"


def _run(*cmd: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def installed() -> bool:
    """Whether the installer registered a daemon on this machine."""
    if sys.platform == "darwin":
        return _plist().exists()
    if sys.platform == "win32":
        result = _run("schtasks", "/Query", "/TN", TASK_NAME)
        return result is not None and 0 == result.returncode
    unit = Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT
    return unit.exists()


def running() -> bool | None:
    """True or False for the daemon's state, None when no daemon is registered."""
    if not installed():
        return None
    if sys.platform == "darwin":
        result = _run("launchctl", "list")
        return result is not None and LAUNCHD_LABEL in result.stdout
    if sys.platform == "win32":
        result = _run("schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST")
        return result is not None and "Running" in result.stdout
    if shutil.which("systemctl") is None:
        return None
    result = _run("systemctl", "--user", "is-active", SYSTEMD_UNIT)
    return result is not None and "active" == result.stdout.strip()


def set_running(on: bool) -> bool:
    """Start or stop the daemon. True when the command succeeded."""
    if sys.platform == "darwin":
        verb = "load" if on else "unload"
        result = _run("launchctl", verb, str(_plist()))
        return result is not None and 0 == result.returncode
    if sys.platform == "win32":
        verb = "/Run" if on else "/End"
        result = _run("schtasks", verb, "/TN", TASK_NAME)
        return result is not None and 0 == result.returncode
    verb = "start" if on else "stop"
    result = _run("systemctl", "--user", verb, SYSTEMD_UNIT)
    return result is not None and 0 == result.returncode


WATCHDOG_LABEL = "g2swap.watchdog"
WATCHDOG_UNIT = "g2swap-watchdog.service"
WATCHDOG_TASK = "g2swap-watchdog"


def restart_all() -> list[str]:
    """Restart every registered background service so it runs freshly installed code.

    Returns the names of the services that were restarted.
    """
    restarted: list[str] = []
    if sys.platform == "darwin":
        agents = Path.home() / "Library" / "LaunchAgents"
        listed = _run("launchctl", "list")
        loaded = listed.stdout if listed is not None else ""
        for label in (LAUNCHD_LABEL, WATCHDOG_LABEL):
            plist = agents / f"{label}.plist"
            if not plist.exists() or label not in loaded:
                continue
            _run("launchctl", "unload", str(plist))
            result = _run("launchctl", "load", str(plist))
            if result is not None and 0 == result.returncode:
                restarted.append(label)
        return restarted
    if sys.platform == "win32":
        for task in (TASK_NAME, WATCHDOG_TASK):
            if _run("schtasks", "/Query", "/TN", task) is None:
                continue
            _run("schtasks", "/End", "/TN", task)
            result = _run("schtasks", "/Run", "/TN", task)
            if result is not None and 0 == result.returncode:
                restarted.append(task)
        return restarted
    if shutil.which("systemctl") is None:
        return restarted
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    for unit in (SYSTEMD_UNIT, WATCHDOG_UNIT):
        if not (unit_dir / unit).exists():
            continue
        result = _run("systemctl", "--user", "restart", unit)
        if result is not None and 0 == result.returncode:
            restarted.append(unit)
    return restarted
