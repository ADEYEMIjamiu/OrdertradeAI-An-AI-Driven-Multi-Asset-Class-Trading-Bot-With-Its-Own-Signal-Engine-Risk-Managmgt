"""
Per-user order journal for the multi-tenant SaaS product.

Same lifecycle pattern as engines/order_manager.py (the single-owner
bot's order book: create_order() -> mark_order_*() -> save_order()),
but every order is tagged with a user_id and stored in a separate table
in saas_platform.db -- NOT trade_journal.db, so nothing here can ever
mix into or be confused with the single-owner bot's own order history.

The mark_order_*() status mutators (mark_order_submitted/filled/
rejected/failed/cancelled) and the ORDER_STATUS_* constants are pure
functions with no broker-specific or storage-specific behavior -- they
just mutate a plain dict in memory before save_order() persists it --
so they're imported and re-exported from order_manager.py unchanged
rather than duplicated. create_order()/save_order()/load_orders_*()
differ (user_id column, different table, different database) and are
reimplemented here.

WHY get_most_recent_filled_buy_for_user() scopes by user_id, not just
ticker+broker: if two different users both hold AAPL through their own
Alpaca accounts, a lookup that ignored user_id could return the WRONG
user's entry price for a stop-loss/take-profit calculation -- a real
correctness bug, not just a hygiene one, once a per-user risk-management
loop is built on top of this.
"""

from datetime import datetime
import json
import sqlite3
import uuid

# Re-exported unchanged -- see module docstring.
from engines.order_manager import (
    ORDER_STATUS_PENDING,
    ORDER_STATUS_SUBMITTED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_REJECTED,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_FAILED,
    mark_order_submitted,
    mark_order_filled,
    mark_order_rejected,
    mark_order_failed,
    mark_order_cancelled,
)

DB_NAME = "saas_platform.db"


