"""
Per-user AI decision loop for the multi-tenant SaaS product.

This is the piece every other SaaS module built so far (tenant_engine.py,
saas_broker_factory.py, saas_order_manager.py, and the extracted
engines/signal_engine.py) exists to support: given a user_id, generate
signals, run them through the exact same strategy/scoring/planning/
approval pipeline app.py uses for the single-owner bot, size any
approved BUY against THIS user's own real account balance, execute it
through their own connected broker, and journal the result -- all
without touching the single-owner bot's own state (positions,
trade_journal.db, session_state) anywhere.

SCOPE (deliberately narrow for a first version -- see project history
for why: every new integration in this codebase has shipped BUY-first/
manual-trigger/narrow-asset-class-first, then been widened once proven
live):

- All four asset classes now have real per-user execution: US_STOCKS
  (Alpaca), CRYPTO (Binance), and -- as of the 2026-08-26 follow-up --
  FOREX/COMMODITIES (eToro). See saas_broker_factory.py's module
  docstring for the eToro-specific gaps this brought along: no per-user
  equivalent of the single-owner bot's trailing-stop ratchet (eToro's
  own "trailing" flag is broker-side and unreliable -- see
  etoro_broker.py's 2026-08-24 comment), and no eToro reconciliation
  pass (a SUBMITTED-but-not-yet-confirmed eToro order stays that way
  until a future run's buy() poll happens to catch it on a DIFFERENT
  ticker's ticker -- there's no per-order retry for eToro the way
  saas_reconcile_engine.py provides for Alpaca).
- New entries are BUY-side only -- AI-generated SELL signals are still
  shown in results for visibility but never acted on. UPDATED 2026-08-26:
  existing positions DO now get automated exit protection -- see
  engines/saas_exit_engine.py, called at the top of this function,
  before new BUY signals -- but it's stop-loss/take-profit/hard-time-
  exit only (no break-even ratchet or partial profit-taking, unlike the
  single-owner bot's fuller position_lifecycle_engine.py; see that
  file's docstring for why). Still only runs when this function is
  called -- no scheduler yet -- so a position gets zero protection
  between Preview/Execute clicks. Treat as supervised-testing-only
  until a scheduler exists.
- Manually triggered only (called from a dashboard button in
  saas_app.py), never on a schedule -- there is no per-user background
  scheduler yet, and this is the same "prove it by hand first" pattern
  every other integration in this project followed before being trusted
  to run unattended.

POSITION TRACKING is intentionally simple: saas_order_manager.
has_open_position_for_user()/count_open_positions_for_user() just look
at whichever FILLED order for a ticker was most recent (BUY = open,
SELL = closed). There's no FIFO lot tracking, no partial fills, no
lifecycle engine. Good enough to stop this loop from pyramiding the same
ticker or blowing past a position-count cap; not a substitute for real
position management once SELL-side automation exists.

FIX 2026-08-26 (found live, same run this was first tested): the
US_STOCKS execution branch originally journaled every Alpaca BUY as
FILLED immediately using the AI signal's price as the fill price,
without checking Alpaca's actual order status first. Confirmed live: 5
orders (SPY/QQQ/DIA/IWM/AAPL) were still sitting at status "new" on the
real Alpaca dashboard, seconds after this journal had already recorded
them as FILLED with a fabricated price/quantity. This is the exact same
"optimistic order-fill logging" bug already found and fixed for the
single-owner bot's execute_alpaca_trades() (see that function's matching
comment) -- this file just hadn't inherited the fix. Now only a response
status containing "filled" is trusted; anything else stays SUBMITTED
with the real broker_order_id recorded, and the result action is
"submitted" rather than "bought" so the UI can say so honestly. CRYPTO
is unaffected -- buy_crypto_for_user() mirrors binance_broker.py's own
buy_crypto(), which has always treated Binance testnet market orders as
filled immediately (they fill near-synchronously in practice), matching
established production behavior rather than a new assumption.

The reconciliation gap that fix initially left open (a SUBMITTED order
that fills later would stay SUBMITTED forever) is now closed -- see
engines/saas_reconcile_engine.py, called at the top of the US_STOCKS
branch below.

RISK GATES applied per candidate, in order: asset class enabled for this
user -> broker connected -> user has a nonzero real balance -> signal is
BUY -> passes the existing strategy/scoring/approval pipeline unchanged
-> not already holding this ticker (SaaS journal) -> under this user's
per-asset-class open-position cap (MAX_POSITIONS/MAX_CRYPTO_POSITIONS,
same constants the single-owner bot uses, applied per user) -> under
this user's daily-trade-limit and per-ticker cooldown (same constants,
scoped to this user's own saas_orders rows) -> calculate_trade_amount()
returns a nonzero size (it returns 0.0 itself if the account can't cover
MIN_TRADE_AMOUNT -- see risk_engine.py). Any failure at any stage is
recorded in the returned results list with a human-readable reason;
nothing here raises out to the caller for an individual ticker's
failure, so one bad ticker/API hiccup can never stop the rest of the
user's run.

FIX 2026-08-26 (follow-up): the reconciliation gap described above is
now closed for Alpaca -- see engines/saas_reconcile_engine.py, called at
the top of the US_STOCKS branch below, before position counts/caps are
read. Any order still sitting at SUBMITTED from a previous run gets
checked against Alpaca first, so a since-filled order is correctly
counted as open (and its real fill price/quantity backfilled) before
this run decides whether there's room for a new one. CRYPTO doesn't
need this (see that file's docstring); eToro now has per-user execution
(FOLLOW-UP 2026-08-26, see saas_broker_factory.py) but still no
reconciliation pass of its own -- see that file's docstring for why.

NOT included: portfolio-level exposure cap (MAX_PORTFOLIO_EXPOSURE) --
the single-owner bot's version of this reads live equity across all
positions, which would need a per-user equivalent of
calculate_portfolio_value()/get_exposure_percent() that doesn't exist
yet. The single-trade budget cap already inside calculate_trade_amount()
(account_balance * MAX_POSITION_SIZE per trade) plus the per-asset-class
position-count cap are the real safety rails for this version. Flagged
here so this isn't mistaken for full parity with the single-owner bot's
risk_engine.py.
"""

