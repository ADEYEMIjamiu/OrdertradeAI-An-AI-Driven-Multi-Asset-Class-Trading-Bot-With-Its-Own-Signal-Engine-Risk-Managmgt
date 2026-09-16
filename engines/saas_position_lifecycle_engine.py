"""
Per-user break-even ratchet + partial profit-taking for the multi-tenant
SaaS product -- US_STOCKS and CRYPTO only. This deliberately mirrors the
single-owner bot's ACTUAL scope, not a superset of it:
engines/position_lifecycle_engine.py is wired into app.py's
apply_risk_management() (stocks) and apply_crypto_risk_management()
(crypto) only -- there is no equivalent for eToro/FOREX/COMMODITIES on
the single-owner side either, so this module doesn't invent one for
SaaS.

Reuses engines/position_lifecycle_engine.py's pure decision functions
(should_activate_breakeven, effective_stop_loss_percent,
should_take_partial_profit, partial_profit_quantity, should_time_exit)
directly -- that module's math is broker-agnostic and already proven
live; only the STATE persistence differs here (a saas_platform.db table
keyed by user_id+ticker+broker instead of the single-owner's flat JSON
file keyed by "ASSET_CLASS:TICKER" string -- same concurrency reasoning
as engines/saas_etoro_trailing_engine.py's own state table: this runs
per-user, potentially with overlapping scheduler ticks, and a shared
flat file has no real concurrency guarantee).

REQUIRES the lot-based tracking added to engines/saas_order_manager.py
2026-08-27 (remaining_quantity, reduce_remaining_quantity(),
get_open_lot_for_user()) -- partial profit-taking sells only a FRACTION
of a lot, which is exactly what the OLD "most recent FILLED order side"
open/closed tracking could not survive (a partial SELL would become the
new "most recent" order and wrongly flip the position to closed).

BREAK-EVEN ENFORCEMENT NOTE: this module does not independently enforce
the break-even stop-loss -- it computes the new floor (entry price) and
writes it directly onto the original BUY order's stop_loss field via
saas_order_manager.update_stop_loss(). saas_exit_engine.py is what
actually checks price against stop_loss/take_profit every tick, so the
very next pass after break-even activates picks up the tightened floor
automatically through the SAME order row -- no separate enforcement
path to keep in sync, same "one source of truth" reasoning as the rest
of this journal.

TIME-EXIT OVERLAP NOTE: saas_exit_engine.py's own _decide_exit_reason()
already closes a position that's hit MAX_HOLD_DAYS_HARD (regardless of
P&L). This module's should_time_exit() call ALSO covers that same hard
tier (plus the SOFT MAX_HOLD_DAYS/flat-or-better tier saas_exit_engine.py
doesn't have). In real (dry_run=False) execution this never double-sells
-- whichever engine runs first in a given tick closes the position, and
list_open_tickers_for_user()/get_open_lot_for_user() then correctly show
it as no longer open for whichever engine runs second. In dry_run=True
preview mode the only side effect of the overlap is a cosmetic duplicate
"would sell" message for the hard-time-exit case specifically -- not a
correctness issue, not worth the complexity of splitting should_time_exit()
into two separate calls to avoid.

Not yet wired into saas_decision_engine.py's per-tick loop -- built and
smoke-tested standalone first, same deliberate-separate-step pattern
already used for engines/saas_etoro_trailing_engine.py.
"""

import sqlite3

from engines import saas_broker_factory as factory
from engines import saas_order_manager as journal
from engines.market_data_engine import get_market_data
from engines.position_lifecycle_engine import (
    should_activate_breakeven,
    should_take_partial_profit,
    partial_profit_quantity,
    should_time_exit,
)

DB_NAME = "saas_platform.db"

