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
    # FIX 2026-08-27: added for partial-exit / break-even support (see
    # get_open_lot_for_user()'s docstring below). ALTER TABLE ADD COLUMN
    # is the only portable way to add a column to an existing sqlite
    # table -- wrapped in try/except since sqlite has no "ADD COLUMN IF
    # NOT EXISTS", and this runs on every _get_connection() call.
    try:
        conn.execute("ALTER TABLE saas_orders ADD COLUMN remaining_quantity REAL")
    except sqlite3.OperationalError:
        pass  # column already exists
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
    after every mark_order_*() call.

    FIX 2026-08-27: a FILLED BUY now gets remaining_quantity defaulted to
    its filled_quantity the first time it's saved -- see
    get_open_lot_for_user()'s docstring for why this exists (partial-exit
    / break-even support). On a re-save of an order that was already
    inserted once, the ON CONFLICT clause deliberately does NOT overwrite
    remaining_quantity with whatever the in-memory `order` dict happens
    to hold (COALESCE keeps the value already in the database if one
    exists) -- reduce_remaining_quantity() below is the ONLY thing
    allowed to actually reduce it after the initial fill, via a direct
    UPDATE. Without this COALESCE, re-saving a stale copy of the same
    order (e.g. from a reconciliation pass holding an old in-memory
    dict) could silently reset a partially-sold lot back to its full
    original quantity.
    """
    remaining_quantity = order.get("remaining_quantity")
    if (
        remaining_quantity is None
        and str(order.get("side", "")).upper() == "BUY"
        and str(order.get("status", "")).upper() == "FILLED"
    ):
        remaining_quantity = order.get("filled_quantity")

    conn = _get_connection()
    conn.execute("""
        INSERT INTO saas_orders (
            order_id, user_id, created_at, ticker, side, quantity, trade_amount,
            price, asset_class, broker, strategy, confidence,
            ai_trade_score, priority, stop_loss, take_profit, status,
            broker_order_id, filled_price, filled_quantity, error, updated_at,
            remaining_quantity
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET
            status=excluded.status,
            broker_order_id=excluded.broker_order_id,
            filled_price=excluded.filled_price,
            filled_quantity=excluded.filled_quantity,
            error=excluded.error,
            updated_at=excluded.updated_at,
            remaining_quantity=COALESCE(saas_orders.remaining_quantity, excluded.remaining_quantity)
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
        remaining_quantity,
    ))
    conn.commit()
    conn.close()
    return order


def get_most_recent_filled_buy_for_user(user_id, ticker, broker):
    """Most recent FILLED BUY for this exact user/ticker/broker, or None.
    See module docstring for why user_id scoping here is a correctness
    requirement, not just hygiene.

    FIX 2026-08-27: ordering by updated_at alone breaks ties arbitrarily
    when two orders land in the same second (updated_at has only
    second-level precision) -- caught live by a re-entry-after-full-close
    smoke test, where a fresh BUY landing in the same second as the
    closing SELL of the PREVIOUS lot could get sorted behind it,
    resurfacing the stale/closed lot instead of the new open one. `rowid`
    (sqlite's own implicit, strictly-increasing insertion-order column)
    is a reliable tie-breaker since order_id is a random UUID, not
    sortable by creation order itself.
    """

    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT * FROM saas_orders
        WHERE user_id = ? AND UPPER(ticker) = UPPER(?) AND LOWER(broker) = LOWER(?)
          AND UPPER(side) = 'BUY' AND UPPER(status) = 'FILLED'
          AND filled_price IS NOT NULL
        ORDER BY updated_at DESC, rowid DESC
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


def update_stop_loss(order_id, new_stop_loss):
    """
    Directly UPDATE a BUY order's stop_loss field -- used by
    engines/saas_position_lifecycle_engine.py to move a position's floor
    to break-even in place, so saas_exit_engine.py's very next pass
    checks price against the NEW level automatically (same order row is
    the single source of truth for this position's exit levels).

    A dedicated direct UPDATE rather than routing through save_order():
    save_order()'s ON CONFLICT clause deliberately does NOT touch
    stop_loss/take_profit on a re-save (it's built for the PENDING ->
    SUBMITTED -> FILLED status lifecycle, not for editing an already-
    FILLED order's risk levels) -- reusing it here would either require
    loosening that contract for every caller or silently no-op, which is
    exactly the bug this function's own existence was created to avoid
    catching in a smoke test.
    """
    conn = _get_connection()
    conn.execute(
        "UPDATE saas_orders SET stop_loss = ? WHERE order_id = ?",
        (float(new_stop_loss), order_id),
    )
    conn.commit()
    conn.close()


def reduce_remaining_quantity(order_id, sold_qty):
    """
    Reduce a FILLED BUY order's remaining_quantity by sold_qty (floored
    at 0, never negative). This is the ONLY function that should ever
    shrink remaining_quantity after its initial fill -- see save_order()'s
    docstring for why a plain re-save can't be trusted to do this safely.
    Called by saas_exit_engine.py (full exits) and engines/saas_position_
    lifecycle_engine.py (partial profit-taking) right after a SELL
    against a specific lot is confirmed filled, and by
    saas_reconcile_engine.py once a previously-SUBMITTED SELL is
    confirmed filled asynchronously.

    COALESCE(remaining_quantity, quantity) handles a BUY row saved before
    this column existed (remaining_quantity is NULL) by treating it as
    "full original quantity" the first time anything tries to reduce it
    -- consistent with get_open_lot_for_user()'s "NULL means fully open"
    treatment elsewhere.
    """
    conn = _get_connection()
    conn.execute("""
        UPDATE saas_orders
        SET remaining_quantity = MAX(0, COALESCE(remaining_quantity, quantity) - ?)
        WHERE order_id = ?
    """, (float(sold_qty), order_id))
    conn.commit()
    conn.close()


