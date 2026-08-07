"""
Equity Tracker -- durable, timestamped log of total portfolio value, used
to compute maximum drawdown for the Real-Money Readiness Scorecard.

Built 2026-08-07 because nothing in this project previously recorded
equity over time anywhere durable: st.session_state.equity_history exists
but resets to empty on every service restart (same class of bug already
fixed for highest_profit and eToro's position snapshot), and there was no
on-disk equity history at all. Win rate and profit factor alone don't
tell you whether the account ever took a scary dip along the way --
drawdown is the piece that answers "could I have stomached this with
real money," so it needed its own persistent store.

Uses a plain SQLite file (equity_curve.db), matching the pattern already
used by trade_journal.db and local_account.db elsewhere in this project.
"""

import sqlite3
from datetime import datetime

_DB_NAME = "equity_curve.db"

# Logging on every Streamlit rerun (which can happen every few seconds
# with autorefresh on) would write far more rows than a drawdown
# calculation needs and bloat the file for no benefit. A snapshot every
# 15 minutes is more than enough resolution to catch a real drawdown
# while keeping the table small.
_MIN_INTERVAL_MINUTES = 15


def _get_connection():
    conn = sqlite3.connect(_DB_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS equity_snapshots (
            timestamp TEXT PRIMARY KEY,
            value REAL
        )
    """)
    return conn


def log_equity_snapshot(value):
    """
    Records the current total portfolio value, throttled to at most once
    every _MIN_INTERVAL_MINUTES. Safe to call on every script run --
    the throttle check makes repeated calls within the window a no-op.
    Never raises: a logging failure must not be able to affect trading.
    """
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT timestamp FROM equity_snapshots ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

        now = datetime.now()

        if row is not None:
            try:
                last_logged = datetime.fromisoformat(row[0])
                elapsed_minutes = (now - last_logged).total_seconds() / 60
                if elapsed_minutes < _MIN_INTERVAL_MINUTES:
                    conn.close()
                    return
            except Exception:
                pass  # malformed row -- fall through and log anyway

        conn.execute(
            "INSERT OR REPLACE INTO equity_snapshots (timestamp, value) VALUES (?, ?)",
            (now.isoformat(timespec="seconds"), float(value)),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[equity_tracker] could not log equity snapshot: {e}")


def get_equity_history(since=None):
    """
    Returns [(datetime, value), ...] ordered oldest-first, optionally
    filtered to snapshots at or after `since`.
    """
    try:
        conn = _get_connection()
        rows = conn.execute(
            "SELECT timestamp, value FROM equity_snapshots ORDER BY timestamp ASC"
        ).fetchall()
        conn.close()
    except Exception:
        return []

    history = []
    for timestamp_text, value in rows:
        try:
            ts = datetime.fromisoformat(timestamp_text)
        except Exception:
            continue
        if since is not None and ts < since:
            continue
        history.append((ts, value))

    return history


def get_max_drawdown_percent(since=None):
    """
    Largest peak-to-trough decline in the logged equity curve, as a
    percentage. Returns None if there isn't enough history yet (fewer
    than 2 snapshots) rather than a misleading 0%.
    """
    history = get_equity_history(since=since)

    if len(history) < 2:
        return None

    peak = history[0][1]
    max_drawdown = 0.0

    for _, value in history:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = ((peak - value) / peak) * 100
            max_drawdown = max(max_drawdown, drawdown)

    return max_drawdown
