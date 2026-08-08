"""
Broker synchronisation and order-reconciliation engine.

This module converts Alpaca SDK objects into predictable dictionaries
that can be displayed and processed safely by the trading application.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from broker import get_open_positions, get_orders



def safe_float(value: Any, default: float = 0.0) -> float:
    """Convert a value to float safely."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_text(value: Any, default: str = "") -> str:
    """Convert SDK values and enum values to readable text."""
    if value is None:
        return default

    enum_value = getattr(value, "value", None)

    if enum_value is not None:
        return str(enum_value)

    return str(value)


def safe_datetime(value: Any) -> str | None:
    """Convert broker datetime values into ISO-formatted strings."""
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.isoformat()

    return str(value)


def normalise_alpaca_order(order: Any) -> dict[str, Any]:
    """
    Convert one Alpaca order object into a stable dictionary.

    Alpaca fields can differ depending on the order state, so all fields
    are accessed defensively.
    """
    return {
        "Order ID": safe_text(getattr(order, "id", None)),
        "Client Order ID": safe_text(
            getattr(order, "client_order_id", None)
        ),
        "Ticker": safe_text(getattr(order, "symbol", None)),
        "Side": safe_text(getattr(order, "side", None)).upper(),
        "Type": safe_text(getattr(order, "type", None)).upper(),
        "Status": safe_text(getattr(order, "status", None)).upper(),
        "Requested Qty": safe_float(getattr(order, "qty", None)),
        "Filled Qty": safe_float(getattr(order, "filled_qty", None)),
        "Requested Notional": safe_float(
            getattr(order, "notional", None)
        ),
        "Average Fill Price": safe_float(
            getattr(order, "filled_avg_price", None)
        ),
        "Submitted": safe_datetime(
            getattr(order, "submitted_at", None)
        ),
        "Filled": safe_datetime(
            getattr(order, "filled_at", None)
        ),
        "Cancelled": safe_datetime(
            getattr(order, "canceled_at", None)
        ),
        "Expired": safe_datetime(
            getattr(order, "expired_at", None)
        ),
        "Rejected": safe_datetime(
            getattr(order, "failed_at", None)
        ),
    }


def normalise_alpaca_position(position: Any) -> dict[str, Any]:
    """Convert one Alpaca position into a stable dictionary."""
    quantity = safe_float(getattr(position, "qty", None))
    average_entry = safe_float(
        getattr(position, "avg_entry_price", None)
    )
    current_price = safe_float(
        getattr(position, "current_price", None)
    )
    market_value = safe_float(
        getattr(position, "market_value", None)
    )
    unrealised_pnl = safe_float(
        getattr(position, "unrealized_pl", None)
    )
    unrealised_return = safe_float(
        getattr(position, "unrealized_plpc", None)
    ) * 100

    return {
        "Ticker": safe_text(getattr(position, "symbol", None)),
        "Side": safe_text(getattr(position, "side", None)).upper(),
        "Shares": quantity,
        "Entry Price": average_entry,
        "Current Price": current_price,
        "Market Value": market_value,
        "Unrealised PnL": unrealised_pnl,
        "Return %": unrealised_return,
    }


def get_broker_order_snapshot() -> dict[str, Any]:
    """
    Return the latest broker orders grouped by operational status.
    """
    try:
        raw_orders = get_orders()

        orders = [
            normalise_alpaca_order(order)
            for order in raw_orders
        ]

        filled_orders = [
            order
            for order in orders
            if order["Status"] == "FILLED"
        ]

        active_statuses = {
            "NEW",
            "ACCEPTED",
            "PENDING_NEW",
            "PARTIALLY_FILLED",
            "PENDING_REPLACE",
            "PENDING_CANCEL",
            "HELD",
        }

        active_orders = [
            order
            for order in orders
            if order["Status"] in active_statuses
        ]

        failed_statuses = {
            "REJECTED",
            "CANCELED",
            "EXPIRED",
            "STOPPED",
            "SUSPENDED",
        }

        failed_orders = [
            order
            for order in orders
            if order["Status"] in failed_statuses
        ]

        return {
            "connected": True,
            "orders": orders,
            "filled_orders": filled_orders,
            "active_orders": active_orders,
            "failed_orders": failed_orders,
            "order_count": len(orders),
            "active_count": len(active_orders),
            "filled_count": len(filled_orders),
            "failed_count": len(failed_orders),
            "error": None,
        }

    except Exception as exc:
        return {
            "connected": False,
            "orders": [],
            "filled_orders": [],
            "active_orders": [],
            "failed_orders": [],
            "order_count": 0,
            "active_count": 0,
            "filled_count": 0,
            "failed_count": 0,
            "error": str(exc),
        }


