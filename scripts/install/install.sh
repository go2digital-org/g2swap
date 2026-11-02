#!/usr/bin/env bash
# g2swap install script — macOS, Linux, WSL
# Usage: bash scripts/install/install.sh [--repo <git-url>] [--no-daemon] [--no-alias] [--alias=NAME]
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/go2digital-org/g2swap}"
DEFAULT_DIR="$HOME/projects/g2swap"
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
if [[ -f "$SCRIPT_PATH" ]]; then
  SCRIPT_ROOT="$(cd "$(dirname "$SCRIPT_PATH")/../.." && pwd)"
  [[ -d "$SCRIPT_ROOT/.git" && -f "$SCRIPT_ROOT/src/claude_swap/cli.py" ]] && DEFAULT_DIR="$SCRIPT_ROOT"
fi
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_DIR}"
NO_DAEMON=0
NO_ALIAS=0
ALIAS_NAME=""

for arg in "$@"; do
  case "$arg" in
    --no-daemon) NO_DAEMON=1 ;;
    --no-alias)  NO_ALIAS=1  ;;
    --repo=*)    REPO_URL="${arg#--repo=}" ;;
    --alias=*)   ALIAS_NAME="${arg#--alias=}" ;;
  esac
done

# ── Choose the shell command name ──────────────────────────────────────────────
HAVE_TTY=0
{ : </dev/tty; } 2>/dev/null && HAVE_TTY=1
if [[ $NO_ALIAS -eq 0 && -z "$ALIAS_NAME" && $HAVE_TTY -eq 1 ]]; then
  read -r -p "  Command name for the dashboard [g2swap]: " ALIAS_NAME </dev/tty
fi
ALIAS_NAME="${ALIAS_NAME:-g2swap}"
if [[ ! "$ALIAS_NAME" =~ ^[A-Za-z][A-Za-z0-9_-]*$ ]]; then
  echo "  Invalid command name '$ALIAS_NAME': use letters, digits, '-' or '_', starting with a letter." >&2
  exit 1
fi

# ── Detect OS ──────────────────────────────────────────────────────────────────
OS="unknown"
case "$(uname -s)" in
  Darwin) OS="macos" ;;
  Linux)
    OS="linux"
    grep -qi microsoft /proc/version 2>/dev/null && OS="wsl"
    ;;
  MINGW*|MSYS*|CYGWIN*) OS="windows-bash" ;;
esac

echo ""
echo "  g2swap installer"
echo "  OS: $OS"
echo ""

# ── 1. Install uv if missing ───────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
  echo "  Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
echo "  uv: $(uv --version)"

# ── 2. Clone or update repo ────────────────────────────────────────────────────
if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo "  Updating existing repo at $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --ff-only
fi
if [[ ! -d "$INSTALL_DIR/.git" ]]; then
  echo "  Cloning $REPO_URL → $INSTALL_DIR..."
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

# ── 3. Install g2swap globally via uv tool ─────────────────────────────────────
echo "  Installing g2swap..."
uv tool install "$INSTALL_DIR" --reinstall
export PATH="$HOME/.local/bin:$PATH"
echo "  g2swap: $(g2swap --version)"

# ── 4. Set up auto-daemon ──────────────────────────────────────────────────────
if [[ $NO_DAEMON -eq 0 ]]; then
  LOG_DIR="$HOME/.local/share/claude-swap"
  mkdir -p "$LOG_DIR"

  if [[ "$OS" == "macos" ]]; then
    PLIST="$HOME/Library/LaunchAgents/g2swap.auto.plist"
    CSWAP_BIN="$(command -v g2swap)"
    cat > "$PLIST" << PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>g2swap.auto</string>
    <key>ProgramArguments</key>
    <array>
        <string>${CSWAP_BIN}</string>
        <string>auto</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/auto.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/auto.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST_EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "  Auto-daemon: launchd agent loaded (starts at login)"
    WD_PLIST="$HOME/Library/LaunchAgents/g2swap.watchdog.plist"
    cat > "$WD_PLIST" << WD_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>g2swap.watchdog</string>
    <key>ProgramArguments</key>
    <array>
        <string>${CSWAP_BIN}</string>
        <string>watchdog</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/watchdog.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/watchdog.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
