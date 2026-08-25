import streamlit as st
from datetime import datetime, timedelta

from broker import get_account, get_open_positions
from broker import broker
from engines.regime_engine import get_market_regime, get_market_risk_level
from engines.order_manager import load_orders

from config import (
    LIVE_TRADING,
    ETORO_LIVE_TRADING,
    MIN_TRADE_AMOUNT,
    MAX_TRADE_AMOUNT,
    MAX_POSITION_SIZE,
    STOP_LOSS_PERCENT,
    ALLOW_PYRAMIDING,
    MAX_POSITIONS,
    MAX_OPEN_POSITIONS,
    MAX_CRYPTO_POSITIONS,
    MAX_FOREX_POSITIONS,
    MAX_COMMODITIES_POSITIONS,
    MAX_PORTFOLIO_EXPOSURE,
    MAX_TRADES_PER_DAY,
    TRADE_COOLDOWN_MINUTES,
    CRYPTO_MAX_TRADES_PER_DAY,
    CRYPTO_TRADE_COOLDOWN_MINUTES,
    HIGH_SCORE_SIZE_MULTIPLIER,
    NORMAL_SCORE_SIZE_MULTIPLIER,
    LOW_SCORE_SIZE_MULTIPLIER,
)
from data.asset_universe import ASSET_UNIVERSE

# Bounds for the leverage/stop-aware risk_adjustment applied in
# calculate_trade_amount() below. Kept as a floor/ceiling (rather than
# left unbounded) for the same reason every other multiplier in this
# project's sizing stack is bounded (see position_sizing_engine.py) --
# a single input shouldn't be able to blow a trade past a sane range
# on its own. 0.2 floor means even a very wide/highly leveraged stop
# can only shrink a trade to 20% of its confidence-tier base, never to
# zero; 1.5 ceiling caps how much a tight/safe stop can size a trade up.
RISK_ADJUSTMENT_FLOOR = 0.2
RISK_ADJUSTMENT_CEILING = 1.5

# Confidence-tier fractions of the per-trade position budget (see
# calculate_trade_amount() docstring). Same relative shape as the old
# fixed dollar tiers (100/200/300/500/750/1000, i.e. 0.1x-1.0x of the
# top tier) -- just expressed as a fraction of account_balance *
# MAX_POSITION_SIZE now, instead of a fraction of a fixed $1000.
_CONFIDENCE_TIER_FRACTIONS = (
    (90, 1.00),
    (85, 0.75),
    (80, 0.50),
    (75, 0.30),
    (70, 0.20),
)
_CONFIDENCE_TIER_FLOOR_FRACTION = 0.10


def get_account_balance(asset_class):
    """
    Real, current available balance for whichever broker owns this
    asset class -- CRYPTO reads Binance testnet USDT, FOREX/COMMODITIES
    reads eToro's account cash, everything else (US_STOCKS) reads
    Alpaca cash. Used by calculate_trade_amount() so trade sizing scales
    with however much money is actually in a given account, rather than
    a single fixed dollar range. Never raises -- a failed balance fetch
    returns 0.0, which calculate_trade_amount() below treats as "cannot
    afford any trade right now" rather than crashing or silently
    guessing a number.
    """
    if asset_class == "CRYPTO":
        try:
            import binance_broker
            return float(binance_broker.get_available_usdt())
        except Exception:
            return 0.0

    if asset_class in ("FOREX", "COMMODITIES"):
        try:
            import etoro_broker
            return float(etoro_broker.check_broker_connection().get("cash", 0) or 0)
        except Exception:
            return 0.0

    # US_STOCKS
    try:
        account = get_account()
        return float(account.cash)
    except Exception:
        return 0.0