def _open_lots_by_ticker(user_id, broker):
    """
    {ticker: order_row} for every ticker whose most recent FILLED BUY on
    this user/broker still has quantity remaining. See
    get_open_lot_for_user()'s docstring for the full reasoning -- this is
    the bulk version of that same lookup, used by list_open_tickers_for_
    user()/count_open_positions_for_user() below.

    FIX 2026-08-27: previously this (as _latest_filled_status_by_ticker())
    looked at the most recent FILLED order of EITHER side per ticker and
    treated "most recent was a SELL" as closed. That broke the moment a
    PARTIAL SELL could exist -- a partial exit would become the "most
    recent" order and wrongly flip an still-mostly-open position to
    closed. Now it looks ONLY at FILLED BUY rows and asks the lot itself
    (remaining_quantity) whether anything is left, which is correct
    regardless of how many SELL rows exist against it or when they were
    written.
    """
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM saas_orders
        WHERE user_id = ? AND LOWER(broker) = LOWER(?)
          AND UPPER(side) = 'BUY' AND UPPER(status) = 'FILLED'
          AND filled_price IS NOT NULL
        ORDER BY updated_at ASC, rowid ASC
    """, (user_id, broker)).fetchall()
    conn.close()

    latest_by_ticker = {}
    for row in rows:
        # ORDER BY updated_at ASC, rowid ASC means the last write per
        # ticker wins even when two rows share the same updated_at
        # second -- see get_most_recent_filled_buy_for_user()'s
        # docstring for why rowid is needed as a tie-breaker.
        latest_by_ticker[row["ticker"]] = dict(row)

    open_lots = {}
    for ticker, order in latest_by_ticker.items():
        remaining = order.get("remaining_quantity")
        # NULL remaining_quantity means this row predates partial-exit
        # tracking (or is otherwise untouched by reduce_remaining_
        # quantity()) -- treated as fully open, matching the pre-2026-08-27
        # behavior for every position that never had a partial sell.
        if remaining is None or remaining > 1e-9:
            open_lots[ticker] = order
    return open_lots


def get_open_lot_for_user(user_id, ticker, broker):
    """
    The most recent FILLED BUY for this ticker/user/broker, if it still
    has quantity remaining -- None if there's no open lot. This is the
    canonical "is this position open, and exactly how much of it
    remains" lookup; has_open_position_for_user()/list_open_tickers_
    for_user()/count_open_positions_for_user() are all built on it (via
    _open_lots_by_ticker() for the bulk case).

    Only reliable with pyramiding off (at most one open lot per ticker
    at a time) -- same pre-existing constraint apply_crypto_risk_
    management() in app.py already documents for its own "most recent
    BUY fill" entry-price lookup. If pyramiding is ever turned on for
    this SaaS product, this would need to become real per-lot (not
    per-ticker) tracking.
    """
    order = get_most_recent_filled_buy_for_user(user_id, ticker, broker)
    if order is None:
        return None
    remaining = order.get("remaining_quantity")
    if remaining is not None and remaining <= 1e-9:
        return None
    return order


def has_open_position_for_user(user_id, ticker, broker):
    """True if this user has an open lot for this ticker/broker -- see
    get_open_lot_for_user()'s docstring for the tracking method."""
    return get_open_lot_for_user(user_id, ticker, broker) is not None


def count_open_positions_for_user(user_id, broker):
    """Count of tickers currently considered open for this user/broker
    (see get_open_lot_for_user())."""
    return len(_open_lots_by_ticker(user_id, broker))


def list_open_tickers_for_user(user_id, broker):
    """Tickers currently considered open for this user/broker (see
    get_open_lot_for_user()). Used by saas_exit_engine.py to know which
    positions to check for stop-loss/take-profit/time-based exit, and by
    engines/saas_position_lifecycle_engine.py for break-even/partial-
    profit checks."""
    return list(_open_lots_by_ticker(user_id, broker).keys())


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


def load_pending_orders_for_user_by_ticker(user_id, broker):
    """
    Same idea as load_pending_orders_for_user(), but WITHOUT the
    broker_order_id requirement -- eToro's buy_etoro_for_user() leaves
    broker_order_id as None for a SUBMITTED-not-yet-confirmed order
    (there's no confirmed position_id yet by definition), so the
    broker_order_id-based loader above would never see these rows at
    all. saas_reconcile_engine.py's reconcile_user_etoro_orders() uses
    this instead, matching against the live eToro portfolio by ticker
    (find_etoro_position_by_ticker_for_user()) rather than an order id --
    mirrors the single-owner bot's reconcile_etoro_orders() (app.py),
    which does the same ticker-based matching for the same reason.
    """
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM saas_orders
        WHERE user_id = ? AND broker = ? AND status = 'SUBMITTED'
        ORDER BY created_at ASC
    """, (user_id, broker)).fetchall()
    conn.close()
    return [dict(row) for row in rows]
