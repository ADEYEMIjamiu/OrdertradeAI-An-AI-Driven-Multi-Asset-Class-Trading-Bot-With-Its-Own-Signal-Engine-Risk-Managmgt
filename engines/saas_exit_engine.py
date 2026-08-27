"""
SELL-side exit protection for the multi-tenant SaaS product.

Every other piece of the SaaS decision loop (saas_decision_engine.py) is
BUY-side only -- by design, but that design left a real gap: a position
bought through it has no automated way to close. This file is that
missing piece: for each user's currently-open SaaS positions, checks
whether the stop-loss/take-profit levels set at entry (trade_planner.py's
ATR-based plan, stored on the original BUY order) have been hit, or the
position has been open too long, and closes it if so.

SCOPE (deliberately narrower than the single-owner bot's own exit logic
in app.py / engines/position_lifecycle_engine.py):

- Hard stop-loss, hard take-profit, and MAX_HOLD_DAYS_HARD time-based
  exit only. NOT included: break-even stop ratchet, partial profit-
  taking, or the soft MAX_HOLD_DAYS (flat-or-better) exit --
  position_lifecycle_engine.py's break-even/partial-profit logic
  requires quantity-aware position tracking (knowing exactly how much of
  a position remains after a partial sell). This SaaS journal's open/
  closed tracking (saas_order_manager.has_open_position_for_user) is
  intentionally simple -- "which side was the most recent FILLED order
  for this ticker" -- and a partial-profit SELL would corrupt that: it
  would become the new "most recent" order and incorrectly flip the
  ticker to closed even though most of the position is still open. Full-
  exit-only sidesteps that entirely. Building partial-exit support would
  need real lot/quantity tracking first, not a small addition.
- Same manual-confirm-first pattern as the BUY side: dry_run=True
  previews what would be sold and why without touching the broker;
  dry_run=False actually sells. Deliberately does NOT run automatically
  in the background -- there is no scheduler yet (see roadmap), so a
  position between Preview/Execute clicks gets zero protection in the
  meantime. This is a real gap, not an oversight: treat this as
  supervised-testing-only until a scheduler exists to run it
  unattended.

FOLLOW-UP 2026-08-26: FOREX/COMMODITIES (eToro) added below now that
sell_etoro_for_user() exists in saas_broker_factory.py. Both asset
classes map to the same ETORO broker, so the outer loop dedupes by
broker (processed_brokers) to avoid processing the same open eToro
tickers twice under two different labels -- each ticker's real
asset_class is read back off its own entry_order instead of trusted
from the outer loop variable. An eToro position can only be closed by
its position_id (stored as broker_order_id on the BUY once eToro
confirmed the fill, either at buy time or via
reconcile_user_etoro_orders()) -- a position with no broker_order_id on
record yet is skipped with an error rather than guessed at, same
"never fabricate an unconfirmed state" discipline as the rest of this
file. eToro's close-position endpoint is synchronous (confirmed by
etoro_broker.close_position()'s own live-testing history), so an eToro
exit is always treated as immediately filled, same as the Binance
branch.
"""

from datetime import datetime

from engines.market_data_engine import get_market_data
from engines import saas_broker_factory as factory
from engines import saas_order_manager as journal

from config import MAX_HOLD_DAYS_HARD

_ASSET_CLASS_BROKER = {
    "US_STOCKS": "ALPACA",
    "CRYPTO": "BINANCE",
    "FOREX": "ETORO",
    "COMMODITIES": "ETORO",
}


def _get_current_price(ticker):
    """Latest close, same yfinance source signal_engine.py's prepare_data()
    uses -- deliberately not the AI model's feature-engineered data,
    just a plain current price for comparing against stored stop/take
    levels. Returns None (never raises) if data isn't available; callers
    skip that ticker for this pass rather than crash the whole run."""
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