def calculate_trade_amount(
    confidence,
    market_df=None,
    entry_price=None,
    stop_loss=None,
    leverage=1,
    account_balance=None,
):
    """
    Dynamic position sizing based on AI confidence, market risk,
    per-ticker stop distance/leverage, and (new, 2026-08-25) the real
    account balance behind the trade.

    FIX 2026-08-25 (two-part): this function drives every real order
    size in the system (execute_alpaca_trades/execute_binance_trades/
    execute_etoro_trades all call it identically) but, until now, had
    two gaps:

    1. No awareness of how far away a trade's actual stop-loss was, or
       whether the instrument trades on leverage. Two trades with
       identical AI confidence got identically sized even if one had a
       stop twice as far from entry, or was a 10x-leveraged eToro
       forex/commodities CFD (ETORO_LEVERAGE) where the same price move
       produces 10x the P&L swing of an unleveraged stock/crypto trade.
       Fixed via risk_adjustment below (stop-distance and leverage
       aware, clamped to [RISK_ADJUSTMENT_FLOOR, RISK_ADJUSTMENT_CEILING]),
       using STOP_LOSS_PERCENT (this project's standard 3% stop) as the
       reference distance.

    2. Sizing was a single fixed dollar band (MIN_TRADE_AMOUNT=$100 to
       MAX_TRADE_AMOUNT=$1000) tuned around one specific account's size.
       That's wrong for a bot meant to run for accounts of any size --
       someone with $500 and someone with $100,000 both got offered the
       exact same $100-$1000 trades, disconnected from what either of
       them actually has. Fixed by sizing off account_balance instead:
       position_budget = account_balance * MAX_POSITION_SIZE (this
       project's existing "never risk more than 20% of the account on
       one trade" cap, config.py), then confidence scales a fraction of
       that budget the same way it used to scale a fraction of the old
       $1000 ceiling. MIN_TRADE_AMOUNT remains a hard floor -- if an
       account can't cover even that floor, this returns 0.0 rather
       than force a trade, and every call site checks for that and
       skips the trade with an "insufficient balance" message instead
       of submitting a broker order for less than the floor.

    account_balance is optional for backward compatibility: if omitted,
    this falls back to the original fixed MIN_TRADE_AMOUNT/
    MAX_TRADE_AMOUNT-tiered behavior unchanged.

    entry_price/stop_loss are optional and sourced from each row's
    "Price ($)"/"Stop Loss" columns (trade_planner.py's per-ticker ATR
    stop) -- if either is missing/invalid the risk_adjustment step is
    skipped entirely (defaults to 1.0), so a data gap can only ever
    fall back to the pre-existing confidence/market-risk sizing, never
    block or crash a trade.
    """

    confidence = float(confidence)

    if account_balance is not None:
        account_balance = float(account_balance)

        # Can't cover even the configured minimum trade -- don't force
        # one. Call sites check for this 0.0 and skip with a clear
        # "insufficient balance" message rather than submitting an
        # order for less than the floor.
        if account_balance < MIN_TRADE_AMOUNT:
            return 0.0

        position_budget = account_balance * MAX_POSITION_SIZE

        confidence_fraction = _CONFIDENCE_TIER_FLOOR_FRACTION
        for tier_confidence, tier_fraction in _CONFIDENCE_TIER_FRACTIONS:
            if confidence >= tier_confidence:
                confidence_fraction = tier_fraction
                break

        base_amount = position_budget * confidence_fraction
        balance_ceiling = min(position_budget, account_balance)
    else:
        # Legacy fixed-band behavior (no account_balance supplied).
        if confidence >= 90:
            base_amount = 1000
        elif confidence >= 85:
            base_amount = 750
        elif confidence >= 80:
            base_amount = 500
        elif confidence >= 75:
            base_amount = 300
        elif confidence >= 70:
            base_amount = 200
        else:
            base_amount = 100
        balance_ceiling = MAX_TRADE_AMOUNT

    # Adjust based on market risk
    if market_df is not None:
        risk_level, risk_multiplier = get_market_risk_level(market_df)

        adjusted_amount = base_amount * risk_multiplier
    else:
        adjusted_amount = base_amount

    # Adjust based on per-ticker stop distance and leverage (see
    # docstring above for why this is a bounded multiplier, not a
    # literal portfolio-value risk formula).
    try:
        if entry_price is not None and stop_loss is not None:
            entry_price = float(entry_price)
            stop_loss = float(stop_loss)
            leverage = float(leverage) if leverage else 1.0

            if entry_price > 0 and leverage > 0:
                actual_stop_percent = abs(entry_price - stop_loss) / entry_price

                if actual_stop_percent > 0:
                    raw_adjustment = STOP_LOSS_PERCENT / (
                        actual_stop_percent * leverage
                    )
                    risk_adjustment = max(
                        RISK_ADJUSTMENT_FLOOR,
                        min(raw_adjustment, RISK_ADJUSTMENT_CEILING),
                    )
                    adjusted_amount *= risk_adjustment
    except (TypeError, ValueError, ZeroDivisionError):
        pass  # missing/bad stop data -- fall back to pre-existing sizing, never crash a trade over it

    # Respect configured limits: MIN_TRADE_AMOUNT is always the floor;
    # the ceiling is either the proportional balance_ceiling (account-
    # balance-aware path) or the legacy fixed MAX_TRADE_AMOUNT.
    adjusted_amount = min(adjusted_amount, balance_ceiling)
    adjusted_amount = max(MIN_TRADE_AMOUNT, adjusted_amount)

    return round(adjusted_amount, 2)