def get_broker_position_snapshot() -> dict[str, Any]:
    """Return the current Alpaca positions in normalised form."""
    try:
        raw_positions = get_open_positions()

        positions = [
            normalise_alpaca_position(position)
            for position in raw_positions
        ]

        total_market_value = sum(
            position["Market Value"]
            for position in positions
        )

        total_unrealised_pnl = sum(
            position["Unrealised PnL"]
            for position in positions
        )

        owned_symbols = {
            position["Ticker"]
            for position in positions
            if position["Ticker"]
        }

        return {
            "connected": True,
            "positions": positions,
            "position_count": len(positions),
            "owned_symbols": owned_symbols,
            "total_market_value": total_market_value,
            "total_unrealised_pnl": total_unrealised_pnl,
            "error": None,
        }

    except Exception as exc:
        return {
            "connected": False,
            "positions": [],
            "position_count": 0,
            "owned_symbols": set(),
            "total_market_value": 0.0,
            "total_unrealised_pnl": 0.0,
            "error": str(exc),
        }


def broker_has_active_order(symbol: str) -> bool:
    """
    Check if there is a REAL active order (still pending execution).

    This prevents duplicate orders while allowing:
    - Filled trades to NOT block new entries
    - Completed trades to be ignored
    """

    symbol = symbol.upper().strip()
    snapshot = get_broker_order_snapshot()

    if not snapshot["connected"]:
        return False

    for order in snapshot["active_orders"]:
        order_symbol = order.get("Ticker", "").upper()
        order_status = order.get("Status", "").upper()
        filled_qty = float(order.get("Filled Qty", 0))

        if order_symbol == symbol:
            if order_status in ["NEW", "ACCEPTED", "PENDING_NEW", "PARTIALLY_FILLED"]:
                return True

    return False


def broker_already_owns_symbol(symbol: str) -> bool:
    """
    Check if we already HOLD a position in this symbol.
    Prevents repeated BUY orders.
    """

    symbol = symbol.upper().strip()
    snapshot = get_broker_position_snapshot()

    if not snapshot["connected"]:
        return False

    for position in snapshot["positions"]:
        pos_symbol = position.get("Ticker", "").upper()
        shares = float(position.get("Shares", 0))

        if pos_symbol == symbol and shares > 0:
            return True

    return False

def get_broker_state_health():
    """
    Inspect Alpaca broker state and return a broker health summary.

    Checks:
    - Broker connection
    - Open positions
    - Active orders
    - Duplicate active orders
    - Position/order symbol overlap
    """

    health = {
        "status": "HEALTHY",
        "broker_connected": False,
        "open_positions": 0,
        "active_orders": 0,
        "duplicate_order_symbols": [],
        "position_order_conflicts": [],
        "issues": [],
    }

    try:
        positions = get_open_positions()
        orders = get_orders()

        health["broker_connected"] = True

        position_symbols = [
            str(position.symbol).upper().strip()
            for position in positions
        ]

        active_statuses = {
            "new",
            "accepted",
            "pending_new",
            "partially_filled",
        }

        active_orders = [
            order
            for order in orders
            if str(order.status).lower() in active_statuses
        ]

        health["open_positions"] = len(position_symbols)
        health["active_orders"] = len(active_orders)

        active_order_symbols = [
            str(order.symbol).upper().strip()
            for order in active_orders
        ]

        duplicate_symbols = sorted(
            {
                symbol
                for symbol in active_order_symbols
                if active_order_symbols.count(symbol) > 1
            }
        )

        health["duplicate_order_symbols"] = duplicate_symbols

        if duplicate_symbols:
            health["issues"].append(
                "Duplicate active broker orders detected."
            )

        position_order_conflicts = sorted(
            set(position_symbols).intersection(
                active_order_symbols
            )
        )

        health["position_order_conflicts"] = (
            position_order_conflicts
        )

        if position_order_conflicts:
            health["issues"].append(
                "Open positions also have active broker orders."
            )

        if health["issues"]:
            health["status"] = "WARNING"

        return health

    except Exception as e:
        health["status"] = "CRITICAL"
        health["broker_connected"] = False

        health["issues"].append(
            f"Broker state health check failed: {e}"
        )

        return health
    
