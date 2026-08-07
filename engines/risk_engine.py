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
    ALLOW_PYRAMIDING,
    MAX_POSITIONS,
    MAX_OPEN_POSITIONS,
    MAX_CRYPTO_POSITIONS,
    MAX_FOREX_POSITIONS,
    MAX_COMMODITIES_POSITIONS,
    MAX_PORTFOLIO_EXPOSURE,
    MAX_TRADES_PER_DAY,
    TRADE_COOLDOWN_MINUTES,
    HIGH_SCORE_SIZE_MULTIPLIER,
    NORMAL_SCORE_SIZE_MULTIPLIER,
    LOW_SCORE_SIZE_MULTIPLIER,
)
from data.asset_universe import ASSET_UNIVERSE


def calculate_trade_amount(confidence, market_df=None):
    """
    Dynamic position sizing based on AI confidence and market risk.
    """

    confidence = float(confidence)

    # Base position size from AI confidence
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

    # Adjust based on market risk
    if market_df is not None:
        risk_level, risk_multiplier = get_market_risk_level(market_df)
        
        adjusted_amount = base_amount * risk_multiplier
    else:
        adjusted_amount = base_amount

    # Respect configured limits
    adjusted_amount = max(MIN_TRADE_AMOUNT, adjusted_amount)
    adjusted_amount = min(MAX_TRADE_AMOUNT, adjusted_amount)

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

    # 4. Daily trade limit -- global across all brokers/asset classes,
    # matching the original check's scope.
    today = datetime.now().date()
    trades_today = 0
    for order in recent_orders:
        ts = _order_timestamp(order)
        if ts is not None and ts.date() == today:
            trades_today += 1

    if trades_today >= MAX_TRADES_PER_DAY:
        return False, "Maximum daily trades reached."

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

        if elapsed < timedelta(minutes=TRADE_COOLDOWN_MINUTES):
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
        positions = broker.get_positions()
        for pos in positions:
            total_value += float(pos.market_value)
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