from datetime import datetime, timedelta

import joblib

from engines.signal_engine import get_ai_signal
from engines.strategy_engine import identify_strategy, score_strategy
from engines.trade_planner import create_trade_plan
from engines.scoring_engine import calculate_trade_score
from engines.approval_engine import approve_trade
from engines.risk_engine import calculate_trade_amount
from engines import tenant_engine as tenant
from engines import saas_broker_factory as factory
from engines import saas_order_manager as journal
from engines import saas_reconcile_engine as reconcile
from engines import saas_exit_engine as exit_engine
from engines import saas_emergency_stop
from data.asset_universe import ASSET_UNIVERSE

from config import (
    MAX_POSITIONS,
    MAX_CRYPTO_POSITIONS,
    MAX_FOREX_POSITIONS,
    MAX_COMMODITIES_POSITIONS,
    MAX_TRADES_PER_DAY,
    TRADE_COOLDOWN_MINUTES,
    CRYPTO_MAX_TRADES_PER_DAY,
    CRYPTO_TRADE_COOLDOWN_MINUTES,
)

MODEL_PATH = "models/trading_model.pkl"
FEATURES_PATH = "models/features.pkl"

# All four asset classes now have real per-user execution (eToro
# follow-up landed 2026-08-26 -- see saas_broker_factory.py's module
# docstring for the known gaps: no trailing-lock ratchet, no exit-engine
# coverage for FOREX/COMMODITIES yet).
_ASSET_CLASS_BROKER = {
    "US_STOCKS": "ALPACA",
    "CRYPTO": "BINANCE",
    "FOREX": "ETORO",
    "COMMODITIES": "ETORO",
}

