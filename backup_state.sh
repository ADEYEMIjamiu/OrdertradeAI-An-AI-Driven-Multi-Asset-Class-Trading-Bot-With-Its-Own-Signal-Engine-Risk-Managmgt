#!/bin/bash
# Daily backup of trade history + trailing-stop/rotation state, so a bad
# deploy or accidental file change can't wipe them. Keeps the last 14
# days locally on the droplet.
#
# This does NOT protect against losing the droplet itself (disk
# failure, accidental droplet deletion, etc.) -- for that, use
# DigitalOcean's own "Backups" feature in the control panel (weekly
# whole-server snapshots, small monthly fee, no code needed). This
# script only protects the specific state files this app depends on
# from a local mistake.
#
# Scheduled via cron daily at 03:00 UTC:
#   0 3 * * * /root/AI-Trading-Bot/backup_state.sh >> /root/AI-Trading-Bot/backup.log 2>&1

BACKUP_DIR="/root/AI-Trading-Bot/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

cd /root/AI-Trading-Bot

# trade_journal.db -- shared by trade_journal.py and engines/order_manager.py
# local_account.db -- account_store.py's persistent cash/positions
# etoro_position_state.json -- etoro_broker.py's closed-position snapshot
# alpaca_highest_profit_state.json -- app.py's trailing-profit-lock state
for FILE in trade_journal.db local_account.db etoro_position_state.json alpaca_highest_profit_state.json; do
    if [ -f "$FILE" ]; then
        cp "$FILE" "$BACKUP_DIR/${FILE}.${TIMESTAMP}.bak"
    fi
done

find "$BACKUP_DIR" -type f -mtime +14 -delete
