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

FOLLOW-UP 2026-09-02 (Phase 3 of the MT4/MT5 bridge): reconcile_user_mt_
orders() added below closes a DIFFERENT kind of gap than the two
functions above -- neither "SUBMITTED but not yet confirmed FILLED"
problem applies to MT4/5 (MetaApi market orders confirm filled
synchronously, see mt_broker.execute_buy_by_usd_amount()'s docstring).
What MT4/5 has instead: every BUY sets a REAL broker-side stop-loss/
take-profit ON THE POSITION ITSELF (an actual MT5 stop order, not an
application-level flag -- see mt_broker.execute_buy_by_usd_amount()),
so the broker can close a position entirely on its own, with zero
involvement from this codebase, the moment price hits either level (or
the user closes it by hand in their own MT4/5 terminal). This journal
has no way to find out about that unless something checks -- this is
that check, called from saas_decision_engine.run_decision_loop_for_user()
BEFORE exit protection (not from inside the per-asset-class BUY loop
like the two functions above), specifically so a broker-side close is
already reflected in the journal before saas_exit_engine.py or the
position-cap/exposure math further down ever look at it. See that
function's own docstring for the full reasoning and its close-price
approximation tradeoff.
"""

import json

import mt_broker
from engines import saas_broker_factory as factory
from engines import saas_order_manager as journal
from engines.market_data_engine import get_market_data


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


def _get_current_price(ticker):
    """Latest close via yfinance -- duplicated from saas_exit_engine.py's
    identical helper rather than imported (small and stateless, same
    "duplicate rather than cross-import a private helper" precedent
    mt_broker.resolve_mt_symbol()'s docstring already established for
    this codebase). Returns None (never raises) on any failure; the
    caller below treats that as "can't confirm a close price this pass",
    not as a reason to skip closing the journal out."""
    try:
        df = get_market_data(ticker, period="5d", interval="1d")
        if df is None or df.empty:
            return None
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        closes = df["Close"].dropna()
        if closes.empty:
            return None
        return float(closes.iloc[-1])
    except Exception:
        return None


def reconcile_user_mt_orders(user_id):
    """
    FOLLOW-UP 2026-09-02 (Phase 3): for every ticker this journal thinks
    is currently open for this user on MT_BRIDGE, confirms the recorded
    position_id is STILL among this user's live MetaApi positions. If
    it's gone, the broker's own stop-loss/take-profit (or a manual close
    in the user's MT4/5 terminal) closed it without this codebase's
    involvement -- see module docstring's 2026-09-02 FOLLOW-UP for why
    that's possible for MT4/5 specifically. This journals a synthetic
    SELL to bring has_open_position_for_user()/count_open_positions_
    for_user() back in sync with reality.

    Without this, a broker-side stop-loss hit would leave the journal
    thinking the position is open FOREVER: has_open_position_for_user()
    would keep blocking any future BUY on that ticker, and this user's
    open-position cap/exposure% (both read from the journal, not
    MetaApi, in saas_decision_engine.py's per-asset-class loop) would
    stay overcounted indefinitely.

    No exact close price is available here -- MetaApi's live position
    list simply omits anything no longer open, there's no "tell me what
    this closed at" lookup made in this pass. The current market quote
    (yfinance, via _get_current_price() above) is used as a reasonable
    stand-in when available, same tradeoff this project already accepts
    everywhere else a broker doesn't hand back an exact fill/close price
    (see saas_exit_engine.py's ETORO/BINANCE branches); if even that
    fails, the original BUY's own recorded price is used rather than
    leaving the SELL unpriced.

    Never raises -- if MetaApi itself can't be reached this pass, the
    journal is left untouched entirely (better to leave a possibly-stale
    "open" journal for next pass than to wrongly close a position that
    might actually still be live because of a transient API error) and
    an empty list is returned, not an error result -- this mirrors
    get_user_exposure_percent()'s "fail toward doing nothing, not toward
    a misleading action" contract elsewhere in this codebase.

    Returns a list of {ticker, broker_order_id, old_status, new_status,
    message} for whatever changed; an empty list means every journal-open
    MT4/5 ticker is still genuinely open (or nothing was open to check).
    """
    results = []

    open_tickers = journal.list_open_tickers_for_user(user_id, "MT_BRIDGE")
    if not open_tickers:
        return results

    try:
        live_positions = mt_broker.get_user_mt_positions_sync(user_id)
    except Exception:
        return results

    live_position_ids = {
        str(p.get("id")) for p in live_positions if p.get("id") is not None
    }

    for ticker in open_tickers:
        entry_order = journal.get_most_recent_filled_buy_for_user(user_id, ticker, "MT_BRIDGE")
        if entry_order is None:
            continue

        position_id = entry_order.get("broker_order_id")
        if not position_id:
            # No confirmed position_id on record -- shouldn't happen for
            # MT_BRIDGE (buy_mt_for_user() only journals a BUY as FILLED
            # once a real position_id came back, see saas_decision_
            # engine.py's MT_BRIDGE branch), but nothing to check against
            # either way -- leave for a future pass.
            continue

        if str(position_id) in live_position_ids:
            continue  # still genuinely open, nothing to do

        remaining_qty = entry_order.get("remaining_quantity")
        remaining_qty = float(
            remaining_qty if remaining_qty is not None
            else entry_order.get("filled_quantity") or entry_order.get("quantity") or 0
        )
        if remaining_qty <= 0:
            continue

        close_price = _get_current_price(ticker)
        if close_price is None:
            close_price = float(entry_order.get("filled_price") or entry_order.get("price") or 0)

        order = journal.create_order(
            user_id=user_id,
            ticker=ticker,
            side="SELL",
            quantity=remaining_qty,
            trade_amount=remaining_qty * close_price,
            price=close_price,
            asset_class=entry_order.get("asset_class"),
            broker="MT_BRIDGE",
            strategy="RECONCILED_BROKER_CLOSE",
            confidence=entry_order.get("confidence", 0),
            ai_trade_score=entry_order.get("ai_trade_score", 0),
            priority=None,
        )
        order = journal.mark_order_submitted(order, broker_order_id=str(position_id))
        order = journal.mark_order_filled(
            order, filled_price=close_price, filled_quantity=remaining_qty
        )
        journal.save_order(order)
        journal.reduce_remaining_quantity(entry_order["order_id"], remaining_qty)

        results.append({
            "ticker": ticker,
            "broker_order_id": str(position_id),
            "old_status": "FILLED (open)",
            "new_status": "FILLED (reconciled closed)",
            "message": f"{ticker} position {position_id} is no longer open on "
                       f"MetaApi -- broker-side stop-loss/take-profit (or a "
                       f"manual close) closed it without this platform's "
                       f"involvement. Journal synced closed @ ~{close_price} "
                       f"(approximate -- see function docstring).",
        })

    return results