_POSITION_CAPS = {
    "CRYPTO": MAX_CRYPTO_POSITIONS,
    "FOREX": MAX_FOREX_POSITIONS,
    "COMMODITIES": MAX_COMMODITIES_POSITIONS,
}

_model = None
_features = None


def _load_model():
    """Lazy singleton load -- same joblib files app.py loads, just not
    re-read from disk on every call within one process."""
    global _model, _features
    if _model is None or _features is None:
        _model = joblib.load(MODEL_PATH)
        _features = joblib.load(FEATURES_PATH)
    return _model, _features


def _trades_today_and_last_trade_time(user_id, ticker, asset_class, broker):
    """Per-user equivalent of risk_engine.risk_check_before_trade()'s
    daily-trade-count and per-ticker-cooldown logic, scoped to this
    user's own saas_orders rows instead of the single-owner bot's
    trade_journal.db."""
    orders = journal.load_orders_for_user(user_id, limit=500)

    today = datetime.now().date()
    trades_today = 0
    last_trade_for_ticker = None

    for order in orders:
        if str(order.get("broker", "")).upper() != broker:
            continue

        timestamp_text = order.get("updated_at") or order.get("created_at")
        try:
            ts = datetime.fromisoformat(timestamp_text) if timestamp_text else None
        except Exception:
            ts = None

        if str(order.get("asset_class", "")).upper() == asset_class and ts is not None:
            if ts.date() == today:
                trades_today += 1

        if str(order.get("ticker", "")).upper().strip() == ticker.upper().strip():
            if ts is not None and (last_trade_for_ticker is None or ts > last_trade_for_ticker):
                last_trade_for_ticker = ts

    return trades_today, last_trade_for_ticker


def _daily_limit_or_cooldown_hit(user_id, ticker, asset_class, broker):
    trades_today, last_trade_for_ticker = _trades_today_and_last_trade_time(
        user_id, ticker, asset_class, broker
    )

    max_trades = (
        CRYPTO_MAX_TRADES_PER_DAY if asset_class == "CRYPTO" else MAX_TRADES_PER_DAY
    )
    cooldown_minutes = (
        CRYPTO_TRADE_COOLDOWN_MINUTES if asset_class == "CRYPTO" else TRADE_COOLDOWN_MINUTES
    )

    if trades_today >= max_trades:
        return True, f"Maximum daily trades reached for {asset_class}."

    if last_trade_for_ticker is not None:
        elapsed = datetime.now() - last_trade_for_ticker
        if elapsed < timedelta(minutes=cooldown_minutes):
            return True, "Cooldown active for this ticker."

    return False, ""