def _is_crypto_ticker(ticker):
    """This project's crypto tickers are all named 'XXX-USD'; stocks never are."""
    return str(ticker).upper().endswith("-USD")


def _is_forex_ticker(ticker):
    """yfinance forex tickers are all named 'XXXYYY=X' (e.g. EURUSD=X)."""
    return str(ticker).upper().endswith("=X")


def _is_commodity_ticker(ticker):
    """yfinance commodity futures tickers are all named 'XX=F' (e.g. GC=F)."""
    return str(ticker).upper().endswith("=F")


def _get_etoro_position_count(asset_class):
    """
    Real eToro-backed count for FOREX/COMMODITIES. Fixed 2026-08-06:
    this used to always count from st.session_state.positions (the local
    paper-trading dict), with a comment claiming "no real forex broker
    yet." That stopped being true once etoro_broker.py went live
    (2026-08-03) -- real FOREX/COMMODITIES trades route through eToro
    and never touch st.session_state.positions at all when
    ETORO_LIVE_TRADING is on, so the old count was silently checking a
    dict that real trades never update. It hadn't caused a visible bug
    yet because execute_etoro_trades() in app.py already has its own,
    correct real-position cap check that runs first -- but this function
    is also called as a second gate right after it (via
    risk_check_before_trade()), and would have quietly rubber-stamped
    that gate as "0 open" forever, or blocked things incorrectly, the
    moment anything relied on it alone. Mirrors the exact
    positions-by-symbol / resolve_project_ticker pattern already used in
    execute_etoro_trades().
    """
    try:
        import etoro_broker
        positions_by_symbol = {p["symbol"] for p in etoro_broker.get_positions()}
    except Exception:
        return 0

    project_tickers = ASSET_UNIVERSE.get(asset_class, {}).get("symbols", [])
    count = 0
    for ticker in project_tickers:
        try:
            etoro_symbol = etoro_broker.resolve_project_ticker(ticker)
        except Exception:
            continue
        if etoro_symbol in positions_by_symbol:
            count += 1
    return count


def get_live_forex_commodity_position_count(asset_class):
    """
    Public wrapper around _get_etoro_position_count(), for callers outside
    this module (the Performance Digest display in app.py) that need the
    real, live-eToro-verified FOREX/COMMODITIES open-position count --
    the same kind of override already applied to the digest's US_STOCKS
    count on 2026-08-19 (see the comment at that call site in app.py).

    Why this was needed: digest_engine.py's open_positions_by_asset_class
    counts distinct tickers with an unmatched BUY still sitting in the
    trade journal, for every asset class uniformly. That's the exact
    pattern that made the US_STOCKS digest count go stale (it showed 16
    when the broker only had 3 open). FOREX/COMMODITIES real risk
    enforcement was already fixed to use live eToro data on 2026-08-06
    (_get_etoro_position_count above), but the digest DISPLAY for these
    two asset classes was never updated to match -- it's been reading the
    same stale journal source as stocks was, this whole time.
    """
    return _get_etoro_position_count(asset_class)


def _get_forex_position_count():
    """
    Real eToro count when ETORO_LIVE_TRADING is on (the normal case --
    see _get_etoro_position_count() docstring for why). Falls back to
    the old local-paper count only when eToro trading is switched off
    and FOREX signals are genuinely routing through the local paper
    engine instead, where st.session_state.positions is the correct and
    only source of truth.
    """
    if ETORO_LIVE_TRADING:
        return _get_etoro_position_count("FOREX")
    return sum(
        1 for t in st.session_state.positions if _is_forex_ticker(t)
    )


def _get_commodity_position_count():
    """Same reasoning as _get_forex_position_count(), for commodities."""
    if ETORO_LIVE_TRADING:
        return _get_etoro_position_count("COMMODITIES")
    return sum(
        1 for t in st.session_state.positions if _is_commodity_ticker(t)
    )