def broker_execution_gate(action: str) -> tuple[bool, str]:
    """
    Decide whether AI trade execution is allowed based on
    the current Alpaca broker state.

    Policy:
    - HEALTHY: BUY and SELL allowed.
    - WARNING: New BUY entries blocked, SELL exits allowed.
    - CRITICAL: All AI execution blocked.
    """

    action = str(action).upper().strip()

    health = get_broker_state_health()

    status = str(
        health.get("status", "CRITICAL")
    ).upper().strip()

    if status == "CRITICAL":
        return (
            False,
            "Broker health is CRITICAL. "
            "AI execution is blocked."
        )

    if status == "WARNING":
        if action == "BUY":
            return (
                False,
                "Broker health is WARNING. "
                "New BUY entries are blocked."
            )

        if action == "SELL":
            return (
                True,
                "Broker health is WARNING, but SELL exits "
                "remain allowed for risk reduction."
            )

    if status == "HEALTHY":
        return (
            True,
            "Broker health is HEALTHY. Execution allowed."
        )

    return (
        False,
        f"Unknown broker health status: {status}. "
        "Execution blocked."
    )
    
def get_ai_trading_readiness(
    execution_mode: str = "MANUAL",
    action: str = "BUY",
) -> dict:
    """
    Evaluate whether the AI trading system is ready to submit
    an Alpaca paper-trading order.

    execution_mode:
        MANUAL or AUTOMATIC

    action:
        BUY or SELL
    """

    from config import (
        EXECUTION_KILL_SWITCH,
        MANUAL_ALPACA_EXECUTION_ENABLED,
        AUTOMATIC_ALPACA_EXECUTION_ENABLED,
        ALLOW_EMERGENCY_SELL_EXITS,
        REQUIRE_BROKER_CONNECTION,
        REQUIRE_HEALTHY_BROKER_STATE,
        REQUIRE_MARKET_OPEN_FOR_BUYS,
        BLOCK_BUYS_ON_BROKER_WARNING,
        BLOCK_ALL_ON_BROKER_CRITICAL,
        REQUIRE_ALPACA_PAPER_ENVIRONMENT,
        LIVE_TRADING,
    )

    execution_mode = str(execution_mode).upper().strip()
    action = str(action).upper().strip()

    readiness = {
        "ready": True,
        "status": "READY",
        "execution_mode": execution_mode,
        "action": action,
        "checks": {},
        "reasons": [],
    }

    # --------------------------------------------------------
    # 1. Validate execution mode
    # --------------------------------------------------------
    valid_execution_mode = execution_mode in {
        "MANUAL",
        "AUTOMATIC",
    }

    readiness["checks"]["valid_execution_mode"] = (
        valid_execution_mode
    )

    if not valid_execution_mode:
        readiness["ready"] = False
        readiness["reasons"].append(
            f"Unsupported execution mode: {execution_mode}"
        )

    # --------------------------------------------------------
    # 2. Validate trade action
    # --------------------------------------------------------
    valid_action = action in {"BUY", "SELL"}

    readiness["checks"]["valid_action"] = valid_action

    if not valid_action:
        readiness["ready"] = False
        readiness["reasons"].append(
            f"Unsupported trade action: {action}"
        )

    # --------------------------------------------------------
    # 3. Master kill switch
    # --------------------------------------------------------
    kill_switch_clear = not EXECUTION_KILL_SWITCH

    # Emergency SELL exits may remain available.
    if (
        EXECUTION_KILL_SWITCH
        and action == "SELL"
        and ALLOW_EMERGENCY_SELL_EXITS
    ):
        kill_switch_clear = True

    readiness["checks"]["kill_switch_clear"] = (
        kill_switch_clear
    )

    if not kill_switch_clear:
        readiness["ready"] = False
        readiness["reasons"].append(
            "Execution kill switch is active."
        )

    # --------------------------------------------------------
    # 4. Execution-mode permission
    # --------------------------------------------------------
    if execution_mode == "MANUAL":
        execution_mode_enabled = (
            MANUAL_ALPACA_EXECUTION_ENABLED
        )
    else:
        execution_mode_enabled = (
            AUTOMATIC_ALPACA_EXECUTION_ENABLED
        )

    readiness["checks"]["execution_mode_enabled"] = (
        execution_mode_enabled
    )

    if not execution_mode_enabled:
        readiness["ready"] = False
        readiness["reasons"].append(
            f"{execution_mode.title()} Alpaca execution "
            "is disabled."
        )

    # --------------------------------------------------------
    # 5. Confirm broker execution mode
    # --------------------------------------------------------
    paper_environment_confirmed = bool(LIVE_TRADING)

    readiness["checks"]["paper_environment_confirmed"] = (
        paper_environment_confirmed
    )

    if (
        REQUIRE_ALPACA_PAPER_ENVIRONMENT
        and not paper_environment_confirmed
    ):
        readiness["ready"] = False
        readiness["reasons"].append(
            "Alpaca paper-trading mode is not enabled."
        )

    # --------------------------------------------------------
    # 6. Broker state health
    # --------------------------------------------------------
    broker_health = get_broker_state_health()

    broker_connected = bool(
        broker_health.get("broker_connected", False)
    )

    broker_status = str(
        broker_health.get("status", "CRITICAL")
    ).upper().strip()

    market_open = bool(
        broker_health.get("market_open", False)
    )

    readiness["checks"]["broker_connected"] = (
        broker_connected
    )

    readiness["checks"]["broker_status"] = broker_status
    readiness["checks"]["market_open"] = market_open

    readiness["broker_health"] = broker_health

    # --------------------------------------------------------
    # 7. Require broker connection
    # --------------------------------------------------------
    if REQUIRE_BROKER_CONNECTION and not broker_connected:
        readiness["ready"] = False
        readiness["reasons"].append(
            "Alpaca broker connection is unavailable."
        )

    # --------------------------------------------------------
    # 8. Critical broker-state protection
    # --------------------------------------------------------
    if (
        BLOCK_ALL_ON_BROKER_CRITICAL
        and broker_status == "CRITICAL"
    ):
        emergency_sell_allowed = (
            action == "SELL"
            and ALLOW_EMERGENCY_SELL_EXITS
        )

        if not emergency_sell_allowed:
            readiness["ready"] = False
            readiness["reasons"].append(
                "Broker state is CRITICAL."
            )

    # --------------------------------------------------------
    # 9. Healthy broker-state requirement
    # --------------------------------------------------------
    if (
        REQUIRE_HEALTHY_BROKER_STATE
        and broker_status not in {"HEALTHY", "WARNING"}
    ):
        emergency_sell_allowed = (
            action == "SELL"
            and ALLOW_EMERGENCY_SELL_EXITS
        )

        if not emergency_sell_allowed:
            readiness["ready"] = False
            readiness["reasons"].append(
                "Broker state is not healthy enough "
                "for execution."
            )

    # --------------------------------------------------------
    # 10. Block new BUY orders during WARNING state
    # --------------------------------------------------------
    if (
        action == "BUY"
        and BLOCK_BUYS_ON_BROKER_WARNING
        and broker_status == "WARNING"
    ):
        readiness["ready"] = False
        readiness["reasons"].append(
            "New BUY orders are blocked while broker "
            "state is WARNING."
        )

    # --------------------------------------------------------
    # 11. Market-hours gate for BUY orders
    # --------------------------------------------------------
    if (
        action == "BUY"
        and REQUIRE_MARKET_OPEN_FOR_BUYS
        and not market_open
    ):
        readiness["ready"] = False
        readiness["reasons"].append(
            "US stock market is currently closed."
        )

    # --------------------------------------------------------
    # Final status
    # --------------------------------------------------------
    if readiness["ready"]:
        readiness["status"] = "READY"
        readiness["message"] = (
            f"{execution_mode.title()} {action} execution "
            "is authorised."
        )
    else:
        readiness["status"] = "BLOCKED"
        readiness["message"] = " | ".join(
            readiness["reasons"]
        )

    return readiness

