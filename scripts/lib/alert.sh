#!/usr/bin/env bash
# Shared out-of-band alert channels for ops failures. Sourced by the ops
# scripts (ops-audit.sh) — set REPO before sourcing.
#
# Channels (all fail-soft — a missing/broken channel is logged, never fatal,
# because alerting must not mask the failure it is reporting):
#   1. macOS notification  (osascript, if present)
#   2. Email               (MSB_ALERT_EMAIL + /usr/bin/mail, if both present)
#   3. Telegram            (MSB_TELEGRAM_BOT_TOKEN + MSB_TELEGRAM_CHAT_ID,
#                           via MSB_TELEGRAM_API, default api.telegram.org)
#
# Every channel attempt is logged to MSB_ALERT_LOG (default $REPO/logs/ops-alerts.log).

ALERT_LOG="${MSB_ALERT_LOG:-$REPO/logs/ops-alerts.log}"
mkdir -p "$(dirname "$ALERT_LOG")"

alert_log() { echo "[alert] $(date '+%F %T') $*" >> "$ALERT_LOG"; }

notify_macos() { # title msg
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"$2\" with title \"$1\"" >/dev/null 2>&1 \
      && alert_log "macos notification sent: $1" \
      || alert_log "macos notification FAILED: $1"
  else
    alert_log "macos notification skipped (osascript not found)"
  fi
}

notify_email() { # subject body
  if [ -n "${MSB_ALERT_EMAIL:-}" ] && command -v mail >/dev/null 2>&1; then
    printf '%s\n' "$2" | mail -s "$1" "$MSB_ALERT_EMAIL" >/dev/null 2>&1 \
      && alert_log "email sent to $MSB_ALERT_EMAIL: $1" \
      || alert_log "email FAILED to $MSB_ALERT_EMAIL: $1"
  else
    alert_log "email skipped (MSB_ALERT_EMAIL=${MSB_ALERT_EMAIL:-unset}, mail=$(command -v mail 2>/dev/null || echo absent))"
  fi
}

notify_telegram() { # subject body
  if [ -n "${MSB_TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${MSB_TELEGRAM_CHAT_ID:-}" ]; then
    local api="${MSB_TELEGRAM_API:-https://api.telegram.org}"
    local text="$1 — $(date '+%F %T')%0A$2"
    curl -s --max-time 10 -X POST "$api/bot${MSB_TELEGRAM_BOT_TOKEN}/sendMessage" \
      -d "chat_id=${MSB_TELEGRAM_CHAT_ID}" --data-urlencode "text=$text" >/dev/null 2>&1 \
      && alert_log "telegram sent (chat ${MSB_TELEGRAM_CHAT_ID}): $1" \
      || alert_log "telegram FAILED (chat ${MSB_TELEGRAM_CHAT_ID}): $1"
  else
    local tok chat
    [ -n "${MSB_TELEGRAM_BOT_TOKEN:-}" ] && tok=set || tok=unset
    [ -n "${MSB_TELEGRAM_CHAT_ID:-}" ] && chat=set || chat=unset
    alert_log "telegram skipped (token=$tok chat=$chat)"
  fi
}

# Fire every configured channel for one incident. Always returns 0.
notify_all() { # title body
  notify_macos "$1" "$2"
  notify_email "$1" "$2"
  notify_telegram "$1" "$2"
  return 0
}