def _get_stock_position_count():
    """
    Stocks share st.session_state.positions with forex/commodities, so the
    stock-only cap must exclude those tickers -- otherwise forex/commodity
    positions would eat into MAX_POSITIONS/MAX_OPEN_POSITIONS meant for
    stocks, the same class of bug this whole file works around for crypto.
    """
    return sum(
        1 for t in st.session_state.positions
        if not _is_forex_ticker(t) and not _is_commodity_ticker(t)
    )


def _get_crypto_position_count():
    """
    Number of crypto positions the BOT has actually opened, derived from
    the trade journal (same source as get_bot_owned_crypto_value) --
    NOT a raw count of the Binance wallet's nonzero balances.

    The wallet almost always has dust in every tracked coin (testnet
    seed funds), so counting the wallet directly meant this cap was
    already maxed out before the bot ever placed a single crypto trade,
    for the exact same reason the exposure and asset-class-limit checks
    were broken earlier.
    """
    try:
        from engines import performance_engine
        open_positions = performance_engine.get_open_positions_cost_basis()
        return sum(
            1 for ticker in open_positions
            if str(ticker).upper().endswith("-USD")
        )
    except Exception:
        return 0


def can_open_position(ticker):
    """
    Determines whether a new position can be opened.

    Stocks (st.session_state.positions) and crypto (the Binance testnet
    wallet) are tracked in completely different places, so they're capped
    independently here. They used to share this one check against the
    stock-only count, which meant stocks filling their 5 slots first
    silently locked crypto out of every run, regardless of how much (or
    little) crypto was actually held.
    """

    if _is_crypto_ticker(ticker):
        if _get_crypto_position_count() >= MAX_CRYPTO_POSITIONS:
            return False, "Maximum crypto positions reached."
        return True, ""

    # Forex/commodities: same independent-cap treatment as crypto above,
    # see MAX_FOREX_POSITIONS/MAX_COMMODITIES_POSITIONS in config.py for why.
    if _is_forex_ticker(ticker):
        if _get_forex_position_count() >= MAX_FOREX_POSITIONS:
            return False, "Maximum forex positions reached."
        return True, ""

    if _is_commodity_ticker(ticker):
        if _get_commodity_position_count() >= MAX_COMMODITIES_POSITIONS:
            return False, "Maximum commodities positions reached."
        return True, ""

    # Already holding? / too many positions?
    #
    # This used to check st.session_state.positions and
    # _get_stock_position_count() unconditionally, even when LIVE_TRADING
    # is on. That's local-paper leftover state -- once stocks started
    # routing through real Alpaca (2026-07-30), that local dict still had
    # 5 old local-paper positions sitting in it, so every single Alpaca
    # BUY got blocked with "Already holding this stock" or "Maximum
    # portfolio positions reached" even though the real Alpaca account had
    # zero open positions and plenty of free cash. Mirrors the LIVE_TRADING
    # branch risk_check_before_trade() already uses below.
    if LIVE_TRADING:
        try:
            owned_symbols = {
                str(position.symbol).upper().strip()
                for position in get_open_positions()
            }
        except Exception:
            owned_symbols = set()

        if ticker in owned_symbols:
            if not ALLOW_PYRAMIDING:
                return False, "Already holding this stock."

        if len(owned_symbols) >= MAX_POSITIONS:
            return False, "Maximum portfolio positions reached."

        return True, ""

    if ticker in st.session_state.positions:
        if not ALLOW_PYRAMIDING:
            return False, "Already holding this stock."

    # Too many positions? (stock-only count -- see _get_stock_position_count)
    if _get_stock_position_count() >= MAX_POSITIONS:
        return False, "Maximum portfolio positions reached."

    return True, ""


