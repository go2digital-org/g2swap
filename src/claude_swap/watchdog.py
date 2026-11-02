"""Hard-limit watchdog: switch accounts when the active one reports a rate limit.

The auto-switch daemon polls on a slow cadence, so a limit can land between two
polls. This loop checks every few seconds and switches immediately. It runs on
macOS, Linux, WSL and Windows.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

POLL_INTERVAL_S = 10
COOLDOWN_S = 120
LOG_FRESH_S = 60
LOG_TAIL_BYTES = 8192
LIMITED_STATUSES = frozenset({"rate_limited", "exhausted"})
LIMIT_TEXT = re.compile(
    r"rate.limit|usage.limit|quota.exceeded|5.hour.limit|7.day.limit", re.IGNORECASE
)


def log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {message}", flush=True)


def run_cswap(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "claude_swap.cli", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def notify(message: str) -> None:
    """Desktop notification where the platform has a command line for it."""
    osascript = shutil.which("osascript")
    if osascript:
        script = f'display notification "{message}" with title "g2swap" sound name "Ping"'
        subprocess.run([osascript, "-e", script], capture_output=True, check=False)
        return
    notify_send = shutil.which("notify-send")
    if notify_send:
        subprocess.run([notify_send, "g2swap", message], capture_output=True, check=False)


class Watchdog:
    def __init__(
        self,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = run_cswap,
        notifier: Callable[[str], None] = notify,
        log_dir: Path | None = None,
    ) -> None:
        self._run = run
        self._notify = notifier
        self._log_dir = log_dir if log_dir is not None else Path.home() / ".claude" / "logs"
        self.last_switch = 0.0

    def active(self) -> tuple[str, str]:
        """The active account's usage status and email, or ("ok", "") when unknown."""
        try:
            data = json.loads(self._run("status", "--json").stdout)
            active = data.get("active") or {}
            return str(active.get("usageStatus", "ok")), str(active.get("email", ""))
        except (ValueError, OSError, subprocess.SubprocessError):
            return "ok", ""

    def log_shows_limit(self, now: float) -> bool:
        logs = sorted(self._log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not logs:
            return False
        newest = logs[0]
        if now - newest.stat().st_mtime > LOG_FRESH_S:
            return False
        with newest.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - LOG_TAIL_BYTES))
            tail = handle.read().decode("utf-8", errors="ignore")
        return bool(LIMIT_TEXT.search("\n".join(tail.splitlines()[-50:])))

    def reason(self, now: float) -> str | None:
        status, email = self.active()
        if status in LIMITED_STATUSES:
            return f"Rate limit detected on {email}"
        if self._log_dir.is_dir() and self.log_shows_limit(now):
            return "Rate-limit text found in the Claude log"
        return None

    def tick(self, now: float) -> bool:
        """Switch if a limit is showing and the cooldown has passed. True when it switched."""
        if now - self.last_switch <= COOLDOWN_S:
            return False
        reason = self.reason(now)
        if reason is None:
            return False
        log(f"{reason}, switching")
        self._run("switch", "--strategy", "next-available")
        self.last_switch = now
        _, new_account = self.active()
        log(f"Switched to {new_account}")
        self._notify(f"Switched to {new_account}. Open a new Claude session.")
        return True


def run_watchdog(*, once: bool = False, interval: float = POLL_INTERVAL_S) -> int:
    watchdog = Watchdog()
    log(f"Watchdog started (poll {interval:g}s)")
    while True:
        watchdog.tick(time.time())
        if once:
            return 0
        time.sleep(interval)
