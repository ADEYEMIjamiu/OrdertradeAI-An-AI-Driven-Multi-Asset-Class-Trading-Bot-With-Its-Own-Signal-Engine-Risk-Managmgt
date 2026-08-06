#!/bin/bash
# Pings the local dashboard and alerts via Telegram (+ self-heals via
# systemctl restart) if it's not responding. Added 2026-08-06 alongside
# SSH hardening, the risk_engine.py fix, and backup_state.sh, as part of
# a round of operational hardening for the droplet.
#
# This is deliberately separate from systemd's Restart=always on
# ordertrade-ai.service: that already handles the process actually
# crashing/exiting. What it CANNOT catch is the process still being
# alive but hung/unresponsive (e.g. deadlocked, stuck on a slow network
# call) -- from systemd's point of view a hung process looks identical
# to a healthy one, since it never exits. This script is what notices
# that case, since it checks actual HTTP responsiveness, not just
# "is the process alive."
#
# Scheduled via cron every 5 minutes:
#   */5 * * * * /root/AI-Trading-Bot/health_check.sh >> /root/AI-Trading-Bot/health_check.log 2>&1

set -a
source /root/AI-Trading-Bot/.env 2>/dev/null
set +a

URL="http://127.0.0.1:8501"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$URL")

if [ "$STATUS" != "200" ]; then
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d text="OrderTrade AI dashboard is not responding (HTTP ${STATUS}). Restarting it now." \
            > /dev/null
    fi
    systemctl restart ordertrade-ai
fi