def _get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS saas_orders (
            order_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT,
            ticker TEXT,
            side TEXT,
            quantity REAL,
            trade_amount REAL,
            price REAL,
            asset_class TEXT,
            broker TEXT,
            strategy TEXT,
            confidence REAL,
            ai_trade_score REAL,
            priority TEXT,
            stop_loss REAL,
            take_profit REAL,
            status TEXT,
            broker_order_id TEXT,
            filled_price REAL,
            filled_quantity REAL,
            error TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_saas_orders_user
        ON saas_orders(user_id)
    """)
    return conn


def create_order(
    user_id,
    ticker,
    side,
    quantity,
    trade_amount,
    price,
    asset_class,
    broker,
    strategy,
    confidence,
    ai_trade_score,
    priority,
    stop_loss=None,
    take_profit=None,
):
    """Creates a professional order object before execution. Mirrors
    order_manager.create_order(), plus a required user_id."""

    return {
        "order_id": str(uuid.uuid4()),
        "user_id": user_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ticker": ticker,
        "side": side,
        "quantity": round(float(quantity), 6),
        "trade_amount": round(float(trade_amount), 2),
        "price": round(float(price), 4),
        "asset_class": asset_class,
        "broker": broker,
        "strategy": strategy,
        "confidence": round(float(confidence), 2),
        "ai_trade_score": round(float(ai_trade_score), 2),
        "priority": priority,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "status": ORDER_STATUS_PENDING,
        "broker_order_id": None,
        "filled_price": None,
        "filled_quantity": 0,
        "error": None,
    }


def save_order(order):
    """Persist (insert or update) an order's current state. Same pattern
    as order_manager.save_order() -- call after create_order() and again
    after every mark_order_*() call."""

    conn = _get_connection()
    conn.execute("""
        INSERT INTO saas_orders (
            order_id, user_id, created_at, ticker, side, quantity, trade_amount,
            price, asset_class, broker, strategy, confidence,
            ai_trade_score, priority, stop_loss, take_profit, status,
            broker_order_id, filled_price, filled_quantity, error, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET
            status=excluded.status,
            broker_order_id=excluded.broker_order_id,
            filled_price=excluded.filled_price,
            filled_quantity=excluded.filled_quantity,
            error=excluded.error,
            updated_at=excluded.updated_at
    """, (
        order["order_id"],
        order["user_id"],
        order["created_at"],
        order["ticker"],
        order["side"],
        order["quantity"],
        order["trade_amount"],
        order["price"],
        order["asset_class"],
        order["broker"],
        order["strategy"],
        order["confidence"],
        order["ai_trade_score"],
        json.dumps(order.get("priority")),
        order.get("stop_loss"),
        order.get("take_profit"),
        order["status"],
        order.get("broker_order_id"),
        order.get("filled_price"),
        order.get("filled_quantity"),
        order.get("error"),
        datetime.now().isoformat(timespec="seconds"),
    ))
    conn.commit()
    conn.close()
    return order


def get_most_recent_filled_buy_for_user(user_id, ticker, broker):
    """Most recent FILLED BUY for this exact user/ticker/broker, or None.
    See module docstring for why user_id scoping here is a correctness
    requirement, not just hygiene."""

    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT * FROM saas_orders
        WHERE user_id = ? AND UPPER(ticker) = UPPER(?) AND LOWER(broker) = LOWER(?)
          AND UPPER(side) = 'BUY' AND UPPER(status) = 'FILLED'
          AND filled_price IS NOT NULL
        ORDER BY updated_at DESC
        LIMIT 1
    """, (user_id, ticker, broker)).fetchone()
    conn.close()
    return dict(row) if row else None


def load_orders_for_user(user_id, limit=200):
    """This user's order history, newest first."""
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM saas_orders
        WHERE user_id = ?
        ORDER BY updated_at DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _latest_filled_status_by_ticker(user_id, broker):
    """
    For every ticker this user has ever had a FILLED order for on this
    broker, return {ticker: side_of_most_recent_FILLED_order}. A ticker
    whose most recent FILLED order is a BUY is treated as currently
    open; SELL means it's been closed. Deliberately simple FIFO-free
    open/closed tracking -- this SaaS journal has no lifecycle engine
    yet (unlike the single-owner bot's position_lifecycle_engine.py),
    so "most recent fill's side" is the only signal available. Good
    enough to prevent double-buying/pyramiding a ticker the loop
    already opened; NOT a substitute for real position tracking once
    SELL-side automation is built.
    """
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT ticker, side, updated_at FROM saas_orders
        WHERE user_id = ? AND broker = ? AND status = 'FILLED'
        ORDER BY updated_at ASC
    """, (user_id, broker)).fetchall()
    conn.close()

    latest_side_by_ticker = {}
    for row in rows:
        # ORDER BY updated_at ASC means the last write per ticker wins,
        # so this naturally ends up holding each ticker's most recent side.
        latest_side_by_ticker[row["ticker"]] = row["side"]
    return latest_side_by_ticker


def has_open_position_for_user(user_id, ticker, broker):
    """True if this user's most recent FILLED order for this ticker/broker
    was a BUY with no FILLED SELL after it. See _latest_filled_status_by_ticker
    docstring for the tracking method and its limits."""
    latest = _latest_filled_status_by_ticker(user_id, broker)
    return latest.get(ticker) == "BUY"


def count_open_positions_for_user(user_id, broker):
    """Count of tickers currently considered open for this user/broker
    (see has_open_position_for_user)."""
    latest = _latest_filled_status_by_ticker(user_id, broker)
    return sum(1 for side in latest.values() if side == "BUY")


def list_open_tickers_for_user(user_id, broker):
    """Tickers currently considered open for this user/broker (see
    has_open_position_for_user). Used by saas_exit_engine.py to know
    which positions to check for stop-loss/take-profit/time-based exit."""
    latest = _latest_filled_status_by_ticker(user_id, broker)
    return [ticker for ticker, side in latest.items() if side == "BUY"]


def load_pending_orders_for_user(user_id, broker):
    """
    Orders still sitting at SUBMITTED for this user/broker -- i.e. sent
    to the broker but never confirmed FILLED at submit time (see the
    2026-08-26 fix in saas_decision_engine.py: an Alpaca order that
    responds "new"/"accepted" instead of "filled" is journaled this way
    on purpose, rather than being guessed at). Used by
    saas_reconcile_engine.py to find orders that need a follow-up status
    check. Only rows with a broker_order_id are returned -- without one
    there's nothing to look up.
    """
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM saas_orders
        WHERE user_id = ? AND broker = ? AND status = 'SUBMITTED'
          AND broker_order_id IS NOT NULL AND broker_order_id != ''
        ORDER BY created_at ASC
    """, (user_id, broker)).fetchall()
    conn.close()
    return [dict(row) for row in rows]
