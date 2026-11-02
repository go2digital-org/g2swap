#!/usr/bin/env bash
# g2swap session watchdog
# Watches Claude Code log/process for rate-limit events, switches account + notifies.
# Run in background: bash scripts/watchdog/session-watchdog.sh &
# Or wire into launchd alongside cswap-auto.

POLL_INTERVAL=10   # check every 10 seconds
LOG="$HOME/.local/share/claude-swap/watchdog.log"
CSWAP="$(command -v cswap)"

log() { echo "$(date '+%H:%M:%S')  $*" | tee -a "$LOG"; }

# Claude Code stores session logs here
CLAUDE_LOG_DIR="$HOME/.claude/logs"

last_switch=0

log "Watchdog started (poll ${POLL_INTERVAL}s)"

while true; do
  sleep "$POLL_INTERVAL"

  now=$(date +%s)
  cooldown=120  # don't switch more than once per 2 minutes

  # ── Check 1: cswap status shows current account is rate-limited ────────────
  status_json=$("$CSWAP" status --json 2>/dev/null)
  usage_status=$(echo "$status_json" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('active',{}).get('usageStatus','ok'))" 2>/dev/null)

  if [[ "$usage_status" == "rate_limited" || "$usage_status" == "exhausted" ]]; then
    if (( now - last_switch > cooldown )); then
      account=$(echo "$status_json" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('active',{}).get('email',''))" 2>/dev/null)
      log "Rate-limit detected on $account — switching..."
      "$CSWAP" switch --strategy next-available &>/dev/null
      new_account=$("$CSWAP" status --json 2>/dev/null | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('active',{}).get('email',''))" 2>/dev/null)
      log "Switched to $new_account"
      last_switch=$now

      # macOS notification
      if command -v osascript &>/dev/null; then
        osascript -e "display notification \"Switched to $new_account — open a new Claude session\" \
          with title \"g2swap\" sound name \"Ping\""
      fi
    fi
    continue
  fi

  # ── Check 2: scan recent Claude logs for rate-limit strings ───────────────
  if [[ -d "$CLAUDE_LOG_DIR" ]]; then
    recent_log=$(ls -t "$CLAUDE_LOG_DIR"/*.log 2>/dev/null | head -1)
    if [[ -n "$recent_log" ]]; then
      # Look for rate-limit markers written in the last 60 seconds
      if find "$recent_log" -newer <(date -v-60S +%s 2>/dev/null || date -d '60 seconds ago' 2>/dev/null) \
           2>/dev/null | grep -q .; then
        if grep -qiE 'rate.limit|usage.limit|quota.exceeded|5.hour.limit|7.day.limit' \
             <(tail -50 "$recent_log") 2>/dev/null; then
          if (( now - last_switch > cooldown )); then
            log "Rate-limit string found in Claude log — switching..."
            "$CSWAP" switch --strategy next-available &>/dev/null
            new_account=$("$CSWAP" status --json 2>/dev/null | python3 -c \
              "import sys,json; d=json.load(sys.stdin); print(d.get('active',{}).get('email',''))" 2>/dev/null)
            log "Switched to $new_account"
            last_switch=$now
            if command -v osascript &>/dev/null; then
              osascript -e "display notification \"Switched to $new_account — open a new Claude session\" \
                with title \"g2swap\" sound name \"Ping\""
            fi
          fi
        fi
      fi
    fi
  fi
done
