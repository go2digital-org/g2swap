"""Modal screens: confirmations, token entry, and captured-output display."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class ConfirmModal(ModalScreen[bool]):
    """Yes/No confirmation. Dismisses with True only on explicit confirm.

    Keyboard-first: ←/→ move between the buttons (Enter presses the focused
    one), y/n answer directly, Esc cancels. Clicking still works.
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=False),
        Binding("n,escape", "cancel", "No", show=False),
        Binding("left", "app.focus_previous", show=False),
        Binding("right", "app.focus_next", show=False),
    ]

    def __init__(self, message: str, *, title: str = "Confirm", yes_label: str = "Yes") -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._yes_label = yes_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Label(self._title, classes="modal-title")
            yield Static(self._message, classes="modal-body")
            with Horizontal(classes="modal-buttons"):
                yield Button(self._yes_label, id="yes")
                yield Button("Cancel", id="no")
            yield Static(
                f"← → · enter  ·  y {self._yes_label.lower()}  ·  n / esc cancel",
                classes="modal-hint",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


@dataclass
class TokenForm:
    """What the add-token modal collects."""

    token: str
    email: str | None
    slot: int | None


class AddTokenModal(ModalScreen["TokenForm | None"]):
    """Collects a setup-token/API key, optional email label, optional slot.

    ←/→ only reach the screen when a Button is focused (a focused Input
    consumes them for cursor movement), so they safely double as button
    navigation.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("left", "app.focus_previous", show=False),
        Binding("right", "app.focus_next", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Label("Add account from token", classes="modal-title")
            yield Static(
                "OAuth setup-token (sk-ant-oat…) or Console API key "
                "(sk-ant-api…, sk-ant-usr…); the type is auto-detected.",
                classes="modal-body",
            )
            yield Input(password=True, placeholder="token (required)", id="token")
            yield Input(placeholder="email label (optional)", id="email")
            yield Input(placeholder="slot number (optional)", id="slot", type="integer")
            yield Static("", id="form-error", classes="form-error")
            with Horizontal(classes="modal-buttons"):
                yield Button("Add", id="add")
                yield Button("Cancel", id="cancel")
            yield Static(
                "enter add  ·  tab next field  ·  esc cancel",
                classes="modal-hint",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        token = self.query_one("#token", Input).value.strip()
        email = self.query_one("#email", Input).value.strip() or None
        slot_raw = self.query_one("#slot", Input).value.strip()
        if not token:
            self.query_one("#form-error", Static).update("Token is required.")
            return
        slot: int | None = None
        if slot_raw:
            try:
                slot = int(slot_raw)
            except ValueError:
                self.query_one("#form-error", Static).update("Slot must be a number.")
                return
            if slot < 1:
                self.query_one("#form-error", Static).update("Slot must be >= 1.")
                return
        self.dismiss(TokenForm(token=token, email=email, slot=slot))

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddAccountModal(ModalScreen[bool]):
    """Confirm adding a new account via claude auth login.

    Suspends the TUI, runs ``claude auth logout`` → ``claude auth login``
    → ``g2swap add`` → ``g2swap switch <slot>`` to restore the original login.
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=False),
        Binding("n,escape", "cancel", "No", show=False),
        Binding("left", "app.focus_previous", show=False),
        Binding("right", "app.focus_next", show=False),
    ]

    def __init__(self, current_email: str, current_slot: str) -> None:
        super().__init__()
        self._current_email = current_email
        self._current_slot = current_slot

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Label("Add account", classes="modal-title")
            yield Static(
                "This will log you out temporarily to add a new account.\n\n"
                f"Restore to slot {self._current_slot}  {self._current_email} afterwards?",
                classes="modal-body",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Add account", id="yes")
                yield Button("Cancel", id="no")
            yield Static(
                "y add account  ·  n / esc cancel",
                classes="modal-hint",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class OutputModal(ModalScreen[None]):
    """Scrollable display of captured (ANSI-colored) action output."""

    BINDINGS = [Binding("escape,q,enter", "dismiss_modal", "Close", show=False)]

    def __init__(self, title: str, output: str) -> None:
        super().__init__()
        self._title = title
        self._output = output

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box modal-box-wide"):
            yield Label(self._title, classes="modal-title")
            with VerticalScroll(classes="modal-output"):
                yield Static(Text.from_ansi(self._output.rstrip() or "(no output)"))
            with Horizontal(classes="modal-buttons"):
                yield Button("Close", id="close")
            yield Static("esc close", classes="modal-hint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class _EditPriorityModal(ModalScreen["tuple[str, int] | None"]):
    """Simple integer input for editing one account's priority number."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("left", "app.focus_previous", show=False),
        Binding("right", "app.focus_next", show=False),
    ]

    def __init__(self, number: str, current: object) -> None:
        super().__init__()
        self._number = number
        self._current = str(current) if current is not None else ""

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Label(f"Priority for account {self._number}", classes="modal-title")
            yield Input(
                value=self._current,
                placeholder="priority number",
                id="priority-input",
                type="integer",
            )
            yield Static("", id="edit-error", classes="form-error")
            with Horizontal(classes="modal-buttons"):
                yield Button("Save", id="save")
                yield Button("Cancel", id="cancel")
            yield Static("enter save  ·  esc cancel", classes="modal-hint")

    def on_mount(self) -> None:
        self.query_one("#priority-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        raw = self.query_one("#priority-input", Input).value.strip()
        if not raw:
            self.query_one("#edit-error", Static).update("Priority is required.")
            return
        try:
            val = int(raw)
        except ValueError:
            self.query_one("#edit-error", Static).update("Must be a number.")
            return
        self.dismiss((self._number, val))

    def action_cancel(self) -> None:
        self.dismiss(None)


class PriorityModal(ModalScreen[None]):
    """List all accounts with plan type detection and priority editing.

    Arrow keys / j-k navigate. ``e`` edits the highlighted account's priority.
    ``r`` re-runs smart auto-detection on all accounts. Esc / q closes.
    """

    BINDINGS = [
        Binding("escape,q", "dismiss_modal", "Close", show=False),
        Binding("e", "edit_priority", "Edit"),
        Binding("r", "auto_detect", "Auto-detect"),
        Binding("up,k", "cursor_up", show=False),
        Binding("down,j", "cursor_down", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._accounts: list[dict] = []
        self._cursor: int = 0

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box modal-box-wide"):
            yield Label("Account Priorities", classes="modal-title")
            with VerticalScroll(classes="modal-output"):
                yield Static("Loading…", id="priority-body")
            yield Static("", id="priority-status")
            with Horizontal(classes="modal-buttons"):
                yield Button("Auto-detect all", id="auto")
                yield Button("Close", id="close")
            yield Static(
                "↑/↓ navigate  ·  e edit  ·  r auto-detect  ·  esc close",
                classes="modal-hint",
            )

    def on_mount(self) -> None:
        self.run_worker(self._load_accounts_async, exclusive=True)

    async def _load_accounts_async(self) -> None:
        import asyncio
        import shutil

        cswap_bin = shutil.which("g2swap") or "g2swap"
        try:
            proc = await asyncio.create_subprocess_exec(
                cswap_bin,
                "list",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            data = json.loads(stdout.decode())
            self._accounts = data.get("accounts", [])
        except Exception:
            self._accounts = []
        self._render_list()

    def _load_accounts(self) -> None:
        """Sync fallback used after mutations (priority save)."""
        import shutil

        cswap_bin = shutil.which("g2swap") or "g2swap"
        result = subprocess.run(
            [cswap_bin, "list", "--json"], capture_output=True, text=True, timeout=10
        )
        try:
            data = json.loads(result.stdout)
            self._accounts = data.get("accounts", [])
        except Exception:
            self._accounts = []
        self._render_list()

    @staticmethod
    def _plan_type(acc: dict) -> str:
        usage = acc.get("usage", {})
        scoped = usage.get("scoped", [])
        five_h = usage.get("fiveHour")
        has_premium = any(s.get("name") in ("Fable", "Opus") for s in scoped)
        has_5h = five_h is not None
        if has_premium and not has_5h:
            return "Max 20x"
        if has_premium and has_5h:
            return "Max 5x"
        if not has_premium and has_5h:
            return "Pro"
        return "Uncertain"

    def _render_list(self) -> None:
        if not self._accounts:
            self.query_one("#priority-body", Static).update("No accounts found.")
            return
        lines = []
        for i, acc in enumerate(self._accounts):
            marker = "▶" if i == self._cursor else " "
            n = acc.get("number", "?")
            email = acc.get("email", "?")
            priority = acc.get("priority", "—")
            plan = self._plan_type(acc)
            lines.append(f"{marker} [{n}]  {email}  plan: {plan}  priority: {priority}")
        self.query_one("#priority-body", Static).update("\n".join(lines))

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._render_list()

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._accounts) - 1:
            self._cursor += 1
            self._render_list()

    def action_edit_priority(self) -> None:
        if not self._accounts:
            return
        acc = self._accounts[self._cursor]
        self.app.push_screen(
            _EditPriorityModal(str(acc.get("number", "?")), acc.get("priority")),
            self._on_priority_saved,
        )

    def _on_priority_saved(self, result: "tuple[str, int] | None") -> None:
        if result is None:
            return
        import shutil

        cswap_bin = shutil.which("g2swap") or "g2swap"
        number, priority = result
        subprocess.run([cswap_bin, "priority", str(number), str(priority)], capture_output=True)
        self._load_accounts()

    def action_auto_detect(self) -> None:
        import shutil

        cswap_bin = shutil.which("g2swap") or "g2swap"
        result = subprocess.run(
            [cswap_bin, "list", "--json"], capture_output=True, text=True, timeout=10
        )
        try:
            data = json.loads(result.stdout)
            accounts = data.get("accounts", [])
        except Exception:
            accounts = []
        for acc in accounts:
            n = acc.get("number")
            if n is None:
                continue
            usage = acc.get("usage", {})
            scoped = usage.get("scoped", [])
            five_h = usage.get("fiveHour")
            has_premium = any(s.get("name") in ("Fable", "Opus") for s in scoped)
            has_5h = five_h is not None
            if has_premium and not has_5h:
                pri = 1
            elif has_premium and has_5h:
                pri = 2
            elif not has_premium and has_5h:
                pri = 10
            else:
                pri = 3
            subprocess.run([cswap_bin, "priority", str(n), str(pri)], capture_output=True)
        subprocess.run(
            [cswap_bin, "config", "set", "autoswitch.strategy", "priority-consume-first"],
            capture_output=True,
        )
        subprocess.run(
            [cswap_bin, "config", "set", "autoswitch.weeklyThreshold", "97"],
            capture_output=True,
        )
        self._load_accounts()
        self.query_one("#priority-status", Static).update(
            "Auto-detection done — strategy set to priority-consume-first."
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "auto":
            self.action_auto_detect()
            return
        self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)