def risk_check_before_trade(ticker, trade_amount, market_df):
    """
    Final risk gate before opening a trade.
    Checks position limit, cash, exposure, daily trade limit, and cooldown.
    """

    is_crypto = _is_crypto_ticker(ticker)
    is_forex = _is_forex_ticker(ticker)
    is_commodity = _is_commodity_ticker(ticker)

    # 1. Maximum open positions -- crypto, forex, commodities and stocks
    # are all capped independently, see can_open_position() above for why.
    if is_crypto:
        if _get_crypto_position_count() >= MAX_CRYPTO_POSITIONS:
            return False, "Maximum crypto positions reached."
    elif is_forex:
        if _get_forex_position_count() >= MAX_FOREX_POSITIONS:
            return False, "Maximum forex positions reached."
    elif is_commodity:
        if _get_commodity_position_count() >= MAX_COMMODITIES_POSITIONS:
            return False, "Maximum commodities positions reached."
    elif LIVE_TRADING:
        try:
            positions = get_open_positions()
            if len(positions) >= MAX_OPEN_POSITIONS:
                return False, "Maximum open positions reached."
        except Exception:
            pass
    else:
        if _get_stock_position_count() >= MAX_OPEN_POSITIONS:
            return False, "Maximum open positions reached."

    # 2. Cash check -- crypto spends from the Binance testnet USDT
    # balance, not the local stock paper-trading cash pool. This used to
    # check st.session_state.cash even for crypto orders, so crypto could
    # get "Insufficient cash" once stocks used up the stock cash pool,
    # despite the testnet balance being untouched and available.
    if is_crypto:
        try:
            import binance_broker
            available_cash = binance_broker.get_available_usdt()
        except Exception:
            available_cash = 0.0
    elif LIVE_TRADING:
        try:
            account = get_account()
            available_cash = float(account.cash)
        except Exception:
            available_cash = st.session_state.cash
    else:
        available_cash = st.session_state.cash

    if available_cash < trade_amount:
        return False, "Insufficient cash."

    # 3. Portfolio exposure
    exposure = get_exposure_percent(market_df)

    if exposure >= MAX_PORTFOLIO_EXPOSURE * 100:
        return False, "Maximum portfolio exposure reached."

    # 4 & 5. Daily trade limit + per-ticker cooldown.
    #
    # Fixed 2026-08-07: both of these used to read from
    # st.session_state.trade_log / st.session_state.last_trade_time.
    # Tracing through every execution path that actually places a real
    # trade (execute_alpaca_trades, execute_binance_trades,
    # execute_etoro_trades, execute_paper_trades in app.py) found that
    # ONLY execute_paper_trades and execute_binance_trades ever wrote to
    # those two session_state structures -- execute_alpaca_trades() and
    # execute_etoro_trades() never touched either one. Since LIVE_TRADING
    # and ETORO_LIVE_TRADING are both on, essentially every real stock
    # and forex/commodities trade this bot places was silently invisible
    # to both checks below: MAX_TRADES_PER_DAY only ever counted crypto,
    # and TRADE_COOLDOWN_MINUTES never applied to stocks or
    # forex/commodities at all. On top of that, session_state resets on
    # every service restart (deploys, or health_check.sh's self-heal),
    # so even crypto's numbers would silently reset to zero mid-day.
    #
    # Fix: read from the persistent order book (trade_journal.db, via
    # engines.order_manager.load_orders()) instead. Every one of the
    # four execution paths above already calls create_order()+
    # save_order() on every fill, so this sees every real trade across
    # every broker, and survives restarts -- no new state file needed,
    # this data was already being recorded correctly, just never read
    # back for these two checks.
    try:
        recent_orders = load_orders(limit=500)
    except Exception:
        recent_orders = []

    # FIX 2026-08-23: exclude synthetic "adopted entry" bookkeeping orders
    # (strategy prefix "ADOPTED_ENTRY_") from the daily-trade-limit and
    # cooldown checks below. These are one-time entry-price backfills
    # created by adopt_entry_for_orphaned_positions.py for positions with
    # no recorded original BUY (see order_manager.get_most_recent_filled_buy()'s
    # docstring) -- they never represent a real AI trading decision, but
    # they're real rows in the same order book these checks read from.
    # Confirmed live: 12 such orders consumed 12 of the day's 30-trade
    # crypto budget the moment they were created, with zero real trading
    # behind them. Entry-price lookups still see them (that's the point
    # of writing them) -- only these two throttling checks exclude them.
    recent_orders = [
        o for o in recent_orders
        if not str(o.get("strategy", "")).startswith("ADOPTED_ENTRY_")
    ]

    def _order_timestamp(order):
        timestamp_text = (
            order.get("filled_at") or order.get("updated_at") or order.get("created_at")
        )
        if not timestamp_text:
            return None
        try:
            return datetime.fromisoformat(timestamp_text)
        except Exception:
            return None

    # 4. Daily trade limit -- scoped per asset class, not shared across
    # all of them. This used to count every order placed today across
    # stocks, crypto, forex and commodities against one combined budget
    # of MAX_TRADES_PER_DAY. In practice that meant a busy day of stock
    # activity (including automatic stop-loss/take-profit/trailing exits,
    # which count as trades too) could exhaust the shared budget before
    # crypto's auto-trading loop -- which always runs last in each pass,
    # see the auto-trading block below -- ever got a turn, silently
    # stalling it with no visible error. Each asset class now gets its
    # own independent budget of MAX_TRADES_PER_DAY, the same pattern
    # already used for position caps (MAX_CRYPTO_POSITIONS etc. above).
    current_asset_class = (
        "CRYPTO" if is_crypto
        else "FOREX" if is_forex
        else "COMMODITIES" if is_commodity
        else "US_STOCKS"
    )

    today = datetime.now().date()
    trades_today = 0
    for order in recent_orders:
        if str(order.get("asset_class", "")).upper() != current_asset_class:
            continue
        ts = _order_timestamp(order)
        if ts is not None and ts.date() == today:
            trades_today += 1

    # CRYPTO gets its own, faster daily-trade budget and cooldown (see
    # CRYPTO_MAX_TRADES_PER_DAY/CRYPTO_TRADE_COOLDOWN_MINUTES in
    # config.py, added 2026-08-22 for the crypto scalping engine,
    # roadmap item #9) -- stocks/forex/commodities keep the shared
    # defaults above unchanged.
    max_trades_for_class = (
        CRYPTO_MAX_TRADES_PER_DAY if current_asset_class == "CRYPTO"
        else MAX_TRADES_PER_DAY
    )
    cooldown_minutes_for_class = (
        CRYPTO_TRADE_COOLDOWN_MINUTES if current_asset_class == "CRYPTO"
        else TRADE_COOLDOWN_MINUTES
    )

    if trades_today >= max_trades_for_class:
        return False, f"Maximum daily trades reached for {current_asset_class}."

    # 5. Cooldown per ticker -- most recent order for this exact ticker,
    # across all brokers.
    ticker_normalized = str(ticker).upper().strip()
    last_trade_for_ticker = None
    for order in recent_orders:
        if str(order.get("ticker", "")).upper().strip() != ticker_normalized:
            continue
        ts = _order_timestamp(order)
        if ts is not None and (last_trade_for_ticker is None or ts > last_trade_for_ticker):
            last_trade_for_ticker = ts

    if last_trade_for_ticker is not None:
        elapsed = datetime.now() - last_trade_for_ticker

        if elapsed < timedelta(minutes=cooldown_minutes_for_class):
            return False, "Cooldown active."

    return True, "OK"