def run_decision_loop_for_user(user_id, dry_run=True):
    """
    Runs the full signal -> approval -> sizing -> (optionally) execution
    pipeline for one user, across US_STOCKS and CRYPTO.

    dry_run=True (the default): generates signals and runs every gate
    including sizing, but never calls a broker or writes to the journal
    -- a safe "preview what would happen" pass. dry_run=False actually
    places BUY orders through saas_broker_factory and journals them via
    saas_order_manager. Callers (the SaaS dashboard) should always run
    dry_run=True first and show the user what would happen before
    offering a separate, explicit "confirm and execute" action that
    calls this again with dry_run=False -- same manual-confirm-first
    pattern this project has used for every previous broker integration.

    Returns a list of per-ticker result dicts, always including
    "ticker", "asset_class", "action" (one of: "skipped", "rejected",
    "would_buy", "bought", "error") and "message". Never raises for an
    individual ticker's failure -- see module docstring.
    """
    results = []

    settings = tenant.get_user_settings(user_id) or {}
    enabled_classes = set(settings.get("enabled_asset_classes") or [])

    # tenant.list_connected_brokers() returns a list of credential-status
    # dicts (each with a "broker" key, plus "environment"/"updated_at" --
    # see its usage in saas_app.py's render_broker_connections()), NOT a
    # list of plain broker-code strings. Extracting the codes here rather
    # than assuming the shape -- this was caught live: the first version
    # of this function did `set(tenant.list_connected_brokers(user_id))`
    # directly, which crashed with "cannot use 'dict' as a set element"
    # the moment a real user with saved credentials clicked Preview.
    connected_brokers = {
        c["broker"] for c in tenant.list_connected_brokers(user_id)
    }

    # Exit protection runs first and for BOTH connected brokers
    # regardless of which asset classes are currently enabled for new
    # BUYs -- disabling future buying on an asset class shouldn't strand
    # an already-open position without stop-loss/take-profit/time-exit
    # coverage. See saas_exit_engine.py for what this does and doesn't
    # cover (full-exit only, no partial profit-taking yet). Same
    # dry_run gating as the BUY side -- Preview never touches the
    # broker, Execute does.
    if "ALPACA" in connected_brokers or "BINANCE" in connected_brokers:
        exit_results = exit_engine.check_and_apply_exits_for_user(user_id, dry_run=dry_run)
        results.extend(exit_results)

    # Kill switches -- both block new BUY evaluation only, never the
    # exit protection above (matches the single-owner bot's
    # EXECUTION_KILL_SWITCH semantics: block new entries, keep
    # protective exits running). Checked in this order: platform-wide
    # first (an operator halt should always win), then this user's own
    # pause toggle. Returns immediately rather than continuing into the
    # per-asset-class loop, since there's nothing left to evaluate.
    if saas_emergency_stop.is_stopped():
        reason = saas_emergency_stop.get_reason()
        results.append({
            "ticker": None,
            "asset_class": None,
            "action": "skipped",
            "message": f"Platform-wide trading halt is active"
                       f"{f' ({reason})' if reason else ''} -- no new BUYs "
                       f"will be evaluated until it's lifted.",
        })
        return results

    if settings.get("trading_paused"):
        results.append({
            "ticker": None,
            "asset_class": None,
            "action": "skipped",
            "message": "Your trading is paused (see Settings) -- no new BUYs "
                       "will be evaluated until you resume.",
        })
        return results

    try:
        model, features = _load_model()
    except Exception as e:
        return [{
            "ticker": None,
            "asset_class": None,
            "action": "error",
            "message": f"Could not load AI model: {e}",
        }]

    for asset_class, broker in _ASSET_CLASS_BROKER.items():
        if asset_class not in enabled_classes:
            continue

        if broker not in connected_brokers:
            results.append({
                "ticker": None,
                "asset_class": asset_class,
                "action": "skipped",
                "message": f"{broker.title()} not connected for this user.",
            })
            continue

        # Catch up any orders from a previous run that submitted but
        # hadn't confirmed filled yet -- must happen before open_count/
        # has_open_position_for_user checks below, otherwise a since-
        # filled order would still read as "not open" this run. Alpaca
        # only (see saas_reconcile_engine.py's docstring for why CRYPTO
        # doesn't need this and eToro isn't included yet).
        if broker == "ALPACA":
            reconcile_results = reconcile.reconcile_user_alpaca_orders(user_id)
            for r in reconcile_results:
                results.append({
                    "ticker": r["ticker"],
                    "asset_class": asset_class,
                    "action": "reconciled",
                    "message": r["message"],
                })

        balance = factory.get_user_account_balance(user_id, asset_class)
        if balance <= 0:
            results.append({
                "ticker": None,
                "asset_class": asset_class,
                "action": "skipped",
                "message": f"No usable {broker.title()} balance detected -- "
                           f"cannot size any trade.",
            })
            continue

        open_count = journal.count_open_positions_for_user(user_id, broker)
        position_cap = _POSITION_CAPS.get(asset_class, MAX_POSITIONS)

        tickers = ASSET_UNIVERSE.get(asset_class, {}).get("symbols", [])

        for ticker in tickers:
            try:
                row = get_ai_signal(ticker, model, features)
            except Exception as e:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "error",
                    "message": f"Signal generation failed: {e}",
                })
                continue

            row["Asset Class"] = asset_class
            row["Broker"] = broker.lower()

            if row.get("Signal") != "BUY":
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "skipped",
                    "message": f"Not a BUY signal ({row.get('Signal', 'HOLD')}); "
                               f"SELL-side automation not built yet, see module docstring.",
                })
                continue

            row["Strategy"] = identify_strategy(row)
            row["Strategy Score"] = score_strategy(row)

            plan = create_trade_plan(row)
            row.update(plan)

            row["AI Trade Score"] = calculate_trade_score(row)

            approved, reason = approve_trade(row, open_positions_count=0)
            if not approved:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "rejected",
                    "message": reason,
                })
                continue

            if journal.has_open_position_for_user(user_id, ticker, broker):
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "skipped",
                    "message": "Already holding this ticker (per SaaS journal).",
                })
                continue

            if open_count >= position_cap:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "skipped",
                    "message": f"Maximum {asset_class} positions reached ({position_cap}).",
                })
                continue

            limit_hit, limit_reason = _daily_limit_or_cooldown_hit(
                user_id, ticker, asset_class, broker
            )
            if limit_hit:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "skipped",
                    "message": limit_reason,
                })
                continue

            trade_amount = calculate_trade_amount(
                confidence=row["AI Confidence %"],
                entry_price=row.get("Price ($)"),
                stop_loss=row.get("Stop Loss"),
                leverage=1,
                account_balance=balance,
            )
            if trade_amount <= 0:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "skipped",
                    "message": "Balance too small to cover the minimum trade size.",
                })
                continue

            if dry_run:
                # Count this toward the cap even though nothing was
                # actually bought, so a preview with multiple approved
                # tickers correctly shows later ones as blocked by the
                # position cap once earlier ones would fill it --
                # otherwise a dry run would overstate how many of these
                # could really be bought in one pass.
                open_count += 1
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "would_buy",
                    "trade_amount": trade_amount,
                    "confidence": row["AI Confidence %"],
                    "trade_grade": row.get("Trade Grade"),
                    "risk_reward": row.get("Risk Reward"),
                    "message": f"Would BUY ${trade_amount:.2f} of {ticker}.",
                })
                continue

            estimated_price = float(row["Price ($)"])
            estimated_quantity = trade_amount / estimated_price if estimated_price > 0 else 0

            broker_order_id = None
            is_confirmed_filled = False
            filled_price = estimated_price
            filled_quantity = estimated_quantity

            try:
                if asset_class == "US_STOCKS":
                    alpaca_response = factory.buy_stock_for_user(user_id, ticker, trade_amount)
                    broker_order_id = str(getattr(alpaca_response, "id", "") or "") or None
                    response_status = str(getattr(alpaca_response, "status", "") or "").lower()

                    # Alpaca's response right after submit_order() usually
                    # still reads "accepted"/"pending_new" even when the
                    # real fill happens moments later -- only an explicit
                    # "filled" status is trustworthy. Same fix already
                    # applied to the single-owner bot's execute_alpaca_
                    # trades() after a live incident where a market-closed
                    # order was logged FILLED here while Alpaca itself
                    # still showed it pending. See buy_stock_for_user()'s
                    # docstring for why this check has to live here, not
                    # inside that function.
                    if "filled" in response_status:
                        is_confirmed_filled = True
                        response_filled_price = getattr(alpaca_response, "filled_avg_price", None)
                        response_filled_qty = getattr(alpaca_response, "filled_qty", None)
                        if response_filled_price:
                            filled_price = float(response_filled_price)
                        if response_filled_qty:
                            filled_quantity = float(response_filled_qty)
                elif asset_class == "CRYPTO":
                    # Binance testnet market orders fill effectively
                    # synchronously -- same assumption the single-owner
                    # bot's own binance_broker.buy_crypto() already makes
                    # (see that file), so treating this as filled
                    # immediately matches established, already-live
                    # behavior rather than introducing a new one.
                    is_confirmed_filled = True
                    _order, filled_price, filled_quantity = factory.buy_crypto_for_user(
                        user_id, ticker, trade_amount
                    )
                else:
                    # FOREX/COMMODITIES via eToro. buy_etoro_for_user()
                    # already polls for a confirmed fill internally (15s
                    # window for leveraged CFDs -- see that function's
                    # docstring); position_id is None if that window
                    # elapses without a confirmed match, same "stays
                    # SUBMITTED, not falsely marked bought" discipline as
                    # the US_STOCKS branch above, just with the polling
                    # already done rather than deferred to a reconcile
                    # pass (none exists yet for eToro -- see
                    # saas_broker_factory.py's module docstring).
                    #
                    # broker_order_id stores the eToro POSITION id here,
                    # not the order id -- deliberate: eToro's own
                    # close-position endpoint needs position_id, not
                    # order_id, so that's the identifier worth keeping
                    # for any future SELL/reconciliation support. The
                    # order_id itself has no further use once a position
                    # is confirmed open.
                    etoro_result = factory.buy_etoro_for_user(user_id, ticker, trade_amount)
                    if etoro_result["position_id"] is not None:
                        is_confirmed_filled = True
                        broker_order_id = str(etoro_result["position_id"])
                        if etoro_result["executed_price"]:
                            filled_price = float(etoro_result["executed_price"])
                            filled_quantity = trade_amount / filled_price if filled_price > 0 else estimated_quantity
            except Exception as e:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "error",
                    "message": f"Broker execution failed: {e}",
                })
                continue

            saas_order = journal.create_order(
                user_id=user_id,
                ticker=ticker,
                side="BUY",
                quantity=filled_quantity,
                trade_amount=trade_amount,
                price=filled_price,
                asset_class=asset_class,
                broker=broker,
                strategy=row.get("Strategy"),
                confidence=row["AI Confidence %"],
                ai_trade_score=row["AI Trade Score"],
                priority=None,
                stop_loss=row.get("Stop Loss"),
                take_profit=row.get("Take Profit"),
            )
            saas_order = journal.mark_order_submitted(saas_order, broker_order_id=broker_order_id)

            if is_confirmed_filled:
                saas_order = journal.mark_order_filled(
                    saas_order, filled_price=filled_price, filled_quantity=filled_quantity
                )
            journal.save_order(saas_order)

            # Only count toward the open-position cap once actually
            # confirmed filled -- an order still sitting at "new"/
            # "accepted" on Alpaca hasn't opened a position yet, and
            # has_open_position_for_user()/count_open_positions_for_user()
            # only look at status='FILLED' rows (see saas_order_manager.py).
            if is_confirmed_filled:
                open_count += 1

            if is_confirmed_filled:
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "bought",
                    "trade_amount": trade_amount,
                    "filled_price": filled_price,
                    "filled_quantity": filled_quantity,
                    "message": f"Bought {filled_quantity:.6f} {ticker} @ {filled_price:.4f} "
                               f"(${trade_amount:.2f}).",
                })
            else:
                # KNOWN GAP: there is no per-user equivalent of the
                # single-owner bot's reconcile_alpaca_orders() yet, so an
                # order that submits as "new"/"accepted" and fills a
                # moment later will stay SUBMITTED in this journal
                # forever -- its real fill price/quantity never gets
                # recorded, and has_open_position_for_user() will keep
                # saying "not open" even after it fills, risking a
                # duplicate buy on a future run. Flagging rather than
                # building the reconcile pass now; needed before this is
                # trusted for anything beyond supervised testing.
                results.append({
                    "ticker": ticker,
                    "asset_class": asset_class,
                    "action": "submitted",
                    "trade_amount": trade_amount,
                    "message": f"Order for {ticker} submitted to {broker.title()} but not yet "
                               f"confirmed filled (status pending) -- no per-user reconciliation "
                               f"exists yet, check your broker directly to confirm.",
                })

    return results
