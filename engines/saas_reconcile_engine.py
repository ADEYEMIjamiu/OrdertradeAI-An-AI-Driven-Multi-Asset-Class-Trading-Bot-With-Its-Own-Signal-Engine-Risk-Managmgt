"""
Per-user order reconciliation for the multi-tenant SaaS product.

Closes the gap left by the 2026-08-26 fix in saas_decision_engine.py: an
Alpaca order that responds "new"/"accepted" right after submit_order()
(rather than an immediate "filled") is correctly journaled as SUBMITTED
instead of being guessed at as FILLED -- but until this file existed,
nothing ever went back to check whether it filled a moment later. A
SUBMITTED order would stay that way in the SaaS journal forever: its
real fill price/quantity never got recorded, has_open_position_for_user()
kept saying "not open" for it even after it genuinely filled, and a
future decision-loop run could try to buy the same ticker again.

Mirrors the single-owner bot's reconcile_alpaca_orders() (app.py) in
spirit -- same "went back and asked the broker what actually happened"
idea -- but scoped to one user's own saas_orders rows and their own
Alpaca credentials, the same separation every other saas_*.py module in
this project keeps from the single-owner's own broker.py/order_manager.py.

CRYPTO is not included here on purpose: buy_crypto_for_user() already
treats Binance testnet market orders as filled synchronously (matching
binance_broker.py's own established behavior), so there's nothing for
crypto orders to reconcile -- they're never left at SUBMITTED in the
first place.

FOLLOW-UP 2026-08-26: reconcile_user_etoro_orders() added below now that
per-user eToro execution exists (saas_broker_factory.py's
buy_etoro_for_user()). Unlike Alpaca, an unconfirmed eToro order has no
broker_order_id to look up (position_id is only known once eToro
confirms the fill) -- so this matches by TICKER against the user's live
eToro portfolio instead, same approach the single-owner bot's own
reconcile_etoro_orders() (engines/broker_sync_engine.py) already
established for exactly this reason.
"""

import json

from engines import saas_broker_factory as factory
from engines import saas_order_manager as journal


def _decode_priority(order):
    """save_order() json.dumps()'s the priority field on every write, so
    a row loaded back via SELECT * already has it JSON-encoded (e.g. the
    string 'null'). Re-saving that same dict without decoding first
    would double-encode it a little more each reconciliation pass.
    Always None in practice for saas_orders today, but decoding properly
    here keeps this correct if that ever changes."""
    raw = order.get("priority")
    if isinstance(raw, str):
        try:
            order["priority"] = json.loads(raw)
        except (ValueError, TypeError):
            order["priority"] = None
    return order