# FOLLOW-UP 2026-09-15 (task #365, Kraken): was a dict {asset_class:
# broker} -- CRYPTO now has two possible brokers (Binance or Kraken, see
# saas_broker_factory.py's KRAKEN section docstring), so a single dict
# can no longer say which broker(s) actually have open positions for a
# given user. Changed to a flat list of (asset_class, broker) pairs,
# same "check every broker regardless of which is currently preferred
# for new BUYs" reasoning saas_exit_engine.py's module docstring already
# established for its own _BROKERS list.
_ASSET_CLASS_BROKER_PAIRS = (
    ("US_STOCKS", "ALPACA"),
    ("CRYPTO", "BINANCE"),
    ("CRYPTO", "KRAKEN"),
    ("CRYPTO", "LUNO"),
)


def _get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS saas_position_lifecycle_state (
            user_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            broker TEXT NOT NULL,
            breakeven_active INTEGER NOT NULL DEFAULT 0,
            partial_taken INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            PRIMARY KEY (user_id, ticker, broker)
        )
    """)
    return conn


def _get_state(user_id, ticker, broker):
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT breakeven_active, partial_taken FROM saas_position_lifecycle_state "
            "WHERE user_id = ? AND ticker = ? AND broker = ?",
            (user_id, ticker, broker),
        ).fetchone()
        if row is None:
            return {"breakeven_active": False, "partial_taken": False}
        return {"breakeven_active": bool(row[0]), "partial_taken": bool(row[1])}
    finally:
        conn.close()


def _update_state(user_id, ticker, broker, **updates):
    state = _get_state(user_id, ticker, broker)
    state.update(updates)
    conn = _get_connection()
    try:
        conn.execute("""
            INSERT INTO saas_position_lifecycle_state
                (user_id, ticker, broker, breakeven_active, partial_taken, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id, ticker, broker) DO UPDATE SET
                breakeven_active = excluded.breakeven_active,
                partial_taken = excluded.partial_taken,
                updated_at = excluded.updated_at
        """, (user_id, ticker, broker, int(state["breakeven_active"]), int(state["partial_taken"])))
        conn.commit()
    finally:
        conn.close()


def clear_position_lifecycle_state(user_id, ticker, broker):
    """Call once a position is fully closed, so a future re-entry on the
    same ticker starts fresh rather than inheriting old breakeven/partial
    flags -- same reasoning as the single-owner bot's own
    clear_position_state()."""
    conn = _get_connection()
    try:
        conn.execute(
            "DELETE FROM saas_position_lifecycle_state WHERE user_id = ? AND ticker = ? AND broker = ?",
            (user_id, ticker, broker),
        )
        conn.commit()
    finally:
        conn.close()


def _get_current_price(ticker):
    """Same yfinance source as saas_exit_engine.py's own _get_current_price()
    -- duplicated rather than imported to keep these two modules
    independent of each other (neither imports from the other)."""
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


_STRANDED = "STRANDED"  # sentinel: real broker/wallet balance is confirmed
# zero, so this isn't a transient sell failure -- see _execute_sell's
# BINANCE branch and the FIX 2026-09-03 (#266) note there.


def _execute_sell(user_id, ticker, broker, requested_qty):
    """
    Executes a real SELL (callers must gate dry_run themselves) and
    returns {"quantity", "price"} for what ACTUALLY sold, the _STRANDED
    sentinel if the broker/wallet confirms zero real balance, or None on
    any other failure. Mirrors saas_exit_engine.py's own per-broker
    execution exactly, including the 2026-08-27 real-wallet-balance cap
    for crypto (the fix for the SOL-USD 'insufficient balance' failure)
    -- a partial-profit or time-exit sell here has the identical exposure
    to that same bug class if it blindly trusted the journal's quantity.
    Never raises.
    """
    try:
        if broker == "ALPACA":
            # FIX 2026-09-09 (found live, user 2aff7644, QQQ): this branch
            # blindly trusted the journal's requested_qty, unlike this
            # same function's BINANCE branch just below (task #266) and
            # unlike saas_exit_engine.py's own ALPACA branch (which caps
            # to a live Alpaca position query). Confirmed live: Alpaca
            # rejected a max-hold-time close with {"code":42210000,
            # "message":"fractional orders cannot be sold short"} --
            # Alpaca's error for "you're asking to sell more of this
            # fractional position than you actually hold", i.e. the exact
            # same journal-vs-broker drift class as everywhere else in
            # this codebase. Capping to the real held quantity here too,
            # and treating a confirmed-zero real position as _STRANDED
            # (already-closed-elsewhere) rather than attempting a doomed
            # sell -- mirrors the BINANCE branch's handling exactly, and
            # the caller below already knows how to reconcile _STRANDED
            # for either broker.
            try:
                real_positions = {
                    p["ticker"]: p["quantity"]
                    for p in factory.get_user_open_positions(user_id, "ALPACA")
                }
            except Exception:
                real_positions = {}
            real_qty = real_positions.get(ticker, 0)
            if real_qty <= 0:
                print(f"[saas_position_lifecycle_engine] {ticker} (ALPACA): Alpaca shows "
                      f"zero real shares held -- journal thinks {requested_qty} is open. "
                      f"Treating as stranded/already-closed-elsewhere.")
                return _STRANDED
            requested_qty = min(requested_qty, real_qty)

            alpaca_response = factory.sell_stock_for_user(user_id, ticker, requested_qty)
            response_status = str(getattr(alpaca_response, "status", "") or "").lower()
            # FIX 2026-09-09 (task #310, same class as the AMZN incident):
            # `"filled" not in response_status` also matches
            # "partially_filled" as a substring (it IS "filled" not-in
            # "partially_filled" evaluates False, so a partial fill fell
            # through to being treated as fully confirmed below, using
            # whatever partial filled_qty had accumulated at this single
            # snapshot). Exact match only -- a genuine partial fill now
            # correctly falls through to the "not confirmed, retry next
            # tick" path instead of being journaled as a complete sell
            # while a resting remainder at Alpaca goes untracked forever.
            if response_status != "filled":
                # Not confirmed synchronously -- rather than guess, treat
                # this pass as a no-op and let a future tick retry. This
                # module has no reconciliation pass of its own (unlike
                # BUY/exit-protection orders), so an unconfirmed partial/
                # time-exit sell here is simplest handled by just not
                # recording anything and trying again next tick.
                return None
            filled_price = getattr(alpaca_response, "filled_avg_price", None)
            filled_qty = getattr(alpaca_response, "filled_qty", None)
            return {
                "quantity": float(filled_qty) if filled_qty else requested_qty,
                "price": float(filled_price) if filled_price else None,
            }
        elif broker == "BINANCE":
            real_qty = factory.get_user_crypto_held_qty(user_id, ticker)
            if real_qty <= 0:
                # FIX 2026-09-03 (#266): this used to return None with NO
                # log line at all -- "return None" here never touches the
                # except block below, so the caller's "...see logs" error
                # message pointed at logs that had nothing in them, and
                # because nothing ever reconciled the journal, the exact
                # same ticker re-failed identically every single tick
                # forever (confirmed live: SOL-USD/XRP-USD/DOGE-USD/
                # XLM-USD/ETH-USD repeating every ~5 min for one user).
                # Same root cause class as saas_exit_engine.py's own
                # 2026-08-27 fix a few lines up in that file's BINANCE
                # branch (stranded/already-closed-elsewhere wallet vs.
                # stale journal quantity) -- that fix never got mirrored
                # here when this module was built. Now: log it plainly,
                # and hand the caller a distinguishable sentinel so it
                # reconciles the journal instead of retrying a doomed
                # sell forever.
                print(f"[saas_position_lifecycle_engine] {ticker} (BINANCE): wallet shows "
                      f"zero real balance -- journal thinks {requested_qty} is open. "
                      f"Treating as stranded/already-closed-elsewhere.")
                return _STRANDED
            actual_qty = min(requested_qty, real_qty)
            factory.sell_crypto_for_user(user_id, ticker, actual_qty)
            # Binance testnet market sells fill effectively synchronously
            # -- same established assumption as sell_crypto_for_user()'s
            # other callers. No separate fill price returned.
            return {"quantity": actual_qty, "price": None}
        elif broker == "KRAKEN":  # task #365 -- see saas_broker_factory.py's
            # KRAKEN section docstring: sell_kraken_for_user() calls
            # _require_kraken_live_trading_enabled() first, which raises
            # LiveTradingNotEnabledError (caught by the except block
            # below, same as any other broker failure here) if this
            # user's Lock 1 is off -- there is no demo/sandbox for Kraken
            # to silently fall back to instead.
            real_qty = factory.get_user_kraken_held_qty(user_id, ticker)
            if real_qty <= 0:
                # Same stranded-position reasoning as the BINANCE branch
                # above.
                print(f"[saas_position_lifecycle_engine] {ticker} (KRAKEN): wallet shows "
                      f"zero real balance -- journal thinks {requested_qty} is open. "
                      f"Treating as stranded/already-closed-elsewhere.")
                return _STRANDED
            actual_qty = min(requested_qty, real_qty)
            factory.sell_kraken_for_user(user_id, ticker, actual_qty)
            return {"quantity": actual_qty, "price": None}
        else:  # LUNO (task #378) -- same shape as KRAKEN above, see
            # saas_broker_factory.py's LUNO section docstring. quantity
            # here is always base-asset units (e.g. XBT), no currency
            # conversion needed for a sell -- only buy-side sizing needs
            # Luno's FX rate (see buy_luno_for_user()'s docstring).
            real_qty = factory.get_user_luno_held_qty(user_id, ticker)
            if real_qty <= 0:
                print(f"[saas_position_lifecycle_engine] {ticker} (LUNO): wallet shows "
                      f"zero real balance -- journal thinks {requested_qty} is open. "
                      f"Treating as stranded/already-closed-elsewhere.")
                return _STRANDED
            actual_qty = min(requested_qty, real_qty)
            factory.sell_luno_for_user(user_id, ticker, actual_qty)
            return {"quantity": actual_qty, "price": None}
    except Exception as e:
        print(f"[saas_position_lifecycle_engine] sell failed for {ticker} ({broker}): {e}")
        return None


def apply_position_lifecycle_for_user(user_id, dry_run=True):
    """
    For every open US_STOCKS/CRYPTO position this user holds: activates
    a break-even stop-loss floor once BREAKEVEN_STOP_TRIGGER_PERCENT
    profit is reached, takes PARTIAL_PROFIT_TAKE_FRACTION off the table
    once PARTIAL_PROFIT_TRIGGER_PERCENT profit is reached (one-time),
    and force-closes a position that's exceeded MAX_HOLD_DAYS_HARD (or
    MAX_HOLD_DAYS while flat-or-better) -- see module docstring for the
    full reasoning and the two important notes on break-even enforcement
    and the time-exit overlap with saas_exit_engine.py.

    dry_run=True (default): reports what WOULD happen, touches nothing.
    dry_run=False: actually raises the stop-loss floor and/or sells.

    Returns a list of result dicts ({"ticker", "asset_class", "action",
    "message", ...}). Never raises -- each ticker gets its own
    try/except so one bad position can't block the rest.
    """
    results = []

    for asset_class, broker in _ASSET_CLASS_BROKER_PAIRS:
        try:
            open_tickers = journal.list_open_tickers_for_user(user_id, broker)
        except Exception as e:
            results.append({
                "ticker": None, "asset_class": asset_class, "action": "error",
                "message": f"Could not load open {asset_class} positions: {e}",
            })
            continue

        for ticker in open_tickers:
            try:
                lot = journal.get_open_lot_for_user(user_id, ticker, broker)
                if lot is None:
                    continue

                remaining_qty = lot.get("remaining_quantity")
                remaining_qty = float(remaining_qty) if remaining_qty is not None else float(
                    lot.get("filled_quantity") or lot.get("quantity") or 0
                )
                if remaining_qty <= 0:
                    continue

                entry_price = float(lot.get("filled_price") or lot.get("price") or 0)
                if entry_price <= 0:
                    continue

                current_price = _get_current_price(ticker)
                if current_price is None:
                    results.append({
                        "ticker": ticker, "asset_class": asset_class, "action": "error",
                        "message": "Could not fetch current price -- skipped this pass.",
                    })
                    continue

                change_percent = ((current_price / entry_price) - 1) * 100
                state = _get_state(user_id, ticker, broker)

                # --- time-based exit (soft flat-or-better / hard
                # regardless-of-P&L) -- see module docstring's overlap
                # note. Checked first: if this fires, the position is
                # closing entirely, nothing else below applies.
                if should_time_exit(lot.get("created_at"), change_percent):
                    if dry_run:
                        results.append({
                            "ticker": ticker, "asset_class": asset_class, "action": "would_sell",
                            "quantity": remaining_qty,
                            "message": f"Would close {ticker} entirely -- max hold time "
                                       f"reached (change {change_percent:.2f}%).",
                        })
                    else:
                        sold = _execute_sell(user_id, ticker, broker, remaining_qty)
                        if sold == _STRANDED:
                            # FIX 2026-09-03 (#266): confirmed-zero real
                            # balance -- reconcile the journal to closed
                            # rather than reporting a generic failure and
                            # retrying the same doomed sell every tick.
                            # Mirrors saas_exit_engine.py's stranded-exit
                            # reconciliation.
                            results.append({
                                "ticker": ticker, "asset_class": asset_class, "action": "error",
                                "message": f"Max-hold-time close triggered for {ticker} but the "
                                           f"wallet shows zero held -- position may already be "
                                           f"closed outside this journal. Reconciling the journal "
                                           f"to closed rather than retrying a doomed sell.",
                            })
                            journal.reduce_remaining_quantity(lot["order_id"], remaining_qty)
                            clear_position_lifecycle_state(user_id, ticker, broker)
                        elif sold is None:
                            results.append({
                                "ticker": ticker, "asset_class": asset_class, "action": "error",
                                "message": f"Max-hold-time close failed for {ticker} -- see logs.",
                            })
                        else:
                            fill_price = sold["price"] or current_price
                            sell_order = journal.create_order(
                                user_id=user_id, ticker=ticker, side="SELL",
                                quantity=sold["quantity"], trade_amount=sold["quantity"] * fill_price,
                                price=fill_price, asset_class=asset_class, broker=broker,
                                strategy="MAX_HOLD_TIME_EXIT",
                                confidence=lot.get("confidence", 0), ai_trade_score=lot.get("ai_trade_score", 0),
                                priority=None,
                            )
                            sell_order = journal.mark_order_submitted(sell_order)
                            sell_order = journal.mark_order_filled(
                                sell_order, filled_price=fill_price, filled_quantity=sold["quantity"]
                            )
                            journal.save_order(sell_order)
                            journal.reduce_remaining_quantity(lot["order_id"], sold["quantity"])
                            clear_position_lifecycle_state(user_id, ticker, broker)
                            results.append({
                                "ticker": ticker, "asset_class": asset_class, "action": "sold",
                                "quantity": sold["quantity"],
                                "message": f"Closed {ticker} entirely @ {fill_price:.4f} -- "
                                           f"max hold time reached (change {change_percent:.2f}%).",
                            })
                    continue

                # --- break-even ratchet ---
                if should_activate_breakeven(change_percent, state["breakeven_active"]):
                    if dry_run:
                        results.append({
                            "ticker": ticker, "asset_class": asset_class, "action": "would_activate_breakeven",
                            "message": f"Would move {ticker}'s stop-loss to break-even "
                                       f"({entry_price}) -- profit reached {change_percent:.2f}%.",
                        })
                    else:
                        _update_state(user_id, ticker, broker, breakeven_active=True)
                        new_stop = round(entry_price, 4)
                        journal.update_stop_loss(lot["order_id"], new_stop)
                        lot["stop_loss"] = new_stop
                        state["breakeven_active"] = True
                        results.append({
                            "ticker": ticker, "asset_class": asset_class, "action": "breakeven_activated",
                            "message": f"{ticker}: profit reached {change_percent:.2f}% -- "
                                       f"stop-loss moved to break-even ({entry_price}).",
                        })

                # --- partial profit-taking ---
                if should_take_partial_profit(change_percent, state["partial_taken"]):
                    partial_qty = round(partial_profit_quantity(remaining_qty), 6)
                    if partial_qty <= 0:
                        continue

                    if dry_run:
                        results.append({
                            "ticker": ticker, "asset_class": asset_class, "action": "would_sell",
                            "quantity": partial_qty,
                            "message": f"Would take partial profit on {ticker}: sell "
                                       f"{partial_qty} (change {change_percent:.2f}%).",
                        })
                        continue

                    sold = _execute_sell(user_id, ticker, broker, partial_qty)
                    if sold == _STRANDED:
                        # FIX 2026-09-03 (#266): confirmed-zero real
                        # balance means the WHOLE position is already
                        # gone, not just this partial slice -- reconcile
                        # the entire remaining lot as closed (same as the
                        # max-hold-time branch above / saas_exit_engine.py's
                        # stranded-exit reconciliation) instead of
                        # reporting a generic failure and retrying the
                        # same doomed partial sell every tick forever.
                        results.append({
                            "ticker": ticker, "asset_class": asset_class, "action": "error",
                            "message": f"Partial profit-take triggered for {ticker} but the "
                                       f"wallet shows zero held -- position may already be "
                                       f"closed outside this journal. Reconciling the journal "
                                       f"to closed rather than retrying a doomed sell.",
                        })
                        journal.reduce_remaining_quantity(lot["order_id"], remaining_qty)
                        clear_position_lifecycle_state(user_id, ticker, broker)
                        continue
                    if sold is None:
                        results.append({
                            "ticker": ticker, "asset_class": asset_class, "action": "error",
                            "message": f"Partial profit-take failed for {ticker} -- see logs.",
                        })
                        continue

                    fill_price = sold["price"] or current_price
                    sell_order = journal.create_order(
                        user_id=user_id, ticker=ticker, side="SELL",
                        quantity=sold["quantity"], trade_amount=sold["quantity"] * fill_price,
                        price=fill_price, asset_class=asset_class, broker=broker,
                        strategy="PARTIAL_PROFIT",
                        confidence=lot.get("confidence", 0), ai_trade_score=lot.get("ai_trade_score", 0),
                        priority=None,
                    )
                    sell_order = journal.mark_order_submitted(sell_order)
                    sell_order = journal.mark_order_filled(
                        sell_order, filled_price=fill_price, filled_quantity=sold["quantity"]
                    )
                    journal.save_order(sell_order)
                    journal.reduce_remaining_quantity(lot["order_id"], sold["quantity"])
                    _update_state(user_id, ticker, broker, partial_taken=True)
                    results.append({
                        "ticker": ticker, "asset_class": asset_class, "action": "partial_profit_taken",
                        "quantity": sold["quantity"],
                        "message": f"Took partial profit on {ticker}: sold {sold['quantity']} "
                                   f"@ {fill_price:.4f} (change {change_percent:.2f}%).",
                    })
            except Exception as e:
                results.append({
                    "ticker": ticker, "asset_class": asset_class, "action": "error",
                    "message": f"Position-lifecycle check failed for {ticker}: {e}",
                })

    return results