def get_dynamic_buy_confidence(market_df):
    """
    Adjusts required BUY confidence based on market movement and market regime.
    Higher number = stricter BUY filter.
    """

    avg_change = market_df["Daily Change %"].mean()
    market_regime, regime_score = get_market_regime()

    # Base threshold from short-term market movement
    if avg_change > 2:
        threshold = 65
    elif avg_change > 1:
        threshold = 70
    elif avg_change > 0:
        threshold = 75
    elif avg_change > -1:
        threshold = 80
    else:
        threshold = 85

    # Regime adjustment
    if market_regime == "STRONG BULL":
        threshold -= 5
    elif market_regime == "BULL":
        threshold -= 2
    elif market_regime == "NEUTRAL":
        threshold += 0
    elif market_regime == "DEFENSIVE":
        threshold += 5
    elif market_regime == "BEAR":
        threshold += 10

    # Keep threshold inside safe range
    threshold = max(60, min(95, threshold))

    return threshold

def get_exposure_percent(market_df):
    if LIVE_TRADING:
        try:
            positions = get_open_positions()
            account = get_account()

            equity = float(account.equity)
            total_position_value = 0

            for position in positions:
                total_position_value += float(position.market_value)

            if equity == 0:
                return 0

            return (total_position_value / equity) * 100

        except Exception:
            return 0

    portfolio_value = calculate_portfolio_value(market_df)
    invested_value = get_open_positions_value(market_df)

    if portfolio_value == 0:
        return 0

    return (invested_value / portfolio_value) * 100

