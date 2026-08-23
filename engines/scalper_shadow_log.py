"""
Persisted log for crypto_scalping_engine.py's shadow-mode decisions --
what the 60-second Binance-native loop WOULD have traded, without
actually placing any order. Exists so the shadow trial period has
something concrete to review afterward (compare against what the
existing 5-minute yfinance-based dashboard loop actually did), rather
than only ever being visible in scrolling console/journalctl output.

Plain SQLite file, matching the project's established pattern
(trade_journal.db, equity_curve.db, asset_toggles.db, etc.).
"""

import sqlite3
from datetime import datetime

_DB_NAME = "crypto_scalper_shadow.db"


def _get_connection():
    conn = sqlite3.connect(_DB_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            ticker TEXT,
            price REAL,
            confidence REAL,
            signal TEXT,
            data_source TEXT
        )
    """)
    return conn


def log_decision(ticker, price, confidence, signal, data_source):
    """Never raises -- a logging failure must not be able to crash the loop."""
    try:
        conn = _get_connection()
        conn.execute(
            "INSERT INTO shadow_decisions "
            "(timestamp, ticker, price, confidence, signal, data_source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                ticker,
                price,
                confidence,
                signal,
                data_source,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[scalper_shadow_log] could not log decision for {ticker}: {e}")


def get_recent_decisions(limit=100):
    """Newest-first. Used for manual review during the shadow trial."""
    try:
        conn = _get_connection()
        rows = conn.execute(
            "SELECT timestamp, ticker, price, confidence, signal, data_source "
            "FROM shadow_decisions ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def get_signal_summary():
    """
    {signal: count} across the whole shadow log so far -- a quick sanity
    check during the trial (e.g. "is this loop mostly saying HOLD, or is
    it firing BUY/SELL constantly on noise").
    """
    try:
        conn = _get_connection()
        rows = conn.execute(
            "SELECT signal, COUNT(*) FROM shadow_decisions GROUP BY signal"
        ).fetchall()
        conn.close()
        return dict(rows)
    except Exception:
        return {}