WD_EOF
    launchctl unload "$WD_PLIST" 2>/dev/null || true
    launchctl load "$WD_PLIST"
    echo "  Watchdog: launchd agent loaded (starts at login)"

  elif [[ "$OS" == "linux" || "$OS" == "wsl" ]]; then
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SYSTEMD_DIR"
    # Services were named cswap-* before the rename; remove them so only one set runs.
    for old in cswap-auto cswap-watchdog; do
      systemctl --user disable --now "$old.service" &>/dev/null || true
      rm -f "$SYSTEMD_DIR/$old.service"
    done
    CSWAP_BIN="$(command -v g2swap)"
    cat > "$SYSTEMD_DIR/g2swap-auto.service" << SVC_EOF
[Unit]
Description=g2swap auto account switcher
After=network.target

[Service]
Type=simple
ExecStart=${CSWAP_BIN} auto
Restart=always
RestartSec=5
StandardOutput=append:${LOG_DIR}/auto.log
StandardError=append:${LOG_DIR}/auto.log
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
SVC_EOF
    cat > "$SYSTEMD_DIR/g2swap-watchdog.service" << WDSVC_EOF
[Unit]
Description=g2swap hard-limit watchdog
After=network.target

[Service]
Type=simple
ExecStart=${CSWAP_BIN} watchdog
Restart=always
RestartSec=5
StandardOutput=append:${LOG_DIR}/watchdog.log
StandardError=append:${LOG_DIR}/watchdog.log
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
WDSVC_EOF
    HAVE_SYSTEMD=0
    command -v systemctl &>/dev/null && systemctl --user status &>/dev/null 2>&1 && HAVE_SYSTEMD=1
    if [[ $HAVE_SYSTEMD -eq 1 ]]; then
      systemctl --user daemon-reload
      systemctl --user enable --now g2swap-auto.service g2swap-watchdog.service
      echo "  Auto-daemon and watchdog: systemd user services enabled"
    fi
    if [[ $HAVE_SYSTEMD -eq 0 ]]; then
      echo "  Service files written to $SYSTEMD_DIR (g2swap-auto, g2swap-watchdog)"
      echo "  (systemd not available in this environment — start manually: g2swap auto & g2swap watchdog &)"
    fi
  fi
fi

# ── Weekly line: keep the 5-hour threshold at its default, let weekly windows run higher
g2swap config set autoswitch.weeklyThreshold 97 &>/dev/null || true

# ── 5. Add the shell command to the shell profile ───────────────────────────────────────
if [[ $NO_ALIAS -eq 0 ]]; then
  SHELL_RC=""
  case "${SHELL:-}" in
    */zsh)  [[ -f "$HOME/.zshrc" ]]  && SHELL_RC="$HOME/.zshrc" ;;
    */bash) [[ -f "$HOME/.bashrc" ]] && SHELL_RC="$HOME/.bashrc" ;;
  esac
  [[ -z "$SHELL_RC" && -f "$HOME/.zshrc" ]]  && SHELL_RC="$HOME/.zshrc"
  [[ -z "$SHELL_RC" && -f "$HOME/.bashrc" ]] && SHELL_RC="$HOME/.bashrc"
  [[ -z "$SHELL_RC" && -f "$HOME/.profile" ]] && SHELL_RC="$HOME/.profile"

  HAS_ALIAS=0
  [[ -n "$SHELL_RC" ]] && grep -qF "# ── $ALIAS_NAME: Claude Code account manager" "$SHELL_RC" 2>/dev/null && HAS_ALIAS=1
  if [[ -n "$SHELL_RC" && $HAS_ALIAS -eq 0 ]]; then
    sed "s/__ALIAS__/$ALIAS_NAME/g" >> "$SHELL_RC" << 'ALIAS_EOF'