def reconcile_alpaca_orders() -> dict[str, Any]:
    """
    Sync locally-journaled Alpaca orders that are still PENDING/SUBMITTED
    against Alpaca's real order status.

    2026-08-08: execute_alpaca_trades() used to mark every BUY/SELL
    "FILLED" in the local order journal the instant it was submitted,
    using the market_df row's displayed price -- without ever checking
    back with Alpaca. That's usually harmless (paper fills are normally
    near-instant during market hours) but breaks down the moment a fill
    isn't instant -- e.g. a market order submitted while the US market is
    closed, which Alpaca queues as "accepted" until the next open. The
    local Order Book then claims FILLED while Alpaca (and the dashboard's
    own Trade Log, which reads live from Alpaca) shows the order still
    pending, with no price ever actually realised.

    This function is the fix: it looks at every locally-journaled Alpaca
    order still sitting in PENDING/SUBMITTED, checks Alpaca's real status
    for it, and corrects the local record -- FILLED (with the real fill
    price/qty) if Alpaca confirms it, CANCELLED if Alpaca rejected/
    expired/cancelled it, or left alone if it's still genuinely pending.
    Cheap to call on every page load: orders already FILLED/FAILED/
    CANCELLED locally are skipped entirely, so a quiet order book costs
    nothing.
    """
    from engines.order_manager import (
        load_orders,
        save_order,
        mark_order_filled,
        mark_order_cancelled,
        ORDER_STATUS_PENDING,
        ORDER_STATUS_SUBMITTED,
    )

    result = {"checked": 0, "updated": 0, "error": None}

    try:
        local_orders = load_orders(limit=200)
    except Exception as exc:
        result["error"] = f"Could not read local order journal: {exc}"
        return result

    pending_local = [
        order
        for order in local_orders
        if order.get("broker") == "alpaca"
        and order.get("status") in (ORDER_STATUS_PENDING, ORDER_STATUS_SUBMITTED)
        and order.get("broker_order_id")
    ]

    result["checked"] = len(pending_local)

    if not pending_local:
        return result

    try:
        raw_orders = get_orders()
    except Exception as exc:
        result["error"] = f"Could not reach Alpaca to reconcile orders: {exc}"
        return result

    orders_by_id = {
        str(getattr(order, "id", "")): order for order in raw_orders
    }

    settled_statuses = {
        "canceled",
        "cancelled",
        "expired",
        "rejected",
        "stopped",
        "suspended",
    }

    for local_order in pending_local:
        broker_order = orders_by_id.get(str(local_order.get("broker_order_id")))

        if broker_order is None:
            continue

        status = safe_text(getattr(broker_order, "status", None)).lower()

        if status == "filled":
            filled_qty = safe_float(getattr(broker_order, "filled_qty", None))
            filled_price = safe_float(getattr(broker_order, "filled_avg_price", None))

            local_order = mark_order_filled(
                local_order,
                filled_price=filled_price or local_order.get("price"),
                filled_quantity=filled_qty or local_order.get("quantity"),
            )
            save_order(local_order)
            result["updated"] += 1

        elif status in settled_statuses:
            local_order = mark_order_cancelled(
                local_order, reason=f"Alpaca reported status: {status}"
            )
            save_order(local_order)
            result["updated"] += 1

        # Anything else (new/accepted/pending_new/partially_filled) is
        # still genuinely in flight -- leave it as SUBMITTED and check
        # again next reconcile pass.

    return result


def execute_order_with_alpaca(order: dict) -> dict:

    from broker import place_order
    from engines.order_manager import (
        mark_order_submitted,
        mark_order_failed,
    )

    try:
        ticker = order["ticker"]
        side = order["side"]
        quantity = order["quantity"]

        # ✅ SAFE IMPORT INSIDE FUNCTION (VERY IMPORTANT)
        from engines.broker_sync_engine import broker_already_owns_symbol

        # 🚫 BLOCK DUPLICATE BUY
        if side.upper() == "BUY" and broker_already_owns_symbol(ticker):
            print(f"⛔ SKIPPED BUY → already holding {ticker}")
            return order

        print(f"🚀 EXECUTING ORDER → {side} {ticker} ({quantity})")

        response = place_order(
            symbol=ticker,
            qty=quantity,
            side=side.lower(),
        )

        order = mark_order_submitted(
            order,
            broker_order_id=str(response.get("id", "UNKNOWN")),
        )

        print(f"✅ ORDER SENT → {ticker}")

        return order

    except Exception as e:
        print(f"❌ EXECUTION FAILED → {order['ticker']} | {e}")

        return mark_order_failed(order, e)