def reconcile_user_alpaca_orders(user_id):
    """
    Checks every SUBMITTED (not-yet-confirmed-filled) Alpaca order for
    this user and updates the journal if Alpaca now reports it filled,
    rejected, or canceled. Returns a list of {ticker, broker_order_id,
    old_status, new_status, message} for whatever changed -- an empty
    list means nothing needed updating (including "no pending orders" or
    "not connected", which aren't errors).

    Never raises for an individual order's lookup failure -- a broker
    hiccup on one order shouldn't block reconciling the rest, same
    principle as saas_decision_engine.py's per-ticker error handling.
    """
    results = []

    pending_orders = journal.load_pending_orders_for_user(user_id, "ALPACA")
    if not pending_orders:
        return results

    for order in pending_orders:
        broker_order_id = order.get("broker_order_id")
        ticker = order.get("ticker")

        try:
            alpaca_order = factory.get_alpaca_order_status_for_user(user_id, broker_order_id)
        except Exception as e:
            results.append({
                "ticker": ticker,
                "broker_order_id": broker_order_id,
                "old_status": "SUBMITTED",
                "new_status": "SUBMITTED",
                "message": f"Could not check status: {e}",
            })
            continue

        response_status = str(getattr(alpaca_order, "status", "") or "").lower()

        if "filled" in response_status:
            response_filled_price = getattr(alpaca_order, "filled_avg_price", None)
            response_filled_qty = getattr(alpaca_order, "filled_qty", None)
            confirmed_qty = float(response_filled_qty) if response_filled_qty else order.get("quantity")

            order = _decode_priority(order)
            order = journal.mark_order_filled(
                order,
                filled_price=float(response_filled_price) if response_filled_price else order.get("price"),
                filled_quantity=confirmed_qty,
            )
            journal.save_order(order)

            # FIX 2026-08-27: a SELL that was SUBMITTED-not-yet-filled at
            # exit-engine time and only confirms filled HERE (a moment
            # later) must still reduce the original BUY lot's remaining
            # quantity -- otherwise a position exited via this delayed
            # path would stay "open" forever under the new lot-based
            # has_open_position_for_user() tracking, even though it's
            # genuinely closed on Alpaca. Same "most recent FILLED BUY,
            # pyramiding-off assumption" lookup used everywhere else in
            # this journal. BUY fills don't need this -- save_order()
            # already defaults a freshly-FILLED BUY's own
            # remaining_quantity to its filled_quantity.
            if str(order.get("side", "")).upper() == "SELL":
                entry_order = journal.get_most_recent_filled_buy_for_user(user_id, ticker, "ALPACA")
                if entry_order is not None:
                    journal.reduce_remaining_quantity(entry_order["order_id"], confirmed_qty)

            results.append({
                "ticker": ticker,
                "broker_order_id": broker_order_id,
                "old_status": "SUBMITTED",
                "new_status": "FILLED",
                "message": f"{ticker} confirmed filled @ {order.get('filled_price')}.",
            })

        elif response_status in ("canceled", "cancelled", "expired"):
            order = _decode_priority(order)
            order = journal.mark_order_cancelled(order, reason=f"Alpaca status: {response_status}")
            journal.save_order(order)

            results.append({
                "ticker": ticker,
                "broker_order_id": broker_order_id,
                "old_status": "SUBMITTED",
                "new_status": "CANCELLED",
                "message": f"{ticker} order was {response_status} on Alpaca, never filled.",
            })

        elif response_status == "rejected":
            order = _decode_priority(order)
            order = journal.mark_order_rejected(order, reason="Rejected by Alpaca")
            journal.save_order(order)

            results.append({
                "ticker": ticker,
                "broker_order_id": broker_order_id,
                "old_status": "SUBMITTED",
                "new_status": "REJECTED",
                "message": f"{ticker} order was rejected by Alpaca.",
            })

        # Anything else (still "new"/"accepted"/"pending_new") -- leave
        # as SUBMITTED, nothing to update yet, no result entry needed.

    return results


def reconcile_user_etoro_orders(user_id):
    """
    Checks every SUBMITTED (not-yet-confirmed-filled) eToro order for
    this user and updates the journal if a matching open position has
    since appeared on eToro. See module docstring for why this matches
    by ticker rather than a broker order id.

    Never raises for an individual order's lookup failure -- same
    per-order isolation as reconcile_user_alpaca_orders() above.
    """
    results = []

    pending_orders = journal.load_pending_orders_for_user_by_ticker(user_id, "ETORO")
    if not pending_orders:
        return results

    for order in pending_orders:
        ticker = order.get("ticker")

        try:
            position = factory.find_etoro_position_by_ticker_for_user(user_id, ticker)
        except Exception as e:
            results.append({
                "ticker": ticker,
                "broker_order_id": order.get("broker_order_id"),
                "old_status": "SUBMITTED",
                "new_status": "SUBMITTED",
                "message": f"Could not check status: {e}",
            })
            continue

        if position is None:
            # Still genuinely unconfirmed -- leave it for the next pass.
            continue

        order = _decode_priority(order)
        order["broker_order_id"] = str(position["position_id"])
        order = journal.mark_order_filled(
            order,
            filled_price=position.get("open_price") or order.get("price"),
            filled_quantity=position.get("quantity") or order.get("quantity"),
        )
        journal.save_order(order)

        results.append({
            "ticker": ticker,
            "broker_order_id": order.get("broker_order_id"),
            "old_status": "SUBMITTED",
            "new_status": "FILLED",
            "message": f"{ticker} confirmed filled @ {order.get('filled_price')} "
                       f"(late eToro confirmation, position {order['broker_order_id']}).",
        })

    return results