# ── __ALIAS__: Claude Code account manager ───────────────────────────────────────
__ALIAS__() {
  if [[ "$1" == "auto" ]]; then
    local mode="${2:-toggle}" running=0
    if [[ "$(uname -s)" == "Darwin" ]]; then
      local plist="$HOME/Library/LaunchAgents/g2swap.auto.plist"
      launchctl list 2>/dev/null | grep -q 'g2swap.auto' && running=1
      [[ "$mode" == "toggle" && $running -eq 1 ]] && mode="off"
      [[ "$mode" == "toggle" ]] && mode="on"
      [[ "$mode" == "off" ]] && launchctl unload "$plist" 2>/dev/null && echo "  Auto-switch OFF (manual mode)"
      [[ "$mode" == "on" ]] && launchctl load "$plist" 2>/dev/null && echo "  Auto-switch ON"
      return 0
    fi
    systemctl --user is-active --quiet g2swap-auto.service && running=1
    [[ "$mode" == "toggle" && $running -eq 1 ]] && mode="off"
    [[ "$mode" == "toggle" ]] && mode="on"
    [[ "$mode" == "off" ]] && systemctl --user stop g2swap-auto.service && echo "  Auto-switch OFF (manual mode)"
    [[ "$mode" == "on" ]] && systemctl --user start g2swap-auto.service && echo "  Auto-switch ON"
    return 0
  fi
  if [[ "$1" == "add-account" ]]; then
    local original_email original_slot
    original_email=$(g2swap status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('active',{}).get('email',''))" 2>/dev/null)
    original_slot=$(g2swap status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('active',{}).get('number',''))" 2>/dev/null)
    echo "" && echo "  Add new Claude account" && echo "  Current: $original_email"
    echo "" && echo "  This will log you out temporarily, open a browser, then restore $original_email."
    echo "" && printf '  Proceed? (y/N): '; read -r confirm
    [[ "$confirm" != [yY] ]] && echo "  Cancelled." && return 0
    claude auth logout &>/dev/null
    claude auth login
    g2swap add
    g2swap switch "$original_slot" &>/dev/null
    echo "  Done. Back on $original_email."
    return 0
  fi
  if [[ "$1" == "smart-priority" ]]; then
    printf '\n  Detecting plan types...\n\n'
    g2swap list --json 2>/dev/null | python3 -c "
import sys, json, subprocess
data = json.load(sys.stdin)
for acc in data['accounts']:
    n = acc['number']
    email = acc['email']
    usage = acc.get('usage', {})
    scoped = usage.get('scoped', [])
    five_h = usage.get('fiveHour')
    has_premium = any(s.get('name') in ('Fable','Opus') for s in scoped)
    has_5h = five_h is not None
    plan, pri = {
        (True, False): ('Max 20x', 1),
        (True, True): ('Max 5x', 2),
        (False, True): ('Pro', 10),
    }.get((has_premium, has_5h), ('Uncertain (treating as Max)', 3))
    subprocess.run(['g2swap', 'priority', str(n), str(pri)], capture_output=True)
    print(f'  Slot {n}: {email}  =>  {plan}  (priority {pri})')
"
    g2swap config set autoswitch.strategy priority-consume-first &>/dev/null
    g2swap config set autoswitch.weeklyThreshold 97 &>/dev/null
    printf '\n  Strategy: priority-consume-first\n'
    return 0
  fi
  local before after
  before=$(g2swap status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('active',{}).get('email',''))" 2>/dev/null)
  [[ $# -eq 0 ]] && g2swap tui || g2swap "$@"
  after=$(g2swap status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('active',{}).get('email',''))" 2>/dev/null)
  if [[ -n "$before" && -n "$after" && "$before" != "$after" ]]; then
    echo "" && echo "  Switched $before -> $after"
    echo "  Reconnecting Remote Control..."
    claude --dangerously-skip-permissions /remote-control &>/dev/null &
    echo "  Done."
  fi
}
ALIAS_EOF
    echo "  Shell command: $ALIAS_NAME added to $SHELL_RC"
  fi
  if [[ $HAS_ALIAS -eq 1 ]]; then
    echo "  Shell command: already present in $SHELL_RC (skipped)"
  fi
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "  Installation complete."
echo ""
echo "  Open a new terminal, then:"
echo "    $ALIAS_NAME                  — open TUI"
echo "    $ALIAS_NAME add-account      — register a new Claude account"
echo "    $ALIAS_NAME smart-priority   — auto-detect Pro vs Max, set priority"
echo "    $ALIAS_NAME list             — show all accounts + usage"
echo ""
