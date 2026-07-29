from datetime import datetime
import json
import sqlite3
import uuid

DB_NAME = "trade_journal.db"


def _get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
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
    return conn


def save_order(order):
    """
    Persist (insert or update) an order's current state. Call this after
    create_order() and again after every mark_order_*() call, so the full
    lifecycle of every order -- across every broker -- is queryable later,
    not just whatever happened to still be in memory.
    """
    conn = _get_connection()
    conn.execute("""
        INSERT INTO orders (
            order_id, created_at, ticker, side, quantity, trade_amount,
            price, asset_class, broker, strategy, confidence,
            ai_trade_score, priority, stop_loss, take_profit, status,
            broker_order_id, filled_price, filled_quantity, error, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET
            status=excluded.status,
            broker_order_id=excluded.broker_order_id,
            filled_price=excluded.filled_price,
            filled_quantity=excluded.filled_quantity,
            error=excluded.error,
            updated_at=excluded.updated_at
    """, (
        order["order_id"],
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


def load_orders(limit=200):
    """
    Return the most recent orders (all brokers, all statuses) as a list
    of dicts, newest first. This is the real order book -- a single,
    persistent, queryable view across local paper, Alpaca, and Binance,
    instead of three separate in-memory lists that vanish on restart.
    """
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM orders
        ORDER BY updated_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


ORDER_STATUS_PENDING = "PENDING"
ORDER_STATUS_SUBMITTED = "SUBMITTED"
ORDER_STATUS_FILLED = "FILLED"
ORDER_STATUS_REJECTED = "REJECTED"
ORDER_STATUS_CANCELLED = "CANCELLED"
ORDER_STATUS_FAILED = "FAILED"


def create_order(
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
    """
    Creates a professional order object before execution.
    """

    return {
        "order_id": str(uuid.uuid4()),
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


def mark_order_submitted(order, broker_order_id=None):
    order["status"] = ORDER_STATUS_SUBMITTED
    order["broker_order_id"] = broker_order_id
    order["submitted_at"] = datetime.now().isoformat(timespec="seconds")
    return order


def mark_order_filled(order, filled_price=None, filled_quantity=None):
    order["status"] = ORDER_STATUS_FILLED
    order["filled_at"] = datetime.now().isoformat(timespec="seconds")

    if filled_price is not None:
        order["filled_price"] = round(float(filled_price), 4)

    if filled_quantity is not None:
        order["filled_quantity"] = round(float(filled_quantity), 6)

    return order


def mark_order_rejected(order, reason):
    order["status"] = ORDER_STATUS_REJECTED
    order["error"] = reason
    order["rejected_at"] = datetime.now().isoformat(timespec="seconds")
    return order


def mark_order_failed(order, error):
    order["status"] = ORDER_STATUS_FAILED
    order["error"] = str(error)
    order["failed_at"] = datetime.now().isoformat(timespec="seconds")
    return order


def mark_order_cancelled(order, reason="Cancelled"):
    order["status"] = ORDER_STATUS_CANCELLED
    order["error"] = reason
    order["cancelled_at"] = datetime.now().isoformat(timespec="seconds")
    return order