def _decide_exit_reason(entry_order, current_price):
    """Returns a human-readable reason string if this position should be
    closed, or None if it should stay open. Pure decision logic, no
    broker/journal calls -- mirrors the engines-decide/caller-executes
    separation position_lifecycle_engine.py already uses."""
    stop_loss = entry_order.get("stop_loss")
    take_profit = entry_order.get("take_profit")

    if stop_loss is not None:
        try:
            if current_price <= float(stop_loss):
                return f"Stop-loss hit ({current_price:.4f} <= {float(stop_loss):.4f})"
        except (TypeError, ValueError):
            pass

    if take_profit is not None:
        try:
            if current_price >= float(take_profit):
                return f"Take-profit hit ({current_price:.4f} >= {float(take_profit):.4f})"
        except (TypeError, ValueError):
            pass

    opened_at = entry_order.get("created_at")
    if opened_at:
        try:
            opened_dt = datetime.fromisoformat(opened_at)
            days_open = (datetime.now() - opened_dt).total_seconds() / 86400
            if days_open >= MAX_HOLD_DAYS_HARD:
                return f"Max hold time exceeded ({days_open:.1f} days >= {MAX_HOLD_DAYS_HARD})"
        except (TypeError, ValueError):
            pass

    return None


def check_and_apply_exits_for_user(user_id, dry_run=True):
    """
    Checks every currently-open position for this user (US_STOCKS,
    CRYPTO, FOREX, COMMODITIES) against its stored stop-loss/take-profit/
    max-hold-time, and closes (or previews closing) any that have
    triggered.

    dry_run=True (default): reports what WOULD be sold and why, touches
    nothing. dry_run=False: actually sells via saas_broker_factory and
    journals the SELL, same confirmed-fill discipline as the BUY side
    (see saas_decision_engine.py's 2026-08-26 fix -- an Alpaca SELL that
    doesn't come back "filled" immediately stays SUBMITTED, not
    fabricated as sold).

    Returns a list of result dicts: {"ticker", "asset_class", "action"
    (one of "would_sell", "sold", "submitted", "error"), "message"}.
    An empty list means nothing needed closing. Never raises for an
    individual ticker's failure.
    """
    results = []
    processed_brokers = set()

    for asset_class, broker in _ASSET_CLASS_BROKER.items():
        if broker in processed_brokers:
            # FOREX and COMMODITIES both map to ETORO -- without this
            # guard the same open eToro tickers would be fetched and
            # potentially closed twice per pass, once under each label.
            continue
        processed_brokers.add(broker)

        open_tickers = journal.list_open_tickers_for_user(user_id, broker)

        for ticker in open_tickers:
            entry_order = journal.get_most_recent_filled_buy_for_user(user_id, ticker, broker)
            if entry_order is None:
                continue  # shouldn't happen if list_open_tickers_for_user is consistent, but never crash over it

            # eToro tickers span two asset classes under one broker --
            # trust the order's own recorded asset_class for reporting,
            # not the outer loop variable (which is only correct for the
            # first of the two labels once deduped above).
            asset_class = entry_order.get("asset_class", asset_class)

            current_price = _get_current_price(ticker)
            if current_price is None:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "error",
                    "message": "Could not fetch current price -- skipped this pass.",
                })
                continue

            exit_reason = _decide_exit_reason(entry_order, current_price)
            if exit_reason is None:
                continue

            quantity = float(entry_order.get("filled_quantity") or entry_order.get("quantity") or 0)
            if quantity <= 0:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "error",
                    "message": f"Exit triggered ({exit_reason}) but no valid quantity on record -- skipped.",
                })
                continue

            if dry_run:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "would_sell",
                    "quantity": quantity,
                    "message": f"Would SELL {quantity} {ticker} -- {exit_reason}.",
                })
                continue

            is_confirmed_filled = False
            filled_price = current_price
            broker_order_id = None

            try:
                if broker == "ALPACA":
                    alpaca_response = factory.sell_stock_for_user(user_id, ticker, quantity)
                    broker_order_id = str(getattr(alpaca_response, "id", "") or "") or None
                    response_status = str(getattr(alpaca_response, "status", "") or "").lower()

                    if "filled" in response_status:
                        is_confirmed_filled = True
                        response_filled_price = getattr(alpaca_response, "filled_avg_price", None)
                        if response_filled_price:
                            filled_price = float(response_filled_price)
                elif broker == "ETORO":
                    position_id = entry_order.get("broker_order_id")
                    if not position_id:
                        results.append({
                            "ticker": ticker,
                            "asset_class": asset_class,
                            "action": "error",
                            "message": f"Exit triggered ({exit_reason}) but no confirmed eToro "
                                       f"position_id on record yet -- will retry once "
                                       f"reconciliation catches the BUY up.",
                        })
                        continue
                    # eToro's close-position endpoint is synchronous --
                    # see module docstring / sell_etoro_for_user()'s own
                    # docstring for the live-tested history behind that.
                    is_confirmed_filled = True
                    broker_order_id = str(position_id)
                    factory.sell_etoro_for_user(user_id, position_id)
                    # No separate close price returned -- use the price
                    # already fetched above for the exit decision, same
                    # as the Binance branch.
                else:
                    # FIX 2026-08-27: `quantity` above comes from the
                    # ORIGINAL BUY order's journaled filled_quantity,
                    # which can drift from the wallet's real current
                    # balance -- confirmed live: a SOL-USD take-profit
                    # exit failed with "Account has insufficient balance
                    # for requested action" because the journal's
                    # quantity exceeded what was actually held. Same
                    # class of bug the single-owner bot already avoids
                    # (app.py's crypto risk-management SELL path sizes
                    # off a live binance_broker.get_positions() wallet
                    # query, not its own order history) -- mirrored here
                    # via a fresh wallet balance check right before the
                    # sell, capping to whatever's actually held rather
                    # than trusting the journal blindly.
                    real_qty = factory.get_user_crypto_held_qty(user_id, ticker)
                    if real_qty <= 0:
                        results.append({
                            "ticker": ticker,
                            "asset_class": asset_class,
                            "action": "error",
                            "message": f"Exit triggered ({exit_reason}) but "
                                       f"the wallet shows zero {ticker} "
                                       f"held -- position may already be "
                                       f"closed outside this journal. "
                                       f"Skipped to avoid a doomed sell; "
                                       f"investigate if this persists.",
                        })
                        continue
                    quantity = min(quantity, real_qty)

                    # Binance testnet market sells fill effectively
                    # synchronously -- same established assumption as
                    # buy_crypto_for_user()/binance_broker.py's own
                    # buy_crypto(), not a new one introduced here.
                    is_confirmed_filled = True
                    factory.sell_crypto_for_user(user_id, ticker, quantity)
                    # No separate fill price returned by sell_crypto_for_user
                    # -- use the price already fetched above for the exit
                    # decision, same source, seconds apart.
            except Exception as e:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "error",
                    "message": f"Broker sell failed ({exit_reason}): {e}",
                })
                continue

            sell_order = journal.create_order(
                user_id=user_id,
                ticker=ticker,
                side="SELL",
                quantity=quantity,
                trade_amount=quantity * filled_price,
                price=filled_price,
                asset_class=asset_class,
                broker=broker,
                strategy="EXIT_PROTECTION",
                confidence=entry_order.get("confidence", 0),
                ai_trade_score=entry_order.get("ai_trade_score", 0),
                priority=None,
            )
            sell_order = journal.mark_order_submitted(sell_order, broker_order_id=broker_order_id)
            if is_confirmed_filled:
                sell_order = journal.mark_order_filled(
                    sell_order, filled_price=filled_price, filled_quantity=quantity
                )
            journal.save_order(sell_order)

            if is_confirmed_filled:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "sold",
                    "quantity": quantity,
                    "filled_price": filled_price,
                    "message": f"Sold {quantity} {ticker} @ {filled_price:.4f} -- {exit_reason}.",
                })
            else:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "submitted",
                    "quantity": quantity,
                    "message": f"SELL for {ticker} submitted ({exit_reason}) but not yet "
                               f"confirmed filled -- will be caught up by reconciliation.",
                })

    return results