def calculate_portfolio_value(market_df):
    value = st.session_state.cash

    for ticker, position in st.session_state.positions.items():
        latest_price = market_df.loc[market_df["Ticker"] == ticker, "Price ($)"].values

        if len(latest_price) > 0:
            value += position["shares"] * latest_price[0]

    # Crypto trades independently of LIVE_TRADING (always via Binance
    # testnet), so its value belongs in the portfolio total regardless
    # of stock execution mode.
    try:
        import binance_broker
        value += binance_broker.get_crypto_positions_value(market_df)
    except Exception:
        pass  # Binance not configured/reachable -- don't break the whole page over it

    return value

def calculate_bot_attributable_portfolio_value(market_df):
    """
    Portfolio value for the equity/drawdown snapshot (engines/
    equity_tracker.py) specifically -- uses only the crypto the bot
    itself actually bought (get_bot_owned_crypto_value(), same source
    already used to fix the exposure-percent gate), instead of the full
    raw Binance wallet value calculate_portfolio_value() uses.

    calculate_portfolio_value() is intentionally left untouched: it's
    correctly used elsewhere for net-worth display, where the full
    wallet balance genuinely is your money regardless of who bought it.
    But that same full-wallet number is the wrong thing to judge trading
    PERFORMANCE against -- pre-seeded testnet dust and legacy pre-fix
    pyramided positions swinging in value isn't the bot's skill, and
    letting that swing register as "drawdown" misrepresents how the
    strategy has actually performed.
    """
    value = st.session_state.cash

    for ticker, position in st.session_state.positions.items():
        latest_price = market_df.loc[market_df["Ticker"] == ticker, "Price ($)"].values
        if len(latest_price) > 0:
            value += position["shares"] * latest_price[0]

    value += get_bot_owned_crypto_value(market_df)

    return value


def get_bot_owned_crypto_value(market_df):
    """
    Value of ONLY the crypto this bot has actually bought itself, derived
    from trade_journal.db via FIFO BUY/SELL matching -- as opposed to
    whatever raw balance happens to sit in the Binance testnet wallet.

    Binance testnet accounts commonly come pre-seeded with dust in every
    tracked coin (BTC/ETH/BNB/SOL) that the bot never traded. Counting
    that dust as "invested" inflated the portfolio-exposure gate and
    permanently blocked new trades (both stock and crypto) that had
    nothing to do with it. This isolates the bot's own positions so the
    exposure check reflects risk the bot actually chose to take on.
    """
    try:
        from engines import performance_engine
        open_positions = performance_engine.get_open_positions_cost_basis()
    except Exception:
        return 0.0

    if market_df is None or market_df.empty:
        return 0.0

    total_value = 0.0
    for ticker, lot in open_positions.items():
        if not str(ticker).upper().endswith("-USD"):
            continue  # this project's crypto tickers are all "XXX-USD"; skip stocks

        price_rows = market_df.loc[market_df["Ticker"] == ticker, "Price ($)"]
        if not price_rows.empty:
            total_value += lot["shares"] * float(price_rows.iloc[0])

    return total_value


def get_open_positions_value(market_df):
    total_value = 0.0

    if LIVE_TRADING:
        # Fixed 2026-08-08: this call had NO error handling at all, unlike
        # every other broker.get_positions()/get_open_positions() call in
        # this file (see get_exposure_percent() a few lines up, which
        # already wraps the identical call in try/except). Alpaca having
        # a real, if temporary, outage ("service temporary unavailable")
        # took down the ENTIRE dashboard with an unhandled
        # alpaca.common.exceptions.APIError -- not just this one number
        # going blank, the whole Streamlit script crashed mid-render.
        # Matches the same "don't let a broker hiccup break the whole
        # page" reasoning already applied everywhere else here.
        try:
            positions = broker.get_positions()
            for pos in positions:
                total_value += float(pos.market_value)
        except Exception:
            pass
    else:
        for ticker, position in st.session_state.positions.items():
            latest_price = market_df.loc[market_df["Ticker"] == ticker, "Price ($)"].values
            if len(latest_price) > 0:
                total_value += position["shares"] * latest_price[0]

    # Only the bot's own crypto trades count toward invested/exposure --
    # see get_bot_owned_crypto_value() docstring. calculate_portfolio_value()
    # below still uses the FULL real wallet value for net-worth display,
    # since that money is genuinely yours regardless of who bought it.
    total_value += get_bot_owned_crypto_value(market_df)

    return total_value
