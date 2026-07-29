"""
Persistent, shared local paper-trading account state.

Previously cash/positions/equity_history/AUTO_TRADING/last_trade_time all
lived only in st.session_state, which Streamlit scopes per browser
session -- every new tab or device that opened the dashboard started an
entirely separate, independent paper account from scratch ($100k, no
positions, Auto-Trading off), instead of showing the same live account
already running elsewhere. That's fine for a quick local test, but not
for something meant to be trusted with real decisions: your phone should
show the same account your laptop is trading on.

This persists that state to a local SQLite file (like trade_journal.db
already does for trade history) so every session reads the same shared
account at startup, and writes back to it after every mutation.

Important limitation, by design: this is last-write-wins, not a
transaction-safe multi-writer system. Two devices both issuing trades in
the exact same instant could still race and one could clobber the
other's change. That's an acceptable trade-off for one person occasionally
checking in from a second device while a single "driver" session (the one
with Auto-Trading actually running) does the real work -- it is NOT a
substitute for a real multi-user backend, which would need proper
transactions/locking and is out of scope until this becomes a multi-user
product.
"""
import json
import os
import sqlite3
from datetime import datetime

_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "local_account.db"
)


def _json_default(obj):
    # pandas/numpy scalar types (float64, int64, bool_, ...) all expose
    # .item() to convert to a native Python type -- position/price values
    # flow in from DataFrame rows via row.to_dict(), so this is common,
    # not an edge case. datetime is also handled defensively even though
    # last_trade_time is serialized separately below.
    if hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _get_conn():
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema():
    conn = _get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS account_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cash REAL NOT NULL,
                positions TEXT NOT NULL,
                equity_history TEXT NOT NULL,
                auto_trading INTEGER NOT NULL,
                last_trade_time TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def load_account(default_cash):
    """
    Returns {cash, positions, equity_history, auto_trading,
    last_trade_time}. If no account has ever been saved (very first run
    on a brand new install), returns a fresh default account without
    writing anything -- the first save_account() call creates the row.
    """
    _ensure_schema()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT cash, positions, equity_history, auto_trading, "
            "last_trade_time FROM account_state WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return {
            "cash": default_cash,
            "positions": {},
            "equity_history": [],
            "auto_trading": False,
            "last_trade_time": {},
        }

    cash, positions_json, equity_json, auto_trading, last_trade_json = row
    return {
        "cash": cash,
        "positions": json.loads(positions_json),
        "equity_history": json.loads(equity_json),
        "auto_trading": bool(auto_trading),
        "last_trade_time": {
            ticker: datetime.fromisoformat(iso)
            for ticker, iso in json.loads(last_trade_json).items()
        },
    }


def save_account(cash, positions, equity_history, auto_trading, last_trade_time):
    """
    Write-through save. Called after every mutation checkpoint in app.py
    (trade execution, reset, Auto-Trading toggle) so any other session
    that loads afterward sees this immediately.
    """
    _ensure_schema()

    last_trade_serializable = {
        ticker: (dt.isoformat() if isinstance(dt, datetime) else dt)
        for ticker, dt in last_trade_time.items()
    }

    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO account_state
                (id, cash, positions, equity_history, auto_trading,
                 last_trade_time, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                cash=excluded.cash,
                positions=excluded.positions,
                equity_history=excluded.equity_history,
                auto_trading=excluded.auto_trading,
                last_trade_time=excluded.last_trade_time,
                updated_at=excluded.updated_at
            """,
            (
                float(cash),
                json.dumps(positions, default=_json_default),
                json.dumps(equity_history, default=_json_default),
                int(bool(auto_trading)),
                json.dumps(last_trade_serializable, default=_json_default),
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def reset_account(starting_cash):
    save_account(
        cash=starting_cash,
        positions={},
        equity_history=[],
        auto_trading=False,
        last_trade_time={},
    )
