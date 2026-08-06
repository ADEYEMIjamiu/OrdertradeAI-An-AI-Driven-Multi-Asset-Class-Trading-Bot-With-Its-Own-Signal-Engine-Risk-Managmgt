import streamlit as st
import pandas as pd
import yfinance as yf
import ta
import joblib
import os
import time

if "AUTO_TRADING" not in st.session_state:
    st.session_state.AUTO_TRADING = False

from streamlit_autorefresh import st_autorefresh
# The actual autorefresh call lives further down (search
# "market_refresh"), guarded against interrupting an in-progress trade
# execution. This import is kept up top since it's needed there.

# =========================
# ENGINE INIT
# =========================
if "paper_engine" not in st.session_state:
    from paper_trading.paper_engine import PaperTradingEngine
    st.session_state.paper_engine = PaperTradingEngine(starting_cash=10000)
    print(f"🧠 ENGINE CREATED: {id(st.session_state.paper_engine)}")

print(f"🧠 ENGINE ID (CURRENT): {id(st.session_state.paper_engine)}")

# =========================
# RUN ENGINE (AFTER INIT)
# =========================

from smart_strategy_v23 import run_engine

from broker import broker
from datetime import datetime
from streamlit_autorefresh import st_autorefresh
from trade_journal import init_trade_journal, log_trade, load_trade_journal

from data.asset_universe import get_enabled_symbols
from engines.strategy_engine import identify_strategy, score_strategy
from engines.position_engine import (
    initialize_position,
    update_position,
    check_position_exit,
)
from engines.market_data_engine import get_market_data
from engines.order_manager import (
    create_order,
    mark_order_filled,
    mark_order_rejected,
    mark_order_failed,
    save_order,
)
from engines.portfolio_engine import (
    calculate_asset_allocation,
    can_add_asset_class,
    rank_trades_by_portfolio_fit,
    preview_allocation_after_trades,
    filter_trades_by_portfolio_limits,
)
from engines.position_sizing_engine import calculate_position_size
from engines.execution_engine import sort_trade_queue, filter_executable_trades
from engines import performance_engine
from engines.digest_engine import calculate_performance_digest
import telegram_notifier
import emergency_stop
import account_store
import etoro_broker
from engines.priority_engine import calculate_priority
from engines.scoring_engine import calculate_trade_score
from engines.trade_planner import create_trade_plan
from engines.approval_engine import approve_trade
from engines.risk_engine import (
    calculate_portfolio_value,
    get_open_positions_value,
    get_exposure_percent,
    calculate_trade_amount,
    can_open_position,
    risk_check_before_trade,
    get_dynamic_buy_confidence,
)
from engines.regime_engine import (
    get_market_risk_level,
    get_market_regime,
)
from engines.broker_sync_engine import (
    get_broker_order_snapshot,
    get_broker_position_snapshot,
    broker_has_active_order,
    broker_already_owns_symbol,
    get_broker_state_health,
    broker_execution_gate,
    get_ai_trading_readiness,
)
from broker import (
    get_account,
    buy_stock,
    sell_stock,
    get_open_positions,
    get_orders,
    get_market_clock,
    check_broker_connection,
    get_portfolio_history,
    get_broker_trades_today_count
)
from config import (
    INITIAL_CASH,
    MIN_TRADE_AMOUNT,
    MAX_TRADE_AMOUNT,
    RISK_PER_TRADE,
    BUY_CONFIDENCE,
    SELL_CONFIDENCE,
    STOP_LOSS_PERCENT,
    TAKE_PROFIT_PERCENT,
    PAPER_TRADING,
    LIVE_TRADING,
    MAX_OPEN_POSITIONS,
    MAX_PORTFOLIO_EXPOSURE,
    DAILY_LOSS_LIMIT,
    DAILY_PROFIT_TARGET,
    MAX_TRADES_PER_DAY,
    TRAILING_PROFIT_START,
    TRAILING_PROFIT_DROP,
    MAX_OPEN_POSITIONS,
    MAX_PORTFOLIO_EXPOSURE,
    TRADE_COOLDOWN_MINUTES,
    MIN_TRADE_AMOUNT,
    MAX_TRADE_AMOUNT,
    RISK_PER_TRADE,
    MARKET_RISK_LOW,
    MARKET_RISK_MEDIUM,
    MARKET_RISK_HIGH,
    AGGRESSIVE_RISK_MULTIPLIER,
    NORMAL_RISK_MULTIPLIER,
    DEFENSIVE_RISK_MULTIPLIER,
    DANGER_RISK_MULTIPLIER,
    ALLOW_PYRAMIDING,
    ALLOW_CRYPTO_PYRAMIDING,
    MAX_POSITIONS,
    ATR_STOP_MULTIPLIER,
    ATR_TAKE_PROFIT_MULTIPLIER,
    MIN_RISK_REWARD_RATIO,
    REGIME_STRONG_BULL_SCORE,
    REGIME_BULL_SCORE,
    REGIME_NEUTRAL_SCORE,
    REGIME_DEFENSIVE_SCORE,
    MIN_TRADE_SCORE,
    HIGH_SCORE_SIZE_MULTIPLIER,
    NORMAL_SCORE_SIZE_MULTIPLIER,
    LOW_SCORE_SIZE_MULTIPLIER,
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
    AUTO_LIVE_TRADING_LOCKED,
    ALPACA_VALIDATION_START,
    ETORO_LIVE_TRADING,
    AUTO_ETORO_TRADING_LOCKED,
    REQUIRE_ETORO_DEMO_ENVIRONMENT,
    MAX_FOREX_POSITIONS,
    MAX_COMMODITIES_POSITIONS,
)

st.set_page_config(page_title="OrderTrade AI", layout="wide")

# Visual polish only -- no behavioral changes. Card-style metrics and
# tighter spacing so the dashboard reads like a trading terminal rather
# than a default Streamlit app, while the app itself is still mid
# validation (see .streamlit/config.toml for the base dark theme).
st.markdown(
    """
    <style>
    div[data-testid="stMetric"] {
        background-color: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 10px;
        padding: 14px 16px 10px 16px;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.8rem;
        letter-spacing: 0.02em;
        opacity: 0.75;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.6rem;
        font-weight: 600;
    }
    h1, h2, h3 {
        letter-spacing: -0.01em;
    }
    div[data-testid="stDataFrame"] {
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 8px;
        overflow: hidden;
    }
    hr {
        margin-top: 1.4rem;
        margin-bottom: 1.4rem;
        opacity: 0.15;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

init_trade_journal()
account = None
broker_health = check_broker_connection()
broker_state_health = get_broker_state_health()
if LIVE_TRADING:
    try:
        account = get_account()
    except Exception as e:
        st.error(f"Unable to connect to Alpaca: {e}")

# Auto-refresh -- required for genuinely unattended 24/7 operation
# (otherwise trades only ever happen when a human clicks something).
#
# This was disabled for a long time because a rerun triggered mid-click
# can interrupt Streamlit's script thread while a trade execution is
# still being logged (Streamlit cancels the currently running script
# pass whenever a new rerun is triggered, including by this timer).
# trade_execution_in_progress (set around both the manual Execute
# Trades button and the Auto-Trading block below) prevents this
# component from even being rendered -- and therefore from re-arming
# its timer -- while a trade batch is actively executing. Combined
# with a long interval (5 minutes, which is already far more frequent
# than the underlying market data changes -- it's daily-resolution),
# this makes the original race very unlikely in practice.
#
# It does not make it mathematically impossible: a timer that already
# fired and is in flight over the websocket at the exact moment
# execution begins can't be recalled by a server-side flag. Fully
# closing that gap means moving trade execution into its own process,
# decoupled from the Streamlit UI's rerun cycle entirely -- worth doing
# before scaling up real capital, not required for continued
# paper-trading validation.
if not st.session_state.get("trade_execution_in_progress", False):
    st_autorefresh(
        interval=300000,
        key="market_refresh",
    )

MODEL_PATH = "models/trading_model.pkl"
FEATURES_PATH = "models/features.pkl"

# 🔥 LOAD FROM YOUR ASSET UNIVERSE
asset_list = get_enabled_symbols()

# Extract tickers
tickers = [asset["symbol"] for asset in asset_list]

print("🔥 LOADED ASSETS:", tickers)

# Loaded from persistent shared storage (account_store.py) rather than
# reset to hardcoded defaults, so opening the dashboard from a new
# browser tab or a different device shows the SAME live account instead
# of an independent fresh copy. Only fires once per session (gated on
# "cash", same pattern as before) -- a session already running keeps
# using its own in-memory state for the rest of its life, saving back to
# storage after every mutation so it stays the source of truth for any
# other session that loads afterward. See account_store.py for the full
# reasoning and its limits.
if "cash" not in st.session_state:
    _saved_account = account_store.load_account(default_cash=INITIAL_CASH)
    st.session_state.cash = _saved_account["cash"]
    st.session_state.positions = _saved_account["positions"]
    st.session_state.equity_history = _saved_account["equity_history"]
    st.session_state.AUTO_TRADING = _saved_account["auto_trading"]
    st.session_state.last_trade_time = _saved_account["last_trade_time"]

if "trade_log" not in st.session_state:
    st.session_state.trade_log = []

if "last_execution_result" not in st.session_state:
    st.session_state.last_execution_result = None
    
if "trade_messages" not in st.session_state:
    st.session_state.trade_messages = []
    
    
def prepare_data(ticker):
    df = get_market_data(
        ticker,
        period="2y",
        interval="1d",
    )

    if df is None or df.empty:
        raise ValueError(
            f"No market data available for {ticker}"
        )

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.dropna()

    if "Close" not in df.columns:
        raise ValueError(
            f"Close price column is missing for {ticker}"
        )

    close = df["Close"].squeeze()

    df["SMA20"] = ta.trend.sma_indicator(
        close,
        window=20,
    )

    df["SMA50"] = ta.trend.sma_indicator(
        close,
        window=50,
    )

    df["RSI"] = ta.momentum.rsi(
        close,
        window=14,
    )

    df["MACD"] = ta.trend.macd(close)

    df["Returns"] = close.pct_change()

    df["Volatility"] = (
        df["Returns"]
        .rolling(20)
        .std()
    )

    df = df.dropna()

    if len(df) < 2:
        raise ValueError(
            f"Insufficient prepared market data for {ticker}"
        )

    return df


def get_ai_signal(ticker, model, features):
    df = prepare_data(ticker)

    latest = df.iloc[-1]
    previous = df.iloc[-2]
    X_live = df[features].tail(1)

    probability_up = model.predict_proba(X_live)[0][1]

    price = float(latest["Close"])
    previous_price = float(previous["Close"])
    daily_change = ((price / previous_price) - 1) * 100

    mtf_score, mtf_details = get_multi_timeframe_signal(ticker)

    confidence = probability_up * 100
    confidence += mtf_score * 5
    confidence = max(0, min(100, confidence))

    if confidence >= BUY_CONFIDENCE * 100:
        signal = "BUY"
    elif confidence <= SELL_CONFIDENCE * 100:
        signal = "SELL"
    else:
        signal = "HOLD"

    return {
        "Ticker": ticker,
        "Price ($)": round(price, 2),
        "Daily Change %": round(daily_change, 2),
        "AI Confidence %": round(confidence, 2),
        "Signal": signal,
        "Trend Score": mtf_score,
        "Trend Details": ", ".join(mtf_details)
    }
        
def get_multi_timeframe_signal(ticker):
    timeframes = {
        "1d": ("6mo", "1d"),
        "1h": ("60d", "1h"),
        "15m": ("30d", "15m"),
    }

    score = 0
    details = []

    for name, (period, interval) in timeframes.items():
        try:
            df = get_market_data(
                ticker,
                period=period,
                interval=interval,
            )

            if df is None or df.empty:
                details.append(f"{name}: insufficient data")
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            if "Close" not in df.columns:
                details.append(f"{name}: Close column missing")
                continue

            df = df.dropna(subset=["Close"])

            if len(df) < 50:
                details.append(f"{name}: insufficient data")
                continue

            df["SMA20"] = df["Close"].rolling(20).mean()
            df["SMA50"] = df["Close"].rolling(50).mean()

            df = df.dropna(subset=["SMA20", "SMA50"])

            if df.empty:
                details.append(f"{name}: insufficient indicator data")
                continue

            latest_close = float(df["Close"].iloc[-1])
            sma20 = float(df["SMA20"].iloc[-1])
            sma50 = float(df["SMA50"].iloc[-1])

            if latest_close > sma20 > sma50:
                score += 1
                details.append(f"{name}: bullish")

            elif latest_close < sma20 < sma50:
                score -= 1
                details.append(f"{name}: bearish")

            else:
                details.append(f"{name}: mixed")

        except Exception as e:
            details.append(f"{name}: error {e}")

    return score, details


def get_live_account_metrics():
    """
    Reads real Alpaca paper account values when LIVE_TRADING is enabled.
    Falls back safely if Alpaca is unavailable.
    """
    if LIVE_TRADING:
        try:
            account = get_account()
            positions = get_open_positions()

            equity = float(account.equity)
            cash = float(account.cash)

            position_value = 0
            for position in positions:
                position_value += float(position.market_value)

            exposure = (position_value / equity) * 100 if equity > 0 else 0

            return {
                "equity": equity,
                "cash": cash,
                "position_value": position_value,
                "exposure": exposure,
                "positions_count": len(positions)
            }

        except Exception:
            pass

    return {
        "equity": calculate_portfolio_value(market_df) if "market_df" in globals() else st.session_state.cash,
        "cash": st.session_state.cash,
        "position_value": 0,
        "exposure": 0,
        "positions_count": len(st.session_state.positions)
    }

from datetime import datetime, timedelta, timezone

def get_alpaca_performance_metrics():
    """
    Build performance metrics directly from Alpaca Paper Trading.

    The function:
    - reads Alpaca account values;
    - reads current broker positions;
    - reads filled Alpaca orders;
    - matches BUY and SELL fills using FIFO accounting;
    - calculates realised profit/loss;
    - calculates wins, losses and win rate;
    - calculates current capital invested and portfolio exposure.
    """

    try:
        account = get_account()
        positions = get_open_positions()
        orders = get_orders()

        equity = float(account.equity)
        cash = float(account.cash)

        # -------------------------------------------------
        # Current open-position metrics
        # -------------------------------------------------
        capital_invested = 0.0

        for position in positions:
            try:
                capital_invested += abs(
                    float(position.market_value)
                )
            except (TypeError, ValueError, AttributeError):
                continue

        portfolio_exposure = (
            capital_invested / equity
        ) * 100 if equity > 0 else 0.0

        # -------------------------------------------------
        # Helper functions for Alpaca enums and timestamps
        # -------------------------------------------------
        def normalise_enum_value(value):
            """
            Convert Alpaca enum objects such as OrderSide.BUY
            into simple lowercase text such as 'buy'.
            """
            if value is None:
                return ""

            if hasattr(value, "value"):
                value = value.value

            value = str(value).lower().strip()

            if "." in value:
                value = value.split(".")[-1]

            return value

        def get_order_time(order):
            """
            Return the best available timestamp for ordering fills.
            """
            return (
                getattr(order, "filled_at", None)
                or getattr(order, "submitted_at", None)
                or getattr(order, "created_at", None)
            )

        # -------------------------------------------------
        # Keep only usable filled orders
        # -------------------------------------------------
        filled_orders = []

        for order in orders:
            status = normalise_enum_value(
                getattr(order, "status", None)
            )

            if status != "filled":
                continue

            symbol = str(
                getattr(order, "symbol", "")
            ).upper().strip()

            side = normalise_enum_value(
                getattr(order, "side", None)
            )

            try:
                filled_qty = float(
                    getattr(order, "filled_qty", 0) or 0
                )

                filled_price = float(
                    getattr(order, "filled_avg_price", 0) or 0
                )
            except (TypeError, ValueError):
                continue

            if (
                not symbol
                or side not in {"buy", "sell"}
                or filled_qty <= 0
                or filled_price <= 0
            ):
                continue

            filled_orders.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "qty": filled_qty,
                    "price": filled_price,
                    "time": get_order_time(order),
                    "order_id": str(
                        getattr(order, "id", "")
                    ),
                }
            )

        # Drop anything filled before the validation-period cutoff -- the
        # Alpaca paper account has real order history from earlier dev
        # testing (e.g. 2026-07-24) that must not leak into "clean slate"
        # performance numbers. See ALPACA_VALIDATION_START in config.py.
        # Orders with no timestamp at all are excluded too, since we can't
        # verify which side of the cutoff they fall on.
        def _is_after_validation_start(order_time):
            if order_time is None:
                return False
            if order_time.tzinfo is None:
                order_time = order_time.replace(tzinfo=timezone.utc)
            return order_time >= ALPACA_VALIDATION_START

        filled_orders = [
            order for order in filled_orders
            if _is_after_validation_start(order["time"])
        ]

        # Sort oldest to newest before FIFO matching
        filled_orders.sort(
            key=lambda item: (
                item["time"] is None,
                str(item["time"]),
            )
        )

        # -------------------------------------------------
        # FIFO trade-lifecycle matching
        # -------------------------------------------------
        open_buy_lots = {}

        realised_profit_loss = 0.0
        realised_cost_basis = 0.0

        wins = 0
        losses = 0
        breakeven_trades = 0
        trades_closed = 0

        matched_trades = []

        for order in filled_orders:
            symbol = order["symbol"]
            side = order["side"]
            quantity = order["qty"]
            price = order["price"]

            if symbol not in open_buy_lots:
                open_buy_lots[symbol] = []

            # Add each BUY as a FIFO inventory lot
            if side == "buy":
                open_buy_lots[symbol].append(
                    {
                        "remaining_qty": quantity,
                        "entry_price": price,
                        "time": order["time"],
                        "order_id": order["order_id"],
                    }
                )
                continue

            # Process a SELL against the oldest BUY lots
            remaining_sell_qty = quantity
            sell_realised_pnl = 0.0
            sell_cost_basis = 0.0
            matched_quantity = 0.0

            while (
                remaining_sell_qty > 0
                and open_buy_lots[symbol]
            ):
                buy_lot = open_buy_lots[symbol][0]

                matched_qty = min(
                    remaining_sell_qty,
                    buy_lot["remaining_qty"],
                )

                entry_price = buy_lot["entry_price"]

                matched_cost = matched_qty * entry_price

                matched_pnl = matched_qty * (
                    price - entry_price
                )

                sell_realised_pnl += matched_pnl
                sell_cost_basis += matched_cost
                matched_quantity += matched_qty

                buy_lot["remaining_qty"] -= matched_qty
                remaining_sell_qty -= matched_qty

                if buy_lot["remaining_qty"] <= 1e-10:
                    open_buy_lots[symbol].pop(0)

            # Only count the sell if it matched a previous buy
            if matched_quantity > 0:
                trades_closed += 1

                realised_profit_loss += sell_realised_pnl
                realised_cost_basis += sell_cost_basis

                if sell_realised_pnl > 0:
                    wins += 1
                    trade_result = "WIN"

                elif sell_realised_pnl < 0:
                    losses += 1
                    trade_result = "LOSS"

                else:
                    breakeven_trades += 1
                    trade_result = "BREAKEVEN"

                matched_trades.append(
                    {
                        "Ticker": symbol,
                        "Exit Price": round(price, 4),
                        "Matched Qty": round(
                            matched_quantity,
                            8,
                        ),
                        "Cost Basis": round(
                            sell_cost_basis,
                            2,
                        ),
                        "Realised PnL": round(
                            sell_realised_pnl,
                            2,
                        ),
                        "Result": trade_result,
                        "Exit Time": order["time"],
                        "Sell Order ID": order["order_id"],
                    }
                )

        # -------------------------------------------------
        # Performance ratios
        # -------------------------------------------------
        return_percent = (
            realised_profit_loss / realised_cost_basis
        ) * 100 if realised_cost_basis > 0 else 0.0

        win_loss_total = wins + losses

        win_rate = (
            wins / win_loss_total
        ) * 100 if win_loss_total > 0 else 0.0

        return {
            "profit_loss": realised_profit_loss,
            "return_percent": return_percent,
            "win_rate": win_rate,
            "trades_closed": trades_closed,
            "wins": wins,
            "losses": losses,
            "breakeven_trades": breakeven_trades,
            "capital_invested": capital_invested,
            "portfolio_exposure": portfolio_exposure,
            "equity": equity,
            "cash": cash,
            "matched_cost_basis": realised_cost_basis,
            "matched_trades": matched_trades,
            "error": None,
        }

    except Exception as e:
        return {
            "profit_loss": 0.0,
            "return_percent": 0.0,
            "win_rate": 0.0,
            "trades_closed": 0,
            "wins": 0,
            "losses": 0,
            "breakeven_trades": 0,
            "capital_invested": 0.0,
            "portfolio_exposure": 0.0,
            "equity": 0.0,
            "cash": 0.0,
            "matched_cost_basis": 0.0,
            "matched_trades": [],
            "error": str(e),
        }
        
def execute_paper_trades(buy_signals, sell_signals):
    import pandas as pd
    from datetime import datetime
    import streamlit as st

    if emergency_stop.is_stopped():
        st.session_state.trade_messages.append(
            "🛑 Trade blocked: Emergency Stop is active."
        )
        return

    # =========================
    # 🟢 BUY EXECUTION
    # =========================
    for _, row in buy_signals.iterrows():

        ticker = row.get("Ticker", row.get("symbol", "UNKNOWN"))

        # ✅ 1. Validate position size FIRST
        if "Position Size" not in row or pd.isna(row["Position Size"]):
            st.session_state.trade_messages.append(
                f"BUY skipped for {ticker}: Position Size missing."
            )
            continue

        trade_amount = float(row["Position Size"])

        # ✅ 2. Validate price
        price = row.get("Price ($)", row.get("price", None))

        if price is None or price == 0:
            st.session_state.trade_messages.append(
                f"BUY skipped for {ticker}: Invalid price."
            )
            continue

        # ✅ 3. Risk check
        allowed, reason = risk_check_before_trade(
            ticker,
            trade_amount,
            market_df
        )

        if not allowed:
            st.session_state.trade_messages.append(
                f"BUY skipped for {ticker}: {reason}"
            )
            continue

        # ✅ 4. Avoid duplicate positions
        if ticker in st.session_state.positions:
            continue

        # ✅ 5. Cash check
        if st.session_state.cash < trade_amount:
            st.session_state.trade_messages.append(
                f"BUY skipped for {ticker}: Not enough cash."
            )
            continue

        # ✅ 6. SAFE calculation
        shares = trade_amount / price

        trade_plan = row.to_dict()

        # ✅ 7. Create order
        order = create_order(
            ticker=ticker,
            side="BUY",
            quantity=shares,
            trade_amount=trade_amount,
            price=price,
            asset_class=row.get("Asset Class", "UNKNOWN"),
            broker=row.get("Broker", "paper"),
            strategy=row.get("Strategy", "UNKNOWN"),
            confidence=row.get("AI Confidence %", 0),
            ai_trade_score=row.get("AI Trade Score", 0),
            priority=row.get("Priority", "N/A"),
            stop_loss=trade_plan.get("Stop Loss"),
            take_profit=trade_plan.get("Take Profit"),
        )

        order = mark_order_filled(
            order,
            filled_price=price,
            filled_quantity=shares
        )
        save_order(order)

        managed_position = initialize_position(
            {
                "shares": shares,
                "entry_price": price
            },
            stop_loss=trade_plan.get("Stop Loss"),
            take_profit=trade_plan.get("Take Profit")
        )

        # ✅ 8. Update portfolio
        st.session_state.positions[ticker] = managed_position
        st.session_state.cash -= trade_amount

        st.session_state.trade_log.append({
            "Order ID": order["order_id"],
            "Order Status": order["status"],
            "Ticker": ticker,
            "Action": "BUY",
            "Price": price,
            "Shares": round(shares, 4),
            "Amount": round(trade_amount, 2),
            "Reason": "AI BUY Signal"
        })

        log_trade(
            ticker=ticker,
            action="BUY",
            price=price,
            shares=shares,
            amount=trade_amount,
            confidence=float(row.get("AI Confidence %", 0)),
            trend_score=float(row.get("Trend Score", 0)),
            reason="AI BUY Signal",
            mode="LOCAL_PAPER",
        )

        # Notification is purely downstream of the fill above -- if
        # Telegram is unreachable or unconfigured this is a silent no-op
        # (see telegram_notifier.py), it can never affect the trade itself.
        telegram_notifier.notify_trade_fill(
            ticker=ticker,
            action="BUY",
            price=price,
            shares=shares,
            amount=trade_amount,
            asset_class=row.get("Asset Class", "UNKNOWN"),
            mode="LOCAL_PAPER",
            confidence=row.get("AI Confidence %"),
            trade_grade=row.get("Trade Grade"),
        )

        st.session_state.last_trade_time[ticker] = datetime.now()

        st.session_state.trade_messages.append(
            f"BUY sent for {ticker}: "
            f"${trade_amount:,.2f} filled at ${price:,.2f} (local paper)."
        )

    # =========================
    # 🔴 SELL EXECUTION
    # =========================
    for _, row in sell_signals.iterrows():

        ticker = row.get("Ticker", row.get("symbol", "UNKNOWN"))

        price = row.get("Price ($)", row.get("price", None))

        # ✅ Validate price
        if price is None or price == 0:
            st.session_state.trade_messages.append(
                f"SELL skipped for {ticker}: Invalid price."
            )
            continue

        # ✅ Must have position
        if ticker not in st.session_state.positions:
            st.session_state.trade_messages.append(
                f"SELL skipped for {ticker}: no open position."
            )
            continue

        position = st.session_state.positions[ticker]

        shares = float(position["shares"])
        entry_price = float(position["entry_price"])

        sale_proceeds = shares * price
        original_cost = shares * entry_price
        realized_pnl = sale_proceeds - original_cost

        sell_order = create_order(
            ticker=ticker,
            side="SELL",
            quantity=shares,
            trade_amount=sale_proceeds,
            price=price,
            asset_class=row.get("Asset Class", "UNKNOWN"),
            broker=row.get("Broker", "paper"),
            strategy=row.get("Strategy", "UNKNOWN"),
            confidence=row.get("AI Confidence %", 0),
            ai_trade_score=row.get("AI Trade Score", 0),
            priority=row.get("Priority", "N/A"),
            stop_loss=position.get("stop_loss"),
            take_profit=position.get("take_profit"),
        )

        sell_order = mark_order_filled(
            sell_order,
            filled_price=price,
            filled_quantity=shares,
        )
        save_order(sell_order)

        # ✅ Update cash
        st.session_state.cash += sale_proceeds

        st.session_state.trade_log.append({
            "Order ID": sell_order["order_id"],
            "Order Status": sell_order["status"],
            "Ticker": ticker,
            "Action": "SELL",
            "Price": round(price, 2),
            "Shares": round(shares, 4),
            "Amount": round(sale_proceeds, 2),
            "Entry Price": round(entry_price, 2),
            "Realized PnL": round(realized_pnl, 2),
            "Reason": "AI SELL Signal",
        })

        log_trade(
            ticker=ticker,
            action="SELL",
            price=price,
            shares=shares,
            amount=sale_proceeds,
            confidence=float(row.get("AI Confidence %", 0)),
            trend_score=float(row.get("Trend Score", 0)),
            reason="AI SELL Signal",
            mode="LOCAL_PAPER",
        )

        telegram_notifier.notify_trade_fill(
            ticker=ticker,
            action="SELL",
            price=price,
            shares=shares,
            amount=sale_proceeds,
            asset_class=row.get("Asset Class", "UNKNOWN"),
            mode="LOCAL_PAPER",
            confidence=row.get("AI Confidence %"),
            trade_grade=row.get("Trade Grade"),
            realized_pnl=realized_pnl,
        )

        # ✅ Remove position AFTER logging
        del st.session_state.positions[ticker]

        st.session_state.last_trade_time[ticker] = datetime.now()

        result_text = "profit" if realized_pnl >= 0 else "loss"

        st.session_state.trade_messages.append(
            f"SELL completed for {ticker}: "
            f"{round(shares, 4)} shares at ${price:,.2f} | "
            f"{result_text}: ${realized_pnl:,.2f}"
        )
def validate_alpaca_execution_environment():
    """
    Final broker-level safety validation before an Alpaca order is submitted.

    Returns:
        tuple[bool, str]: approval status and explanation.
    """
    if not LIVE_TRADING:
        return False, "Alpaca execution mode is not enabled."

    if AUTO_LIVE_TRADING_LOCKED:
        return False, "Automatic Alpaca execution is locked for safety."

    broker_health = check_broker_connection()

    if not broker_health.get("connected", False):
        return False, (
            "Alpaca connection unavailable: "
            f"{broker_health.get('error', 'Unknown broker error')}"
        )

    if broker_health.get("trading_blocked", False):
        return False, "The Alpaca account is currently blocked from trading."

    if not broker_health.get("market_open", False):
        return False, (
            "The US stock market is closed. "
            f"Next opening: {broker_health.get('next_market_open', 'Unknown')}"
        )

    entries_today = get_broker_trades_today_count()

    if entries_today >= MAX_TRADES_PER_DAY:
        return False, (
            f"Daily entry limit reached: "
            f"{entries_today}/{MAX_TRADES_PER_DAY}."
        )

    return True, "Alpaca execution environment approved."

def execute_alpaca_trades(buy_signals, sell_signals):
    """
    Execute approved BUY and SELL signals through Alpaca Paper Trading.

    Safety controls:
    - Prevent duplicate pending orders
    - Prevent duplicate positions
    - Apply portfolio and risk checks
    - Log submitted trades
    """
    if emergency_stop.is_stopped():
        st.session_state.trade_messages.append(
            "🛑 Trade blocked: Emergency Stop is active."
        )
        return

    try:
        alpaca_positions = get_open_positions()

        owned_symbols = {
            str(position.symbol).upper().strip()
            for position in alpaca_positions
        }

    except Exception as e:
        st.session_state.trade_messages.append(
            f"Could not load Alpaca positions: {e}"
        )
        return

    # =========================================================
    # BUY EXECUTION
    # =========================================================
    for _, row in buy_signals.iterrows():
        ticker = str(row["Ticker"]).upper().strip()
        buy_gate_allowed, buy_gate_reason = broker_execution_gate("BUY")

        if not buy_gate_allowed:
            st.session_state.trade_messages.append(
                f"BUY blocked for {ticker}: {buy_gate_reason}"
            )
            continue
        # Do not submit another order while one is active
        if broker_has_active_order(ticker):
            st.session_state.trade_messages.append(
                f"BUY skipped for {ticker}: Alpaca already has an active "
                f"order for this ticker."
            )
            continue

        # Do not buy an asset that is already held
        if (
            ticker in owned_symbols
            or broker_already_owns_symbol(ticker)
        ):
            st.session_state.trade_messages.append(
                f"BUY skipped for {ticker}: Alpaca already holds this stock."
            )
            continue

        try:
            trade_amount = calculate_trade_amount(
                row["AI Confidence %"],
                market_df
            )

            allowed, reason = risk_check_before_trade(
                ticker,
                trade_amount,
                market_df
            )

            if not allowed:
                st.session_state.trade_messages.append(
                    f"BUY skipped for {ticker}: {reason}"
                )
                continue

            allowed, reason = can_open_position(ticker)

            if not allowed:
                st.session_state.trade_messages.append(
                    f"BUY skipped for {ticker}: {reason}"
                )
                continue

            buy_stock(ticker, trade_amount)

            current_price = float(row["Price ($)"])
            estimated_shares = (
                trade_amount / current_price
                if current_price > 0
                else 0
            )

            order = create_order(
                ticker=ticker,
                side="BUY",
                quantity=estimated_shares,
                trade_amount=trade_amount,
                price=current_price,
                asset_class=row.get("Asset Class", "US_STOCKS"),
                broker="alpaca",
                strategy=row.get("Strategy", "UNKNOWN"),
                confidence=row.get("AI Confidence %", 0),
                ai_trade_score=row.get("AI Trade Score", 0),
                priority=row.get("Priority", "N/A"),
                stop_loss=row.get("Stop Loss"),
                take_profit=row.get("Take Profit"),
            )
            order = mark_order_filled(
                order, filled_price=current_price, filled_quantity=estimated_shares
            )
            save_order(order)

            log_trade(
                ticker=ticker,
                action="BUY",
                price=current_price,
                shares=estimated_shares,
                amount=trade_amount,
                confidence=float(row["AI Confidence %"]),
                trend_score=float(row["Trend Score"]),
                reason="AI BUY Signal",
                mode="LIVE_ALPACA_PAPER"
            )

            telegram_notifier.notify_trade_fill(
                ticker=ticker,
                action="BUY",
                price=current_price,
                shares=estimated_shares,
                amount=trade_amount,
                asset_class=row.get("Asset Class", "US_STOCKS"),
                mode="LIVE_ALPACA_PAPER",
                confidence=row.get("AI Confidence %"),
                trade_grade=row.get("Trade Grade"),
            )

            st.session_state.trade_messages.append(
                f"BUY sent for {ticker}: "
                f"${trade_amount:,.2f} submitted to Alpaca Paper Trading."
            )

            # Immediately protect against another purchase during this run
            owned_symbols.add(ticker)

        except Exception as e:
            failed_order = create_order(
                ticker=ticker,
                side="BUY",
                quantity=0,
                trade_amount=0,
                price=float(row.get("Price ($)", 0) or 0),
                asset_class=row.get("Asset Class", "US_STOCKS"),
                broker="alpaca",
                strategy=row.get("Strategy", "UNKNOWN"),
                confidence=row.get("AI Confidence %", 0),
                ai_trade_score=row.get("AI Trade Score", 0),
                priority=row.get("Priority", "N/A"),
            )
            save_order(mark_order_failed(failed_order, e))

            st.session_state.trade_messages.append(
                f"Alpaca BUY failed for {ticker}: {e}"
            )

    # Reload positions because BUY orders may have changed the account
    try:
        alpaca_positions = get_open_positions()
    except Exception:
        alpaca_positions = []

    # =========================================================
    # SELL EXECUTION
    # =========================================================
    for _, row in sell_signals.iterrows():
        ticker = str(row["Ticker"]).upper().strip()
        
        sell_gate_allowed, sell_gate_reason = broker_execution_gate("SELL")

        if not sell_gate_allowed:
            st.session_state.trade_messages.append(
                f"SELL blocked for {ticker}: {sell_gate_reason}"
            )
            continue
        if broker_has_active_order(ticker):
            st.session_state.trade_messages.append(
                f"SELL skipped for {ticker}: Alpaca already has an active "
                f"order for this ticker."
            )
            continue

        try:
            position_found = False

            for position in alpaca_positions:
                position_symbol = str(position.symbol).upper().strip()

                if position_symbol == ticker:
                    qty = float(position.qty)

                    if qty <= 0:
                        st.session_state.trade_messages.append(
                            f"SELL skipped for {ticker}: invalid position quantity."
                        )
                        position_found = True
                        break

                    sell_stock(ticker, qty)

                    current_price = float(row["Price ($)"])

                    sell_order = create_order(
                        ticker=ticker,
                        side="SELL",
                        quantity=qty,
                        trade_amount=qty * current_price,
                        price=current_price,
                        asset_class=row.get("Asset Class", "US_STOCKS"),
                        broker="alpaca",
                        strategy=row.get("Strategy", "UNKNOWN"),
                        confidence=row.get("AI Confidence %", 0),
                        ai_trade_score=row.get("AI Trade Score", 0),
                        priority=row.get("Priority", "N/A"),
                    )
                    sell_order = mark_order_filled(
                        sell_order, filled_price=current_price, filled_quantity=qty
                    )
                    save_order(sell_order)

                    log_trade(
                        ticker=ticker,
                        action="SELL",
                        price=current_price,
                        shares=qty,
                        amount=qty * current_price,
                        confidence=float(row["AI Confidence %"]),
                        trend_score=float(row["Trend Score"]),
                        reason="AI SELL Signal",
                        mode="LIVE_ALPACA_PAPER"
                    )

                    telegram_notifier.notify_trade_fill(
                        ticker=ticker,
                        action="SELL",
                        price=current_price,
                        shares=qty,
                        amount=qty * current_price,
                        asset_class=row.get("Asset Class", "US_STOCKS"),
                        mode="LIVE_ALPACA_PAPER",
                        confidence=row.get("AI Confidence %"),
                        trade_grade=row.get("Trade Grade"),
                    )

                    position_found = True

                    st.session_state.trade_messages.append(
                        f"SELL sent for {ticker}: "
                        f"{qty:.4f} shares submitted to Alpaca Paper Trading."
                    )

                    break

            if not position_found:
                st.session_state.trade_messages.append(
                    f"SELL skipped for {ticker}: no Alpaca position found."
                )

        except Exception as e:
            failed_order = create_order(
                ticker=ticker,
                side="SELL",
                quantity=0,
                trade_amount=0,
                price=float(row.get("Price ($)", 0) or 0),
                asset_class=row.get("Asset Class", "US_STOCKS"),
                broker="alpaca",
                strategy=row.get("Strategy", "UNKNOWN"),
                confidence=row.get("AI Confidence %", 0),
                ai_trade_score=row.get("AI Trade Score", 0),
                priority=row.get("Priority", "N/A"),
            )
            save_order(mark_order_failed(failed_order, e))

            st.session_state.trade_messages.append(
                f"Alpaca SELL failed for {ticker}: {e}"
            )


def execute_binance_trades(buy_signals, sell_signals):
    """
    Execute approved crypto BUY/SELL signals through Binance TESTNET.

    Simplified relative to execute_alpaca_trades: crypto trades 24/7 so
    there's no market-hours gating, and Binance testnet has no
    order-clock/broker-state concept like Alpaca does. Still applies the
    same portfolio risk checks (trade size, max positions) before sending
    anything.
    """
    import binance_broker

    if emergency_stop.is_stopped():
        st.session_state.trade_messages.append(
            "🛑 Trade blocked: Emergency Stop is active."
        )
        return

    try:
        binance_positions = binance_broker.get_positions()
        owned_symbols = {p["symbol"].upper().strip() for p in binance_positions}
    except Exception as e:
        st.session_state.trade_messages.append(
            f"Could not load Binance testnet positions: {e}"
        )
        return

    # =========================================================
    # BUY EXECUTION
    # =========================================================
    for _, row in buy_signals.iterrows():
        ticker = str(row["Ticker"]).upper().strip()

        if ticker in owned_symbols and not ALLOW_CRYPTO_PYRAMIDING:
            st.session_state.trade_messages.append(
                f"BUY skipped for {ticker}: Binance testnet already holds this asset."
            )
            continue

        try:
            trade_amount = calculate_trade_amount(
                row["AI Confidence %"],
                market_df
            )

            allowed, reason = risk_check_before_trade(
                ticker, trade_amount, market_df
            )
            if not allowed:
                st.session_state.trade_messages.append(
                    f"BUY skipped for {ticker}: {reason}"
                )
                continue

            allowed, reason = can_open_position(ticker)
            if not allowed:
                st.session_state.trade_messages.append(
                    f"BUY skipped for {ticker}: {reason}"
                )
                continue

            binance_order, fill_price, quantity = binance_broker.buy_crypto(ticker, trade_amount)

            oms_order = create_order(
                ticker=ticker,
                side="BUY",
                quantity=quantity,
                trade_amount=trade_amount,
                price=fill_price,
                asset_class="CRYPTO",
                broker="binance",
                strategy=row.get("Strategy", "UNKNOWN"),
                confidence=row.get("AI Confidence %", 0),
                ai_trade_score=row.get("AI Trade Score", 0),
                priority=row.get("Priority", "N/A"),
            )
            oms_order = mark_order_filled(
                oms_order, filled_price=fill_price, filled_quantity=quantity
            )
            oms_order["broker_order_id"] = (
                binance_order.get("id", "N/A") if isinstance(binance_order, dict) else "N/A"
            )
            save_order(oms_order)

            log_trade(
                ticker=ticker,
                action="BUY",
                price=fill_price,
                shares=quantity,
                amount=trade_amount,
                confidence=float(row["AI Confidence %"]),
                trend_score=float(row["Trend Score"]),
                reason="AI BUY Signal",
                mode="BINANCE_TESTNET",
            )

            st.session_state.trade_log.append({
                "Order ID": oms_order["broker_order_id"],
                "Order Status": "FILLED",
                "Ticker": ticker,
                "Action": "BUY",
                "Price": fill_price,
                "Shares": round(quantity, 6),
                "Amount": round(trade_amount, 2),
                "Reason": "AI BUY Signal (Binance testnet)",
            })

            telegram_notifier.notify_trade_fill(
                ticker=ticker,
                action="BUY",
                price=fill_price,
                shares=quantity,
                amount=trade_amount,
                asset_class="CRYPTO",
                mode="BINANCE_TESTNET",
                confidence=row.get("AI Confidence %"),
                trade_grade=row.get("Trade Grade"),
            )

            st.session_state.trade_messages.append(
                f"BUY sent for {ticker}: "
                f"${trade_amount:,.2f} submitted to Binance testnet."
            )

            owned_symbols.add(ticker)

        except Exception as e:
            failed_order = create_order(
                ticker=ticker,
                side="BUY",
                quantity=0,
                trade_amount=0,
                price=float(row.get("Price ($)", 0) or 0),
                asset_class="CRYPTO",
                broker="binance",
                strategy=row.get("Strategy", "UNKNOWN"),
                confidence=row.get("AI Confidence %", 0),
                ai_trade_score=row.get("AI Trade Score", 0),
                priority=row.get("Priority", "N/A"),
            )
            save_order(mark_order_failed(failed_order, e))

            st.session_state.trade_messages.append(
                f"Binance testnet BUY failed for {ticker}: {e}"
            )

    # Reload positions because BUY orders may have changed holdings
    try:
        binance_positions = binance_broker.get_positions()
    except Exception:
        binance_positions = []

    # =========================================================
    # SELL EXECUTION
    # =========================================================
    for _, row in sell_signals.iterrows():
        ticker = str(row["Ticker"]).upper().strip()

        try:
            position_found = False

            for position in binance_positions:
                if position["symbol"].upper().strip() != ticker:
                    continue

                qty = float(position["qty"])

                if qty <= 0:
                    st.session_state.trade_messages.append(
                        f"SELL skipped for {ticker}: invalid position quantity."
                    )
                    position_found = True
                    break

                binance_sell_order = binance_broker.sell_crypto(ticker, qty)

                current_price = float(row["Price ($)"])

                oms_order = create_order(
                    ticker=ticker,
                    side="SELL",
                    quantity=qty,
                    trade_amount=qty * current_price,
                    price=current_price,
                    asset_class="CRYPTO",
                    broker="binance",
                    strategy=row.get("Strategy", "UNKNOWN"),
                    confidence=row.get("AI Confidence %", 0),
                    ai_trade_score=row.get("AI Trade Score", 0),
                    priority=row.get("Priority", "N/A"),
                )
                oms_order = mark_order_filled(
                    oms_order, filled_price=current_price, filled_quantity=qty
                )
                oms_order["broker_order_id"] = (
                    binance_sell_order.get("id", "N/A")
                    if isinstance(binance_sell_order, dict) else "N/A"
                )
                save_order(oms_order)

                log_trade(
                    ticker=ticker,
                    action="SELL",
                    price=current_price,
                    shares=qty,
                    amount=qty * current_price,
                    confidence=float(row["AI Confidence %"]),
                    trend_score=float(row["Trend Score"]),
                    reason="AI SELL Signal",
                    mode="BINANCE_TESTNET",
                )

                st.session_state.trade_log.append({
                    "Order ID": oms_order["broker_order_id"],
                    "Order Status": "FILLED",
                    "Ticker": ticker,
                    "Action": "SELL",
                    "Price": current_price,
                    "Shares": round(qty, 6),
                    "Amount": round(qty * current_price, 2),
                    "Reason": "AI SELL Signal (Binance testnet)",
                })

                telegram_notifier.notify_trade_fill(
                    ticker=ticker,
                    action="SELL",
                    price=current_price,
                    shares=qty,
                    amount=qty * current_price,
                    asset_class="CRYPTO",
                    mode="BINANCE_TESTNET",
                    confidence=row.get("AI Confidence %"),
                    trade_grade=row.get("Trade Grade"),
                )

                position_found = True

                st.session_state.trade_messages.append(
                    f"SELL sent for {ticker}: "
                    f"{qty:.6f} units submitted to Binance testnet."
                )
                break

            if not position_found:
                st.session_state.trade_messages.append(
                    f"SELL skipped for {ticker}: no Binance testnet position found."
                )

        except Exception as e:
            failed_order = create_order(
                ticker=ticker,
                side="SELL",
                quantity=0,
                trade_amount=0,
                price=float(row.get("Price ($)", 0) or 0),
                asset_class="CRYPTO",
                broker="binance",
                strategy=row.get("Strategy", "UNKNOWN"),
                confidence=row.get("AI Confidence %", 0),
                ai_trade_score=row.get("AI Trade Score", 0),
                priority=row.get("Priority", "N/A"),
            )
            save_order(mark_order_failed(failed_order, e))

            st.session_state.trade_messages.append(
                f"Binance testnet SELL failed for {ticker}: {e}"
            )

# Which of this project's own FOREX/COMMODITIES tickers (from
# data/asset_universe.py) belong to which asset class -- needed here to
# enforce MAX_FOREX_POSITIONS/MAX_COMMODITIES_POSITIONS independently
# per class (same reasoning as MAX_CRYPTO_POSITIONS in config.py: each
# class gets its own budget so one doesn't starve the other). Kept as an
# explicit list rather than reading ASSET_UNIVERSE directly so this
# doesn't silently start trying to route a newly-added ticker through
# eToro before its symbol mapping (see etoro_broker.resolve_project_ticker)
# has actually been verified live.
_ETORO_ASSET_CLASS_TICKERS = {
    "FOREX": ["EURUSD=X", "GBPUSD=X", "USDJPY=X"],
    "COMMODITIES": ["GC=F", "CL=F", "SI=F"],
}


def execute_etoro_trades(buy_signals, sell_signals):
    """
    Execute approved FOREX/COMMODITIES BUY/SELL signals through eToro
    (Demo account -- see REQUIRE_ETORO_DEMO_ENVIRONMENT guard below).

    Modelled directly on execute_binance_trades() above (same reused
    risk/sizing/logging calls), with two eToro-specific differences:
      - eToro identifies positions by a numeric position_id, not by
        ticker -- SELL has to look the position up first to get the ID
        close_position() needs.
      - MAX_FOREX_POSITIONS/MAX_COMMODITIES_POSITIONS are enforced here
        directly against eToro's own live position list, since these
        signals bypass the local paper-trading engine entirely once
        ETORO_LIVE_TRADING is on -- the paper engine's own limit checks
        never see them.

    Live-tested 2026-08-03: an earlier version called
    etoro_broker.find_position_by_symbol() repeatedly -- once per "already
    holds" check, then again once per ticker in the asset class just to
    count open positions for the cap above -- and each call fetches
    eToro's portfolio fresh over the network. With several
    FOREX/COMMODITIES signals in one pass that was a dozen-plus
    sequential eToro API calls, and a single slow one (a real
    requests.exceptions.ReadTimeout, confirmed live) crashed the entire
    Streamlit page instead of just skipping that one trade. This now
    fetches the position list ONCE per execution pass (wrapped so a
    failure here aborts cleanly with a message rather than crashing) and
    reuses that local snapshot for every check below.

    Only ever called from a manual "Execute Trades" click -- see
    AUTO_ETORO_TRADING_LOCKED in the Auto-Trading section further down,
    which deliberately never calls this, per explicit user request
    (2026-08-03) that eToro trades require a human click, unlike
    stocks/crypto's optional auto-trading loop.
    """
    if emergency_stop.is_stopped():
        st.session_state.trade_messages.append(
            "🛑 Trade blocked: Emergency Stop is active."
        )
        return

    if REQUIRE_ETORO_DEMO_ENVIRONMENT and not etoro_broker.IS_DEMO:
        st.session_state.trade_messages.append(
            "🛑 eToro trade blocked: ETORO_ENVIRONMENT is not 'demo' and "
            "REQUIRE_ETORO_DEMO_ENVIRONMENT is True. This is a deliberate "
            "safety lock -- do not bypass it without an explicit, separate "
            "decision to go live on eToro."
        )
        return

    def _load_positions_by_symbol():
        """
        One network call, returns {eToro symbolFull: position dict}. On
        any failure (including a slow/timed-out request), returns None
        so callers can abort cleanly instead of crashing the page.
        """
        try:
            return {p["symbol"]: p for p in etoro_broker.get_positions()}
        except Exception as e:
            print(f"[execute_etoro_trades] could not load eToro positions: {e}")
            return None

    def _find_position(positions_by_symbol, project_ticker):
        etoro_symbol = etoro_broker.resolve_project_ticker(project_ticker)
        return positions_by_symbol.get(etoro_symbol)

    def _reconcile_after_failure(ticker, action):
        """
        A client-side error (almost always a ReadTimeout) does NOT tell us
        whether eToro actually processed the order -- the request can time
        out waiting for a response while the order still goes through on
        their end. Live-tested 2026-08-03: exactly this was checked
        manually via SSH after a batch of timeouts, and it genuinely varies
        (some timed-out orders had gone through, some hadn't). Blindly
        treating every failure as "nothing happened" risks a duplicate
        order on retry; blindly treating every failure as "it went through"
        risks never retrying a trade that really did fail. This makes that
        one-off manual check automatic, using find_position_by_symbol()
        (which does a fresh, un-cached portfolio fetch and raises on
        failure rather than swallowing it, unlike get_positions()) so the
        trade_messages log tells the user definitively whether it's safe
        to retry, instead of leaving that to a manual diagnostic every
        time.

        BUY and SELL have opposite "safe to retry" polarity: a position
        existing now means a BUY likely succeeded (don't retry) but a
        SELL/close likely failed (retry is fine); a position being absent
        means the reverse. Best-effort only -- if this check itself fails,
        it says so plainly rather than guessing.
        """
        try:
            position = etoro_broker.find_position_by_symbol(ticker)
        except Exception as reconcile_error:
            return (
                f" RECONCILIATION check also failed ({reconcile_error}) -- "
                "verify manually on the eToro dashboard before retrying."
            )

        has_position = position is not None

        if action == "BUY":
            if has_position:
                return (
                    " RECONCILIATION: a position for this ticker now exists "
                    "on eToro despite the error above -- the BUY likely "
                    "went through. Do NOT resubmit; check the eToro "
                    "dashboard first."
                )
            return (
                " RECONCILIATION: no matching position found on eToro -- "
                "the BUY most likely did not go through. Should be safe to "
                "retry."
            )

        # SELL
        if has_position:
            return (
                " RECONCILIATION: the position is still open on eToro -- "
                "the SELL/close most likely did not go through. Should be "
                "safe to retry."
            )
        return (
            " RECONCILIATION: no matching position found on eToro anymore "
            "-- the SELL/close likely went through despite the error above. "
            "Do NOT resubmit."
        )

    positions_by_symbol = _load_positions_by_symbol()
    if positions_by_symbol is None:
        st.session_state.trade_messages.append(
            "eToro trades skipped: could not load eToro positions "
            "(connection issue). Nothing was submitted -- try again."
        )
        return

    # =========================================================
    # BUY EXECUTION
    # =========================================================
    for _, row in buy_signals.iterrows():
        ticker = str(row["Ticker"]).upper().strip()
        asset_class = row.get("Asset Class", "FOREX")

        existing_position = _find_position(positions_by_symbol, ticker)

        if existing_position is not None:
            st.session_state.trade_messages.append(
                f"BUY skipped for {ticker}: eToro already holds this position."
            )
            continue

        # Per-asset-class position cap, checked against the same local
        # snapshot -- no extra network calls per candidate ticker.
        class_tickers = _ETORO_ASSET_CLASS_TICKERS.get(asset_class, [])
        max_positions = (
            MAX_FOREX_POSITIONS if asset_class == "FOREX" else MAX_COMMODITIES_POSITIONS
        )
        open_in_class = sum(
            1 for t in class_tickers
            if _find_position(positions_by_symbol, t) is not None
        )
        if open_in_class >= max_positions:
            st.session_state.trade_messages.append(
                f"BUY skipped for {ticker}: {asset_class} position limit "
                f"reached ({open_in_class}/{max_positions})."
            )
            continue

        try:
            trade_amount = calculate_trade_amount(
                row["AI Confidence %"],
                market_df
            )

            allowed, reason = risk_check_before_trade(
                ticker, trade_amount, market_df
            )
            if not allowed:
                st.session_state.trade_messages.append(
                    f"BUY skipped for {ticker}: {reason}"
                )
                continue

            result = etoro_broker.buy(ticker, trade_amount)
            fill_price = result.get("executed_price") or float(row.get("Price ($)", 0) or 0)

            oms_order = create_order(
                ticker=ticker,
                side="BUY",
                quantity=trade_amount / fill_price if fill_price else 0,
                trade_amount=trade_amount,
                price=fill_price,
                asset_class=asset_class,
                broker="etoro",
                strategy=row.get("Strategy", "UNKNOWN"),
                confidence=row.get("AI Confidence %", 0),
                ai_trade_score=row.get("AI Trade Score", 0),
                priority=row.get("Priority", "N/A"),
                stop_loss=row.get("Stop Loss"),
                take_profit=row.get("Take Profit"),
            )
            oms_order = mark_order_filled(
                oms_order,
                filled_price=fill_price,
                filled_quantity=trade_amount / fill_price if fill_price else 0,
            )
            oms_order["broker_order_id"] = result.get("position_id") or "PENDING"
            save_order(oms_order)

            log_trade(
                ticker=ticker,
                action="BUY",
                price=fill_price,
                shares=trade_amount / fill_price if fill_price else 0,
                amount=trade_amount,
                confidence=float(row["AI Confidence %"]),
                trend_score=float(row["Trend Score"]),
                reason="AI BUY Signal",
                mode="ETORO_DEMO",
            )

            if result.get("position_id") is not None:
                # Only notify Telegram once eToro has actually confirmed
                # the position -- previously this fired unconditionally
                # right after the order POST, so a submitted-but-unfilled
                # order (see the else branch below) produced a Telegram
                # "filled" message that turned out to be wrong. Live-
                # tested 2026-08-03: USDJPY=X and SI=F both sent "filled"
                # Telegram alerts while buy() itself reported no confirmed
                # position, and neither ticker showed up in the eToro
                # portfolio minutes later -- the notification was telling
                # the user something that hadn't actually happened.
                telegram_notifier.notify_trade_fill(
                    ticker=ticker,
                    action="BUY",
                    price=fill_price,
                    shares=trade_amount / fill_price if fill_price else 0,
                    amount=trade_amount,
                    asset_class=asset_class,
                    mode="ETORO_DEMO",
                    confidence=row.get("AI Confidence %"),
                    trade_grade=row.get("Trade Grade"),
                )
                # trailing_stop_set is set by etoro_broker.buy() only for
                # FOREX/COMMODITIES (leveraged CFD) trades -- it's absent
                # entirely for stocks/crypto orders routed through the same
                # code path, hence .get() rather than a plain lookup.
                trailing_note = (
                    " Trailing stop enabled -- profit will lock in as price moves in our favor."
                    if result.get("trailing_stop_set")
                    else ""
                )
                st.session_state.trade_messages.append(
                    f"BUY sent for {ticker}: ${trade_amount:,.2f} filled on "
                    f"eToro Demo (position {result['position_id']})."
                    f"{trailing_note}"
                )
                # Keep the local snapshot in sync for the rest of this
                # loop -- otherwise a second BUY signal in the same
                # asset class within this same pass wouldn't see the
                # position that was just opened, and the position-cap
                # check above would undercount.
                etoro_symbol = etoro_broker.resolve_project_ticker(ticker)
                positions_by_symbol[etoro_symbol] = {
                    "symbol": ticker,
                    "qty": trade_amount,
                    "position_id": result["position_id"],
                    "direction": "LONG",
                    "open_price": result.get("executed_price"),
                    "current_price": None,
                    "net_profit": None,
                }
            else:
                # Matches buy()'s own docstring: a stock-style order placed
                # outside its market's hours won't have a position_id yet.
                # Forex/commodities trade far closer to 24/5, so this is
                # unexpected for them specifically and worth flagging.
                st.session_state.trade_messages.append(
                    f"BUY sent for {ticker} on eToro Demo, but no position "
                    f"confirmed yet -- check the eToro dashboard before "
                    f"assuming it filled. Raw order: {result['raw']}"
                )

        except Exception as e:
            failed_order = create_order(
                ticker=ticker,
                side="BUY",
                quantity=0,
                trade_amount=0,
                price=float(row.get("Price ($)", 0) or 0),
                asset_class=asset_class,
                broker="etoro",
                strategy=row.get("Strategy", "UNKNOWN"),
                confidence=row.get("AI Confidence %", 0),
                ai_trade_score=row.get("AI Trade Score", 0),
                priority=row.get("Priority", "N/A"),
            )
            save_order(mark_order_failed(failed_order, e))

            st.session_state.trade_messages.append(
                f"eToro BUY failed for {ticker}: {e}"
                + _reconcile_after_failure(ticker, "BUY")
            )

        # Small pause between tickers -- live-tested 2026-08-03: a batch of
        # 3-4 FOREX/COMMODITIES signals processed back-to-back (each one
        # already doing up to 6 of its own requests via buy()'s poll loop)
        # produced a cluster of ReadTimeouts, including one where the
        # reconciliation check itself also timed out. Spacing requests out
        # a little reduces how hard this loop bursts eToro's API in a
        # short window -- cheap insurance against self-inflicted timeouts,
        # separate from the per-request timeout bump above.
        time.sleep(2)

    # Reload once, for real, since BUY orders above may have changed
    # eToro's position list -- same reasoning as execute_alpaca_trades()/
    # execute_binance_trades() reloading before their own SELL loops. If
    # this fails, fall back to the (possibly slightly stale) snapshot
    # from the BUY loop rather than aborting SELLs entirely.
    reloaded = _load_positions_by_symbol()
    if reloaded is not None:
        positions_by_symbol = reloaded

    # =========================================================
    # SELL EXECUTION
    # =========================================================
    for _, row in sell_signals.iterrows():
        ticker = str(row["Ticker"]).upper().strip()
        asset_class = row.get("Asset Class", "FOREX")

        try:
            position = _find_position(positions_by_symbol, ticker)

            if position is None:
                st.session_state.trade_messages.append(
                    f"SELL skipped for {ticker}: no eToro position found."
                )
                continue

            close_result = etoro_broker.close_position(position["position_id"])

            current_price = float(row.get("Price ($)", 0) or position.get("open_price") or 0)
            qty = position["qty"] / current_price if current_price else 0

            oms_order = create_order(
                ticker=ticker,
                side="SELL",
                quantity=qty,
                trade_amount=position["qty"],
                price=current_price,
                asset_class=asset_class,
                broker="etoro",
                strategy=row.get("Strategy", "UNKNOWN"),
                confidence=row.get("AI Confidence %", 0),
                ai_trade_score=row.get("AI Trade Score", 0),
                priority=row.get("Priority", "N/A"),
            )
            oms_order = mark_order_filled(
                oms_order, filled_price=current_price, filled_quantity=qty
            )
            oms_order["broker_order_id"] = position["position_id"]
            save_order(oms_order)

            log_trade(
                ticker=ticker,
                action="SELL",
                price=current_price,
                shares=qty,
                amount=position["qty"],
                confidence=float(row["AI Confidence %"]),
                trend_score=float(row["Trend Score"]),
                reason="AI SELL Signal",
                mode="ETORO_DEMO",
            )

            telegram_notifier.notify_trade_fill(
                ticker=ticker,
                action="SELL",
                price=current_price,
                shares=qty,
                amount=position["qty"],
                asset_class=asset_class,
                mode="ETORO_DEMO",
                confidence=row.get("AI Confidence %"),
                trade_grade=row.get("Trade Grade"),
            )

            st.session_state.trade_messages.append(
                f"SELL sent for {ticker}: position {position['position_id']} "
                f"closed on eToro Demo. Raw: {close_result}"
            )

        except Exception as e:
            failed_order = create_order(
                ticker=ticker,
                side="SELL",
                quantity=0,
                trade_amount=0,
                price=float(row.get("Price ($)", 0) or 0),
                asset_class=asset_class,
                broker="etoro",
                strategy=row.get("Strategy", "UNKNOWN"),
                confidence=row.get("AI Confidence %", 0),
                ai_trade_score=row.get("AI Trade Score", 0),
                priority=row.get("Priority", "N/A"),
            )
            save_order(mark_order_failed(failed_order, e))

            st.session_state.trade_messages.append(
                f"eToro SELL failed for {ticker}: {e}"
                + _reconcile_after_failure(ticker, "SELL")
            )

        # Same reasoning as the BUY loop above -- space requests out to
        # avoid bursting eToro's API.
        time.sleep(2)


def filter_by_asset_class(df, asset_class):
    """
    Safely filter a signals DataFrame by Asset Class.

    (DataFrame.get("Asset Class", default) does NOT safely broadcast a
    default value into a row-wise boolean comparison when the column is
    missing -- it returns the raw default scalar instead of a Series,
    which breaks df[...] indexing. This does it correctly.)
    """
    if df is None or df.empty:
        return df
    if "Asset Class" not in df.columns:
        # No asset class info at all -- treat as US_STOCKS by default,
        # since that's every row this app has historically dealt with.
        return df if asset_class == "US_STOCKS" else df.iloc[0:0]
    return df[df["Asset Class"] == asset_class].copy()


# Asset classes with no real broker integration yet (see the comments in
# data/asset_universe.py). They always trade through the same local
# paper-trading engine as US_STOCKS -- fake session-state cash and
# positions -- regardless of the LIVE_TRADING toggle, which only ever
# concerns stocks (via Alpaca) and crypto (always real, via Binance
# testnet, handled separately). Add a new asset class here the moment
# its ASSET_UNIVERSE entry is enabled but before its real broker exists,
# and every paper-trading call site below picks it up automatically.
PAPER_ONLY_ASSET_CLASSES = ["FOREX", "COMMODITIES"]


def collect_paper_only_signals(signals_df):
    """
    Pull out every row belonging to a PAPER_ONLY_ASSET_CLASSES asset
    class from a signals DataFrame, combined into one frame.
    """
    if signals_df is None or signals_df.empty:
        return pd.DataFrame()

    frames = [
        filter_by_asset_class(signals_df, asset_class)
        for asset_class in PAPER_ONLY_ASSET_CLASSES
    ]
    frames = [df for df in frames if df is not None and not df.empty]

    if not frames:
        return signals_df.iloc[0:0]

    return pd.concat(frames, ignore_index=True)


def combine_for_paper_engine(stock_signals, paper_only_signals):
    """
    Merge US_STOCKS signals with every PAPER_ONLY_ASSET_CLASSES signal
    into a single frame ready for execute_paper_trades().
    """
    if paper_only_signals is None or paper_only_signals.empty:
        return stock_signals

    frames = [df for df in [stock_signals, paper_only_signals] if not df.empty]

    if not frames:
        return stock_signals

    return pd.concat(frames, ignore_index=True)


def apply_risk_management(market_df):
    if LIVE_TRADING:
        try:
            alpaca_positions = get_open_positions()

            if "highest_profit" not in st.session_state:
                st.session_state.highest_profit = {}

            for position in alpaca_positions:
                ticker = position.symbol
                entry_price = float(position.avg_entry_price)
                current_price = float(position.current_price)
                qty = float(position.qty)

                change_percent = ((current_price / entry_price) - 1) * 100

                previous_high = st.session_state.highest_profit.get(ticker, change_percent)
                st.session_state.highest_profit[ticker] = max(previous_high, change_percent)

                highest_profit = st.session_state.highest_profit[ticker]
                trailing_exit_level = highest_profit - TRAILING_PROFIT_DROP

                # STOP_LOSS_PERCENT/TAKE_PROFIT_PERCENT come from config.py
                # as fractions (0.03 = "3%", per the inline comment there),
                # but change_percent above is a real percentage number
                # (e.g. -3.5 meaning -3.5%) -- comparing them directly was
                # off by 100x, so a "3%" stop-loss was actually triggering
                # at a 0.03 PERCENTAGE-POINT move (a rounding error's worth
                # of price noise), not an actual 3% move. This is almost
                # certainly the real reason positions were being closed
                # within seconds of opening rather than the intended band
                # being merely "tight". TRAILING_PROFIT_START/DROP below
                # are already defined directly in percentage-points (1.5,
                # 0.75) so they don't need this conversion.
                if change_percent <= -(STOP_LOSS_PERCENT * 100):
                    sell_stock(ticker, qty)
                    st.session_state.trade_messages.append(
                        f"STOP LOSS triggered for {ticker}. Sold {round(qty, 4)} shares at ${round(current_price, 2)}"
                    )
                    del st.session_state.highest_profit[ticker]

                elif change_percent >= (TAKE_PROFIT_PERCENT * 100):
                    sell_stock(ticker, qty)
                    st.session_state.trade_messages.append(
                        f"TAKE PROFIT triggered for {ticker}. Sold {round(qty, 4)} shares at ${round(current_price, 2)}"
                    )
                    del st.session_state.highest_profit[ticker]

                elif highest_profit >= TRAILING_PROFIT_START and change_percent <= trailing_exit_level:
                    sell_stock(ticker, qty)
                    st.session_state.trade_messages.append(
                        f"TRAILING PROFIT LOCK triggered for {ticker}. Highest profit was {round(highest_profit, 2)}%, sold at {round(change_percent, 2)}%"
                    )
                    del st.session_state.highest_profit[ticker]

        except Exception as e:
            st.session_state.trade_messages.append(
                f"Live risk management failed: {e}"
            )

        return

    for ticker, position in list(st.session_state.positions.items()):
        latest_price = market_df.loc[
            market_df["Ticker"] == ticker,
            "Price ($)"
        ].values

        if len(latest_price) == 0:
            continue

        current_price = float(latest_price[0])

        position = update_position(position, current_price)
        st.session_state.positions[ticker] = position

        should_exit, exit_reason = check_position_exit(position, current_price)

        if should_exit:
            shares = position["shares"]
            amount = shares * current_price

            st.session_state.cash += amount

            st.session_state.trade_log.append({
                "Ticker": ticker,
                "Action": "SELL",
                "Price": current_price,
                "Shares": round(shares, 4),
                "Amount": round(amount, 2),
                "Reason": exit_reason
            })

            st.session_state.trade_messages.append(
                f"{exit_reason} triggered for {ticker}. Sold {round(shares, 4)} shares at ${round(current_price, 2)}"
            )

            del st.session_state.positions[ticker]


def apply_crypto_risk_management():
    """
    Automatic stop-loss/take-profit for Binance testnet crypto positions,
    mirroring what apply_risk_management() does for Alpaca stock positions
    above. Added 2026-07-30 alongside turning ALLOW_CRYPTO_PYRAMIDING off
    -- previously crypto had no automatic exit at all, so positions only
    ever grew (via pyramiding) and essentially never closed, which is why
    Binance testnet USDT only ever drained and never came back.

    Binance's wallet-balance API (binance_broker.get_positions()) has no
    concept of "entry price" -- it's a spot balance, not a tracked
    position like Alpaca gives us -- so entry price is looked up from the
    most recent FILLED BUY order for that symbol in the persisted order
    book (engines/order_manager.py). This is only reliable with pyramiding
    off: with at most one open lot per coin, "most recent BUY fill" is
    unambiguous. If pyramiding is ever turned back on, this would need to
    become a proper weighted-average cost basis instead.
    """
    import binance_broker
    import engines.order_manager as order_manager

    try:
        positions = binance_broker.get_positions()
    except Exception as e:
        st.session_state.trade_messages.append(
            f"Crypto risk management skipped: could not load Binance "
            f"positions: {e}"
        )
        return

    if not positions:
        return

    try:
        recent_orders = order_manager.load_orders(limit=200)
    except Exception as e:
        st.session_state.trade_messages.append(
            f"Crypto risk management skipped: could not load order "
            f"history: {e}"
        )
        return

    for position in positions:
        ticker = str(position["symbol"]).upper().strip()
        qty = float(position["qty"])

        entry_order = next(
            (
                o for o in recent_orders
                if str(o.get("ticker", "")).upper().strip() == ticker
                and str(o.get("broker", "")).lower() == "binance"
                and str(o.get("side", "")).upper() == "BUY"
                and str(o.get("status", "")).upper() == "FILLED"
                and o.get("filled_price")
            ),
            None,
        )

        if entry_order is None:
            continue

        entry_price = float(entry_order["filled_price"])
        if entry_price <= 0:
            continue

        try:
            current_price = binance_broker.get_current_price(ticker)
        except Exception:
            continue

        change_percent = ((current_price / entry_price) - 1) * 100

        if change_percent <= -(STOP_LOSS_PERCENT * 100):
            exit_reason = "STOP LOSS"
        elif change_percent >= (TAKE_PROFIT_PERCENT * 100):
            exit_reason = "TAKE PROFIT"
        else:
            continue

        try:
            binance_broker.sell_crypto(ticker, qty)
        except Exception as e:
            st.session_state.trade_messages.append(
                f"Crypto {exit_reason} failed for {ticker}: {e}"
            )
            continue

        oms_order = create_order(
            ticker=ticker,
            side="SELL",
            quantity=qty,
            trade_amount=qty * current_price,
            price=current_price,
            asset_class="CRYPTO",
            broker="binance",
            strategy="Risk Management",
            confidence=0,
            ai_trade_score=0,
            priority="N/A",
        )
        oms_order = mark_order_filled(
            oms_order, filled_price=current_price, filled_quantity=qty
        )
        save_order(oms_order)

        realized_pnl = qty * (current_price - entry_price)

        log_trade(
            ticker=ticker,
            action="SELL",
            price=current_price,
            shares=qty,
            amount=qty * current_price,
            confidence=0,
            trend_score=0,
            reason=exit_reason,
            mode="BINANCE_TESTNET",
        )

        telegram_notifier.notify_trade_fill(
            ticker=ticker,
            action="SELL",
            price=current_price,
            shares=qty,
            amount=qty * current_price,
            asset_class="CRYPTO",
            mode="BINANCE_TESTNET",
            realized_pnl=realized_pnl,
        )

        st.session_state.trade_messages.append(
            f"{exit_reason} triggered for {ticker}. Sold "
            f"{round(qty, 6)} units at ${round(current_price, 2)} "
            f"(entry ${round(entry_price, 2)})."
        )


# NOTE: get_exposure_percent used to be redefined here, shadowing the
# import from engines.risk_engine (above). That local copy always queried
# the real Alpaca account directly, regardless of LIVE_TRADING, so it fed
# wrong numbers into position sizing while in Local Paper Trading mode.
# Removed -- the imported engines.risk_engine.get_exposure_percent already
# branches on LIVE_TRADING internally and is the single source of truth.


def calculate_performance(market_df):
    journal_metrics = performance_engine.calculate_performance_metrics()
    open_cost_basis = performance_engine.get_open_positions_cost_basis()

    # Build the set of tickers ACTUALLY held right now (not just ever
    # unmatched-bought in journal history, which includes stale/phantom
    # entries from before session resets wiped local state).
    real_holdings = {}

    for ticker, position in st.session_state.positions.items():
        real_holdings[ticker] = float(position.get("shares", 0))

    try:
        import binance_broker
        for position in binance_broker.get_positions():
            real_holdings[position["symbol"]] = float(position["qty"])
    except Exception:
        pass

    # Real unrealized P&L: current value of ACTUALLY-held positions minus
    # what was actually paid for them. Journal-derived average entry price
    # is used when available; falls back to local entry_price for stocks
    # (tracked directly in session state) rather than fabricating a cost.
    unrealized_value = 0.0
    unrealized_cost = 0.0

    for ticker, real_shares in real_holdings.items():
        if real_shares <= 1e-9:
            continue

        price_rows = market_df.loc[market_df["Ticker"] == ticker, "Price ($)"]
        if price_rows.empty:
            continue
        current_price = float(price_rows.iloc[0])

        lot = open_cost_basis.get(ticker)
        if lot and lot["shares"] > 1e-9:
            avg_entry_price = lot["cost_basis"] / lot["shares"]
        else:
            local_position = st.session_state.positions.get(ticker)
            avg_entry_price = (
                float(local_position["entry_price"])
                if local_position else current_price  # no known cost -> assume no P&L rather than fabricate one
            )

        unrealized_value += real_shares * current_price
        unrealized_cost += real_shares * avg_entry_price

    unrealized_pnl = unrealized_value - unrealized_cost

    pnl = unrealized_pnl + journal_metrics["total_pnl"]  # unrealized + realized
    return_percent = (unrealized_pnl / unrealized_cost * 100) if unrealized_cost > 0 else 0.0

    return {
        "PnL": pnl,
        "Return %": return_percent,
        "Wins": journal_metrics["wins"],
        "Losses": journal_metrics["losses"],
        "Win Rate": journal_metrics["win_rate"],
        "Trades Closed": journal_metrics["trades_closed"],
        "Realized PnL": journal_metrics["total_pnl"],
        "Unrealized PnL": unrealized_pnl,
        "Capital Invested": unrealized_cost,
        "Profit Factor": journal_metrics["profit_factor"],
        "Expectancy": journal_metrics["expectancy"],
        "Max Drawdown": journal_metrics["max_drawdown"],
    }


def persist_account():
    """
    Write this session's current cash/positions/equity_history/
    AUTO_TRADING/last_trade_time back to shared persistent storage. Call
    this after anything that mutates them, so any other session (a
    different tab/device) sees the change the next time it loads. See
    the checkpoints this is called from for exactly which code paths
    that covers.
    """
    account_store.save_account(
        cash=st.session_state.cash,
        positions=st.session_state.positions,
        equity_history=st.session_state.equity_history,
        auto_trading=st.session_state.AUTO_TRADING,
        last_trade_time=st.session_state.last_trade_time,
    )


def update_equity_history(portfolio_value):
    st.session_state.equity_history.append({
        "Time": datetime.now().strftime("%H:%M:%S"),
        "Portfolio Value": round(portfolio_value, 2)
    })

    if len(st.session_state.equity_history) > 50:
        st.session_state.equity_history = st.session_state.equity_history[-50:]


st.title("🤖 OrderTrade AI")
st.write("Live market dashboard for AI trading decisions")

if emergency_stop.is_stopped():
    st.error(
        f"🛑 EMERGENCY STOP ACTIVE — all trade execution is blocked "
        f"(manual and auto). {emergency_stop.stopped_since()}"
    )
    if st.button("✅ Resume Trading", type="primary"):
        emergency_stop.deactivate()
        st.rerun()
else:
    if st.button("🛑 EMERGENCY STOP — Halt All Trading"):
        emergency_stop.activate("Manually triggered from dashboard")
        st.rerun()

st.divider()

if not os.path.exists(MODEL_PATH) or not os.path.exists(FEATURES_PATH):
    st.error("AI model not found. Please run: python train_model.py")
    st.stop()

model = joblib.load(MODEL_PATH)
features = joblib.load(FEATURES_PATH)

rows = []

# Signal generation always runs (independent of the AUTO_TRADING
# auto-execute toggle below) so the watchlist and manual Execute button
# always have current data to work with.
with st.spinner("Loading live market data and AI predictions..."):
    for asset in asset_list:
        ticker = asset["symbol"]
        asset_class = asset["asset_class"]
        broker_name = asset["broker"]

        try:
            row = get_ai_signal(ticker, model, features)
            row["Asset Class"] = asset_class
            row["Broker"] = broker_name
            rows.append(row)

        except Exception as e:
            print(f"❌ ERROR for {ticker}: {e}")
            rows.append({
                "Ticker": ticker,
                "Asset Class": asset_class,
                "Broker": broker_name,
                "Price ($)": None,
                "Daily Change %": None,
                "AI Confidence %": None,
                "Signal": "ERROR"
            })

market_df = pd.DataFrame(rows)

market_df["Strategy"] = market_df.apply(identify_strategy, axis=1)
market_df["Strategy Score"] = market_df.apply(score_strategy, axis=1)

trade_plans = market_df.apply(create_trade_plan, axis=1)
trade_plans_df = pd.DataFrame(list(trade_plans))

market_df = pd.concat([market_df, trade_plans_df], axis=1)

market_df["AI Trade Score"] = market_df.apply(calculate_trade_score, axis=1)

approvals = market_df.apply(
    lambda row: approve_trade(row, open_positions_count=len(st.session_state.positions)),
    axis=1
)

market_df["Trade Approved"] = approvals.apply(lambda x: x[0])
market_df["Approval Reason"] = approvals.apply(lambda x: x[1])

market_df["Priority"] = market_df.apply(calculate_priority, axis=1)

# Apply risk-management values first
apply_risk_management(market_df)
apply_crypto_risk_management()

# Calculate current portfolio information
portfolio_value = calculate_portfolio_value(market_df)
invested_value = get_open_positions_value(market_df)
exposure = get_exposure_percent(market_df)

# Determine the current market regime
market_regime, regime_score = get_market_regime()

# Calculate position size BEFORE creating the trade queue
market_df["Position Size"] = market_df.apply(
    lambda row: calculate_position_size(
        portfolio_value=portfolio_value,
        confidence=float(row.get("AI Confidence %", 0)),
        strategy_score=float(row.get("Strategy Score", 0)),
        regime=market_regime,
        exposure_percent=float(exposure)
    ),
    axis=1
)

# Create the queue only after Position Size exists
trade_queue = sort_trade_queue(market_df)

asset_allocation = calculate_asset_allocation(
    st.session_state.positions,
    market_df
)

portfolio_approved_trades, portfolio_rejected_trades = filter_trades_by_portfolio_limits(
    trade_queue,
    st.session_state.positions,
    market_df,
    portfolio_value,
    default_trade_amount=1000
)

portfolio_approved_df = pd.DataFrame(portfolio_approved_trades)

if len(portfolio_approved_df) == 0 or "Asset Class" not in portfolio_approved_df.columns:
    executable_trades = pd.DataFrame()
    blocked_trades = pd.DataFrame()
else:
    executable_trades, blocked_trades = filter_executable_trades(
        portfolio_approved_df,
        # PAPER_ONLY_ASSET_CLASSES (forex, commodities) included: routed
        # through the same local paper-trading engine as US_STOCKS below
        # (see asset_universe.py comment) -- no real broker for them yet,
        # so they're not treated any differently from stocks here.
        allowed_asset_classes=["US_STOCKS", "CRYPTO"] + PAPER_ONLY_ASSET_CLASSES
    )

apply_risk_management(market_df)
apply_crypto_risk_management()

portfolio_value = calculate_portfolio_value(market_df)
invested_value = get_open_positions_value(market_df)
asset_allocation = calculate_asset_allocation(
    st.session_state.positions,
    market_df
)
preview_allocation = preview_allocation_after_trades(
    st.session_state.positions,
    market_df,
    executable_trades,
    portfolio_value,
    default_trade_amount=1000
)
exposure = get_exposure_percent(market_df)
performance = calculate_performance(market_df)

update_equity_history(portfolio_value)

# Catches anything apply_risk_management() did above (stop loss / take
# profit / trailing exits fire on every pass, not just from the buttons
# below) plus this pass's equity tick, regardless of whether a trade was
# ever attempted this run.
persist_account()

col1, col2, col3 = st.columns(3)

with col1:
    if LIVE_TRADING and account:
        st.metric(
            "Portfolio Value",
            f"${float(account.portfolio_value):,.2f}"
        )
    else:
        st.metric(
            "Portfolio Value",
            f"${portfolio_value:,.2f}"
        )

with col2:
    if LIVE_TRADING and account:
        st.metric(
            "Cash",
            f"${float(account.cash):,.2f}"
        )
    else:
        st.metric(
            "Cash",
            f"${st.session_state.cash:,.2f}"
        )

with col3:
    st.metric(
        "Mode",
        "Alpaca Paper Trading" if LIVE_TRADING else "Local Paper Trading"
    )
    
if not LIVE_TRADING:
    if st.button("Reset Local Paper Trading Account"):

        # FULL RESET (UI + ENGINE SAFE)
        st.session_state.cash = 100000

        st.session_state.positions = {}
        st.session_state.trade_log = []
        st.session_state.equity_history = []
        st.session_state.last_trade_time = {}
        st.session_state.trade_messages = []

        # ADD THESE (you were missing them)
        st.session_state.orders = []
        st.session_state.portfolio_history = []

        st.session_state.performance = {
            "profit": 0,
            "trades": 0,
            "wins": 0
        }

        # 🚨 CRITICAL: RESET ENGINE OBJECT
        if "paper_engine" in st.session_state:
            del st.session_state.paper_engine

        # Reset the shared account too -- otherwise this session shows
        # $100k locally, but any other device still loads the old
        # pre-reset balance from persistent storage.
        account_store.reset_account(starting_cash=100000)

        st.success("Portfolio FULLY reset")

        st.rerun()
else:
    st.caption(
        "Account values are controlled by Alpaca Paper Trading. "
        "The local reset button is disabled in broker mode."
    )
    
st.divider()

# ===========================
# Portfolio Risk Dashboard
# ===========================

risk_level, risk_multiplier = get_market_risk_level(market_df)

portfolio_value = calculate_portfolio_value(market_df)

# NOTE: this used to be recomputed here as (portfolio_value - cash) /
# portfolio_value, a *fraction* (0-1), separate from -- and inconsistent
# with -- the get_exposure_percent() *percentage* (0-100) used by the
# actual risk gate in risk_check_before_trade(). That mismatch is why the
# dashboard and the Performance section used to show different exposure
# numbers. Reusing get_exposure_percent() here keeps a single source of
# truth, already scaled 0-100 and already excluding pre-existing/untracked
# crypto wallet balance (see get_bot_owned_crypto_value in risk_engine.py).
exposure = get_exposure_percent(market_df)

live_metrics = get_live_account_metrics()
if LIVE_TRADING:
    trades_today_count = get_broker_trades_today_count()
else:
    trades_today_count = len(st.session_state.trade_log)

if LIVE_TRADING:
    broker_entries_today = get_broker_trades_today_count()

    remaining_trades = max(
        0,
        MAX_TRADES_PER_DAY - broker_entries_today
    )
else:
    local_buy_entries_today = sum(
        1
        for trade in st.session_state.trade_log
        if str(trade.get("Action", "")).upper() == "BUY"
    )

    remaining_trades = max(
        0,
        MAX_TRADES_PER_DAY - local_buy_entries_today
    )

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("⚠️ Market Risk", risk_level)

with col2:
    if LIVE_TRADING:
        displayed_exposure = float(live_metrics.get("exposure", 0))
    else:
        # exposure is already a 0-100 percentage from get_exposure_percent()
        displayed_exposure = exposure

    st.metric(
        "💼 Portfolio Exposure",
        f"{displayed_exposure:.1f}%"
    )

col4, col5 = st.columns(2)

with col4:
    if LIVE_TRADING:
        displayed_cash = float(live_metrics.get("cash", 0))
    else:
        displayed_cash = float(st.session_state.cash)

    st.metric(
        "💰 Available Cash",
        f"${displayed_cash:,.2f}"
    )

with col5:
    st.metric(
        "📈 Remaining Trades",
        remaining_trades
    )
col6, col7 = st.columns(2)

with col6:
    st.metric(
        "🌍 Market Regime",
        market_regime
    )

with col7:
    st.metric(
        "📊 Regime Score",
        f"{regime_score}/100"
    )
    market_df["Position Size"] = market_df.apply(
    lambda row: calculate_position_size(
        portfolio_value=portfolio_value,
        confidence=row.get("AI Confidence %", 0),
        strategy_score=row.get("Strategy Score", 0),
        regime=market_regime,
        exposure_percent=exposure
    ),
    axis=1
)
st.divider()

st.subheader("📈 Live Market Watchlist")
st.dataframe(market_df, width="stretch")

st.divider()

st.subheader("🧠 AI Decision Engine")

# 2026-08-06: this table previously had no timestamp anywhere on it, unlike
# the "Last Execution Attempt" section further down (which shows
# "Processed at: ..."). That's confusing on a page that auto-refreshes --
# a user comparing this table against an older execution result has no way
# to tell the two apart came from different points in time, and it can look
# like tickers vanished or appeared for no reason when really the AI's
# signals just moved between refreshes. Mirrors the exact "time" format
# used at the actual execution-result "Processed at:" caption below
# (datetime.now().strftime("%Y-%m-%d %H:%M:%S")) so the two timestamps are
# directly comparable at a glance.
st.caption(f"Signals generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# =========================================
# 🔥 ENSURE REQUIRED COLUMNS EXIST (ADD HERE)
# =========================================
required_cols = [
    "AI Confidence %",
    "Strategy Score",
    "AI Trade Score",
    "Daily Change %",
    "Priority"
]

# Ensure required columns
for col in required_cols:
    if col not in market_df.columns:
        market_df[col] = 0

dynamic_buy_confidence = get_dynamic_buy_confidence(market_df)

# =========================
# Ensure column names are clean strings
# =========================
market_df.columns = market_df.columns.astype(str)

# =========================
# SPLIT SIGNALS (using the REAL, already-filtered/approved trades —
# NOT a raw copy of market_df. executable_trades was computed earlier
# via filter_executable_trades(), after approval + portfolio limits.)
# =========================
if executable_trades is None or executable_trades.empty or "Signal" not in executable_trades.columns:
    buy_signals = pd.DataFrame()
    sell_signals = pd.DataFrame()
else:
    buy_signals = executable_trades.loc[
        executable_trades["Signal"] == "BUY"
    ].copy()

    sell_signals = executable_trades.loc[
        executable_trades["Signal"] == "SELL"
    ].copy()

if not buy_signals.empty:
    st.success(
        f"AI approved {len(buy_signals)} executable BUY "
        f"opportunity/opportunities."
    )
    st.dataframe(buy_signals, width="stretch")

if not sell_signals.empty:
    st.warning(
        f"AI approved {len(sell_signals)} executable SELL "
        f"opportunity/opportunities."
    )
    st.dataframe(sell_signals, width="stretch")

if buy_signals.empty and sell_signals.empty:
    raw_buy_count = 0
    raw_sell_count = 0

    if "Signal" in market_df.columns:
        raw_buy_count = int(market_df["Signal"].eq("BUY").sum())
        raw_sell_count = int(market_df["Signal"].eq("SELL").sum())

    if raw_buy_count > 0 or raw_sell_count > 0:
        st.info(
            "The AI model detected raw BUY or SELL signals, but none passed "
            "all execution filters. The system recommends waiting."
        )
    else:
        st.info(
            "No strong BUY or SELL signal is currently available. "
            "The AI recommends HOLD."
        )

st.subheader("🚫 Blocked Non-Stock Trades")

if len(blocked_trades) > 0:
    st.warning(
        "These trades passed the AI pipeline but are blocked from execution because their broker engine is not connected yet."
    )
    st.dataframe(blocked_trades, width="stretch")
else:
    st.info("No blocked non-stock trades right now.")

# =========================
# RESET BUTTON (TEST ONLY)
# =========================
if st.button("Reset Portfolio (TEST ONLY)"):

    # STOP auto trading FIRST
    st.session_state.AUTO_TRADING = False

    # FULL RESET
    st.session_state.cash = 100000
    st.session_state.positions = {}
    st.session_state.trade_log = []
    st.session_state.equity_history = []
    st.session_state.last_trade_time = {}
    st.session_state.trade_messages = []

    st.session_state.orders = []
    st.session_state.portfolio_history = []

    st.session_state.performance = {
        "profit": 0,
        "trades": 0,
        "wins": 0
    }

    # 🔥 CRITICAL: reset engine
    if "paper_engine" in st.session_state:
        del st.session_state.paper_engine

    # Reset the shared account too -- see the comment on the other
    # reset button above for why.
    account_store.reset_account(starting_cash=100000)

    st.success("Portfolio FULLY reset (test)")

    # Restart clean UI
    st.rerun()

# =========================
# EXECUTE BUTTON
# =========================
if st.button("Execute Trades"):
    # Blocks the top-of-script autorefresh (see AUTO REFRESH section)
    # from re-arming while this potentially multi-second execution pass
    # is running -- a rerun triggered mid-execution can otherwise
    # interrupt Streamlit's script thread before a trade finishes being
    # logged. Reset right before st.rerun() below covers every path
    # through this block, since nothing here returns/continues early.
    st.session_state.trade_execution_in_progress = True

    st.session_state.trade_messages = []
    if LIVE_TRADING:
        manual_buy_signals = buy_signals.copy()
        manual_sell_signals = sell_signals.copy()

        # ----------------------------------------------------
        # Manual BUY readiness
        # ----------------------------------------------------
        if not manual_buy_signals.empty:
            buy_readiness = get_ai_trading_readiness(
                execution_mode="MANUAL",
                action="BUY",
            )

            if not buy_readiness["ready"]:
                manual_buy_signals = pd.DataFrame()

                st.session_state.trade_messages.append(
                    "Manual BUY execution blocked: "
                    + buy_readiness["message"]
                )

        # ----------------------------------------------------
        # Manual SELL readiness
        # ----------------------------------------------------
        if not manual_sell_signals.empty:
            sell_readiness = get_ai_trading_readiness(
                execution_mode="MANUAL",
                action="SELL",
            )

            if not sell_readiness["ready"]:
                manual_sell_signals = pd.DataFrame()

                st.session_state.trade_messages.append(
                    "Manual SELL execution blocked: "
                    + sell_readiness["message"]
                )

        # ----------------------------------------------------
        # Submit only authorised signals
        # ----------------------------------------------------
        manual_buy_stocks = filter_by_asset_class(manual_buy_signals, "US_STOCKS")
        manual_sell_stocks = filter_by_asset_class(manual_sell_signals, "US_STOCKS")

        # FOREX/COMMODITIES (PAPER_ONLY_ASSET_CLASSES) are now handled in
        # one unconditional block below, alongside crypto -- both route
        # through their own real broker (eToro / Binance) independent of
        # this LIVE_TRADING toggle, which only ever concerns stocks via
        # Alpaca. See that block for eToro's manual-confirm-only handling.

        if (
            manual_buy_stocks.empty
            and manual_sell_stocks.empty
        ):
            if not st.session_state.trade_messages:
                st.session_state.trade_messages.append(
                    "No manually executable BUY or SELL "
                    "signals are currently available."
                )
        else:
            execute_alpaca_trades(
                manual_buy_stocks,
                manual_sell_stocks,
            )

    else:
        # Local paper-trading execution
        if (
            executable_trades is None
            or executable_trades.empty
            or "Signal" not in executable_trades.columns
        ):
            st.session_state.trade_messages.append(
                "No executable paper trades are currently available."
            )
        else:
            stock_buy_signals = filter_by_asset_class(buy_signals, "US_STOCKS")
            stock_sell_signals = filter_by_asset_class(sell_signals, "US_STOCKS")

            # FOREX/COMMODITIES no longer merge into this call -- see the
            # unconditional block below, which routes them through eToro
            # (or local paper as a fallback) independent of this
            # LIVE_TRADING toggle.
            execute_paper_trades(
                stock_buy_signals,
                stock_sell_signals
            )

    # Crypto always executes on Binance testnet, independent of the
    # LIVE_TRADING toggle above (which only concerns stocks).
    crypto_buy_signals = filter_by_asset_class(buy_signals, "CRYPTO")
    crypto_sell_signals = filter_by_asset_class(sell_signals, "CRYPTO")

    if not crypto_buy_signals.empty or not crypto_sell_signals.empty:
        execute_binance_trades(crypto_buy_signals, crypto_sell_signals)

    # FOREX/COMMODITIES (PAPER_ONLY_ASSET_CLASSES): route through eToro
    # Demo when enabled, independent of the LIVE_TRADING toggle above
    # (same reasoning as crypto/Binance just above -- that toggle only
    # ever concerns stocks via Alpaca). Manual-confirm only: this whole
    # button handler only ever runs from a human "Execute Trades" click,
    # so no separate AUTO_ETORO_TRADING_LOCKED check is needed here --
    # that gate lives in the Auto-Trading section further down instead.
    manual_paper_only_buy = collect_paper_only_signals(buy_signals)
    manual_paper_only_sell = collect_paper_only_signals(sell_signals)

    if not manual_paper_only_buy.empty or not manual_paper_only_sell.empty:
        if ETORO_LIVE_TRADING:
            execute_etoro_trades(manual_paper_only_buy, manual_paper_only_sell)
        else:
            st.session_state.trade_messages.append(
                f"{'/'.join(PAPER_ONLY_ASSET_CLASSES)} signals were approved "
                "but not submitted to a live broker: ETORO_LIVE_TRADING is "
                "False. Executing through local paper trading instead."
            )
            execute_paper_trades(manual_paper_only_buy, manual_paper_only_sell)

    st.session_state.last_execution_result = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "messages": list(st.session_state.trade_messages),
    }
    st.session_state.trade_execution_in_progress = False
    persist_account()
    st.rerun()

# Display the most recent manual execution result
if st.session_state.get("last_execution_result"):
    execution_result = st.session_state.last_execution_result

    st.markdown("### 🧾 Last Execution Attempt")

    st.caption(
        f"Processed at: {execution_result.get('time', 'Unknown')}"
    )

    execution_messages = execution_result.get("messages", [])

    if execution_messages:
        for message in execution_messages:
            message = str(message)
            message_lower = message.lower()

            if (
                "failed" in message_lower
                or "error" in message_lower
            ):
                st.error(message)

            elif (
                "blocked" in message_lower
                or "skipped" in message_lower
                or "closed" in message_lower
            ):
                st.warning(message)

            elif (
                "no manually executable" in message_lower
                or "no executable" in message_lower
                or "not available" in message_lower
                or "waiting" in message_lower
            ):
                st.info(message)

            else:
                st.success(message)

    else:
        st.info(
            "The execution attempt completed without producing "
            "an order or execution message."
        )

# =========================================
# 🤖 AUTO TRADING ENGINE (gated by AUTO_TRADING toggle)
# =========================================

st.divider()
st.subheader("🤖 Auto-Trading")

_auto_trading_before = st.session_state.AUTO_TRADING
st.session_state.AUTO_TRADING = st.checkbox(
    "Enable automatic execution (uses the same approved trades shown above)",
    value=st.session_state.AUTO_TRADING,
    help=(
        "When ON, approved BUY/SELL signals are executed automatically on "
        "every refresh, without needing the Execute Trades button. "
        "When OFF, nothing executes automatically — use the button above."
    ),
)
if st.session_state.AUTO_TRADING != _auto_trading_before:
    # Persist the toggle itself immediately, not just trade mutations --
    # otherwise flipping it on this device wouldn't show up on another
    # device until a trade happened to fire.
    persist_account()

if not st.session_state.AUTO_TRADING:
    st.caption("Auto-trading is OFF. Use the Execute Trades button above for manual control.")
else:
    st.info("🤖 AI Auto-Trading Engine Running...")

    # Same autorefresh guard as the manual Execute Trades button above --
    # see the comment there. Reset unconditionally right after this
    # block finishes, a few lines down.
    st.session_state.trade_execution_in_progress = True

    st.session_state.trade_messages = []

    if (
        executable_trades is None
        or executable_trades.empty
        or "Signal" not in executable_trades.columns
    ):
        st.warning("No executable trades available.")

    else:
        auto_buy = executable_trades.loc[
            executable_trades["Signal"] == "BUY"
        ].copy()

        auto_sell = executable_trades.loc[
            executable_trades["Signal"] == "SELL"
        ].copy()

        auto_buy_stocks = filter_by_asset_class(auto_buy, "US_STOCKS")
        auto_sell_stocks = filter_by_asset_class(auto_sell, "US_STOCKS")

        if LIVE_TRADING:
            # AUTO_LIVE_TRADING_LOCKED was meant to keep Alpaca execution
            # manual-only (require a human to click "Execute Trades")
            # while auto-trading still runs crypto/forex/commodities on
            # its own. It was defined in config.py and checked inside
            # validate_alpaca_execution_environment() -- but that function
            # was never actually called from anywhere, so the lock did
            # nothing and stock BUYs kept auto-firing through this exact
            # branch every autorefresh cycle regardless of the setting.
            # Enforcing it here for real.
            if AUTO_LIVE_TRADING_LOCKED:
                st.session_state.trade_messages.append(
                    "Auto-Trading: stock BUY/SELL signals skipped -- "
                    "Alpaca execution is locked to manual confirmation "
                    "(AUTO_LIVE_TRADING_LOCKED = True). Crypto below is "
                    "unaffected; FOREX/COMMODITIES below is locked the "
                    "same way, separately, via AUTO_ETORO_TRADING_LOCKED."
                )
            else:
                execute_alpaca_trades(auto_buy_stocks, auto_sell_stocks)
        else:
            execute_paper_trades(auto_buy_stocks, auto_sell_stocks)

        # Crypto always executes on Binance testnet, independent of
        # LIVE_TRADING (which only concerns stocks).
        auto_buy_crypto = filter_by_asset_class(auto_buy, "CRYPTO")
        auto_sell_crypto = filter_by_asset_class(auto_sell, "CRYPTO")

        if not auto_buy_crypto.empty or not auto_sell_crypto.empty:
            execute_binance_trades(auto_buy_crypto, auto_sell_crypto)

        # FOREX/COMMODITIES: eToro is manual-confirm only, same safety
        # pattern as Alpaca above (AUTO_LIVE_TRADING_LOCKED) but its own
        # separate flag -- auto-trading must never submit an eToro order
        # on its own, per explicit user request (2026-08-03). Use the
        # Execute Trades button for these instead.
        auto_paper_only_buy = collect_paper_only_signals(auto_buy)
        auto_paper_only_sell = collect_paper_only_signals(auto_sell)

        if not auto_paper_only_buy.empty or not auto_paper_only_sell.empty:
            if ETORO_LIVE_TRADING and AUTO_ETORO_TRADING_LOCKED:
                st.session_state.trade_messages.append(
                    f"Auto-Trading: {'/'.join(PAPER_ONLY_ASSET_CLASSES)} "
                    "signals skipped -- eToro execution is locked to manual "
                    "confirmation (AUTO_ETORO_TRADING_LOCKED = True). Use "
                    "the Execute Trades button above."
                )
            elif ETORO_LIVE_TRADING:
                execute_etoro_trades(auto_paper_only_buy, auto_paper_only_sell)
            else:
                execute_paper_trades(auto_paper_only_buy, auto_paper_only_sell)

    st.session_state.trade_execution_in_progress = False
    persist_account()


st.divider()

st.subheader("📊 Performance")

if LIVE_TRADING:
    performance_metrics = get_alpaca_performance_metrics()

    performance_profit_loss = float(
        performance_metrics.get("profit_loss", 0)
    )

    performance_return = float(
        performance_metrics.get("return_percent", 0)
    )

    performance_win_rate = float(
        performance_metrics.get("win_rate", 0)
    )

    performance_trades_closed = int(
        performance_metrics.get("trades_closed", 0)
    )

    performance_capital_invested = float(
        performance_metrics.get("capital_invested", 0)
    )

    performance_exposure = float(
        performance_metrics.get("portfolio_exposure", 0)
    )

else:
    performance_profit_loss = float(
        performance.get("PnL", 0)
    )

    performance_return = float(
        performance.get("Return %", 0)
    )

    performance_win_rate = float(
        performance.get("Win Rate", 0)
    )

    performance_trades_closed = int(
        performance.get("Wins", 0)
        + performance.get("Losses", 0)
    )

    performance_capital_invested = float(
        performance.get("Capital Invested", 0)
    )

    # `exposure` is already a 0-100 percentage from get_exposure_percent(),
    # same single source of truth used by the main dashboard's "Portfolio
    # Exposure" metric and by the actual risk gate -- no rescaling needed.
    performance_exposure = float(exposure)

p1, p2, p3, p4 = st.columns(4)

with p1:
    st.metric(
        "Profit / Loss",
        f"${performance_profit_loss:,.2f}"
    )

with p2:
    st.metric(
        "Return %",
        f"{performance_return:.2f}%"
    )

with p3:
    st.metric(
        "Win Rate",
        f"{performance_win_rate:.1f}%"
    )

with p4:
    st.metric(
        "Trades Closed",
        performance_trades_closed
    )

c1, c2 = st.columns(2)

with c1:
    st.metric(
        "Capital Invested",
        f"${performance_capital_invested:,.2f}"
    )

if not LIVE_TRADING:
    st.caption(
        "Stats below are computed from the trade journal using FIFO "
        "BUY-to-SELL matching -- they only count trades that have actually "
        "closed, not open positions."
    )

    d1, d2, d3 = st.columns(3)

    with d1:
        pf = performance.get("Profit Factor")
        st.metric(
            "Profit Factor",
            "N/A" if pf is None else f"{pf:.2f}"
        )

    with d2:
        st.metric(
            "Expectancy / Trade",
            f"${performance.get('Expectancy', 0):,.2f}"
        )

    with d3:
        st.metric(
            "Max Drawdown",
            f"${performance.get('Max Drawdown', 0):,.2f}"
        )

with c2:
    st.metric(
        "Portfolio Exposure",
        f"{performance_exposure:.1f}%"
    )

if LIVE_TRADING and performance_metrics.get("error"):
    st.warning(
        "Alpaca performance data could not be fully loaded: "
        f"{performance_metrics['error']}"
    )

if LIVE_TRADING:
    matched_trades = performance_metrics.get(
        "matched_trades",
        []
    )

    with st.expander(
        "🔍 Alpaca Matched Trade Verification"
    ):
        if matched_trades:
            matched_trades_df = pd.DataFrame(
                matched_trades
            )

            st.dataframe(
                matched_trades_df,
                width="stretch"
            )
        else:
            st.info(
                "No complete Alpaca BUY-to-SELL "
                "trade lifecycle has been matched yet."
            )

st.divider()

st.subheader("📅 Performance Digest")
st.caption(
    "Built from the same trade-journal FIFO matching as Performance "
    "above, broken down by asset class -- for a quick daily check "
    "during the validation run without reading the full trade log."
)

digest_period_choice = st.selectbox(
    "Period",
    ["Today", "Last 7 Days", "Last 30 Days", "All Time"],
    key="digest_period_choice",
)

_DIGEST_PERIOD_DAYS = {
    "Today": 1,
    "Last 7 Days": 7,
    "Last 30 Days": 30,
    "All Time": None,
}

digest = calculate_performance_digest(
    period_days=_DIGEST_PERIOD_DAYS[digest_period_choice]
)

digest_overall = digest["overall"]

g1, g2, g3, g4 = st.columns(4)

with g1:
    st.metric("Trades Closed", digest_overall["trades_closed"])

with g2:
    st.metric("Win Rate", f"{digest_overall['win_rate']:.1f}%")

with g3:
    st.metric("Total P&L", f"${digest_overall['total_pnl']:,.2f}")

with g4:
    pf = digest_overall["profit_factor"]
    st.metric("Profit Factor", "N/A" if pf is None else f"{pf:.2f}")

digest_rows = []
all_asset_classes = sorted(
    set(digest["by_asset_class"]) | set(digest["open_positions_by_asset_class"])
)

for asset_class in all_asset_classes:
    stats = digest["by_asset_class"].get(asset_class, {
        "trades_closed": 0, "wins": 0, "losses": 0,
        "win_rate": 0.0, "total_pnl": 0.0,
        "profit_factor": None, "expectancy": 0.0,
    })
    pf = stats["profit_factor"]

    digest_rows.append({
        "Asset Class": asset_class,
        "Trades Closed": stats["trades_closed"],
        "Wins": stats["wins"],
        "Losses": stats["losses"],
        "Win Rate %": round(stats["win_rate"], 1),
        "Total P&L ($)": round(stats["total_pnl"], 2),
        "Profit Factor": "N/A" if pf is None else round(pf, 2),
        "Expectancy/Trade ($)": round(stats["expectancy"], 2),
        "Open Positions Now": digest["open_positions_by_asset_class"].get(asset_class, 0),
    })

if digest_rows:
    st.dataframe(pd.DataFrame(digest_rows), width="stretch")
else:
    st.info(
        f"No closed trades in the selected period ({digest_period_choice})."
    )

if telegram_notifier.is_configured():
    if st.button("📨 Send This Digest to Telegram"):
        telegram_notifier.notify_digest(
            digest_period_choice,
            digest_overall,
            digest["by_asset_class"],
        )
        st.success("Digest sent to Telegram.")
else:
    st.caption(
        "Telegram alerts aren't configured yet -- add TELEGRAM_BOT_TOKEN "
        "and TELEGRAM_CHAT_ID to .env to enable trade-fill alerts and "
        "the button above."
    )

st.divider()

st.subheader("📉 Equity Curve")

if LIVE_TRADING:
    portfolio_history = get_portfolio_history(
        period="1M",
        timeframe="1D",
    )

    history_error = portfolio_history.get("error")

    timestamps = portfolio_history.get("timestamp", [])
    equity_values = portfolio_history.get("equity", [])

    if (
        history_error is None
        and timestamps
        and equity_values
        and len(timestamps) == len(equity_values)
    ):
        broker_equity_df = pd.DataFrame(
            {
                "Time": pd.to_datetime(
                    timestamps,
                    unit="s",
                    errors="coerce",
                ),
                "Portfolio Value": pd.to_numeric(
                    equity_values,
                    errors="coerce",
                ),
            }
        )

        broker_equity_df = broker_equity_df.dropna(
            subset=["Time", "Portfolio Value"]
        )

        if not broker_equity_df.empty:
            broker_equity_df = (
                broker_equity_df
                .sort_values("Time")
                .drop_duplicates(
                    subset=["Time"],
                    keep="last",
                )
            )

            st.line_chart(
                broker_equity_df.set_index("Time")
            )
        else:
            st.info(
                "Alpaca portfolio history currently contains "
                "no usable equity records."
            )

    elif history_error:
        st.warning(
            "Alpaca portfolio history could not be loaded: "
            f"{history_error}"
        )

    else:
        st.info(
            "Alpaca has not returned enough portfolio-history "
            "data to draw the equity curve yet."
        )

else:
    equity_df = pd.DataFrame(
        st.session_state.equity_history
    )

    if (
        not equity_df.empty
        and "Time" in equity_df.columns
        and len(equity_df) > 1
    ):
        import altair as alt

        values = equity_df["Portfolio Value"]
        value_min = float(values.min())
        value_max = float(values.max())

        if value_max == value_min:
            # No change yet between points -- give it a small fixed band
            # so it draws as a visible flat line, not a random-looking
            # crop of empty space.
            padding = max(abs(value_max) * 0.01, 1.0)
        else:
            padding = (value_max - value_min) * 0.1

        # NOTE: st.line_chart's default y-axis previously scaled from
        # near zero up to the full portfolio value (~$100k-180k),
        # regardless of the actual range being plotted. That made real
        # day-to-day swings of a few hundred/thousand dollars visually
        # disappear against that huge span -- especially early on, with
        # only a couple of points. This zooms to the data's own range
        # instead, so real movement is actually visible.
        equity_chart = (
            alt.Chart(equity_df)
            .mark_line(point=True)
            .encode(
                x=alt.X("Time:N", title="Time"),
                y=alt.Y(
                    "Portfolio Value:Q",
                    title="Portfolio Value ($)",
                    scale=alt.Scale(domain=[value_min - padding, value_max + padding]),
                ),
                tooltip=["Time", "Portfolio Value"],
            )
            .properties(height=320)
        )

        st.altair_chart(equity_chart, use_container_width=True)
        st.caption(
            "Resets when the app restarts (session-only history) -- "
            "will keep filling in the longer the app stays running."
        )
    else:
        st.info(
            "Equity curve will appear after more updates."
        )

st.divider()
st.subheader("🔮 Portfolio Preview After AI Trades")

if preview_allocation:
    preview_df = pd.DataFrame(preview_allocation)
    st.dataframe(preview_df, width="stretch")
else:
    st.info("No portfolio preview available yet.")
    
st.subheader("📈 Current Holdings")

st.subheader("🧺 Asset Allocation")

if asset_allocation:
    total_allocation_value = sum(asset_allocation.values())

    allocation_df = pd.DataFrame(
        [
            {
                "Asset Class": asset_class,
                "Value": round(value, 2),
                "Allocation %": round((value / total_allocation_value) * 100, 2)
                if total_allocation_value > 0 else 0
            }
            for asset_class, value in asset_allocation.items()
        ]
    )

    st.dataframe(allocation_df, width="stretch")

else:
    st.info("No active asset allocation yet.")

st.divider()

allocation_data = [{"Asset": "Cash", "Value": round(st.session_state.cash, 2)}]

for ticker, position in st.session_state.positions.items():
    latest_price = market_df.loc[market_df["Ticker"] == ticker, "Price ($)"].values

    if len(latest_price) > 0:
        allocation_data.append({
            "Asset": ticker,
            "Value": round(position["shares"] * latest_price[0], 2)
        })

try:
    import binance_broker
    for position in binance_broker.get_positions():
        ticker = position["symbol"]
        qty = float(position["qty"])
        latest_price = market_df.loc[market_df["Ticker"] == ticker, "Price ($)"].values

        if len(latest_price) > 0:
            allocation_data.append({
                "Asset": ticker,
                "Value": round(qty * latest_price[0], 2)
            })
except Exception:
    pass

allocation_df = pd.DataFrame(allocation_data)

if len(allocation_df) > 0:
    st.bar_chart(allocation_df.set_index("Asset"))

st.divider()

st.subheader("📌 Current Positions")

if LIVE_TRADING:
    try:
        positions = broker.get_positions()

        if positions:
            positions_data = []

            for p in positions:
                positions_data.append({
                    "Ticker": p.symbol,
                    "Shares": float(p.qty),
                    "Entry Price": round(float(p.avg_entry_price), 2),
                    "Current Price": round(float(p.current_price), 2),
                    "Market Value": round(float(p.market_value), 2),
                    "PnL": round(float(p.unrealized_pl), 2),
                    "Return %": round(float(p.unrealized_plpc) * 100, 2)
                })

            positions_df = pd.DataFrame(positions_data)
            st.dataframe(positions_df, width="stretch")

        else:
            st.info("No open positions.")

    except Exception as e:
        st.error(f"Error loading positions: {e}")

else:
    # Local Paper Trading mode: show local session state, NOT the live
    # Alpaca account. These are two separate, unrelated accounts -- mixing
    # them here is what previously made positions look like they'd "never
    # clear" when in local paper mode.
    if st.session_state.positions:
        positions_data = []

        for ticker, position in st.session_state.positions.items():
            shares = float(position["shares"])
            entry_price = float(position["entry_price"])

            latest_price_lookup = market_df.loc[
                market_df["Ticker"] == ticker, "Price ($)"
            ].values
            current_price = (
                float(latest_price_lookup[0])
                if len(latest_price_lookup) > 0
                else entry_price
            )

            market_value = shares * current_price
            pnl = market_value - (shares * entry_price)
            return_pct = (
                (pnl / (shares * entry_price)) * 100
                if shares * entry_price > 0
                else 0
            )

            positions_data.append({
                "Ticker": ticker,
                "Shares": round(shares, 4),
                "Entry Price": round(entry_price, 2),
                "Current Price": round(current_price, 2),
                "Market Value": round(market_value, 2),
                "PnL": round(pnl, 2),
                "Return %": round(return_pct, 2),
            })

        positions_df = pd.DataFrame(positions_data)
        st.dataframe(positions_df, width="stretch")

    else:
        st.info("No open positions.")

st.subheader("🪙 Crypto Positions (Binance Testnet)")

try:
    import binance_broker
    crypto_positions = binance_broker.get_positions()

    if crypto_positions:
        crypto_positions_data = []

        for position in crypto_positions:
            crypto_positions_data.append({
                "Ticker": position["symbol"],
                "Quantity": round(float(position["qty"]), 6),
            })

        crypto_positions_df = pd.DataFrame(crypto_positions_data)
        st.dataframe(crypto_positions_df, width="stretch")
    else:
        st.info("No open crypto positions.")

except Exception as e:
    st.warning(f"Could not load Binance testnet positions: {e}")

st.subheader("📋 Order Book")
st.caption(
    "Every order ever created across all brokers, persisted and queryable "
    "even after restarting the app -- unlike the tables below, which only "
    "show what happened in the current session."
)

try:
    import engines.order_manager as order_manager
    all_orders = order_manager.load_orders(limit=100)

    if all_orders:
        order_book_df = pd.DataFrame(all_orders)[[
            "updated_at", "ticker", "side", "broker", "status",
            "quantity", "price", "filled_price", "trade_amount", "error",
        ]]
        order_book_df.columns = [
            "Updated", "Ticker", "Side", "Broker", "Status",
            "Quantity", "Price", "Filled Price", "Amount", "Error",
        ]
        st.dataframe(order_book_df, width="stretch")
    else:
        st.info("No orders recorded yet.")

except Exception as e:
    st.warning(f"Could not load Order Book: {e}")

st.subheader("🧾 AI Order Manager")

if len(st.session_state.trade_log) > 0:
    ai_order_log_df = pd.DataFrame(st.session_state.trade_log)
    st.dataframe(ai_order_log_df, width="stretch")
else:
    st.info("No AI-managed orders yet.")
    
st.subheader("📜 Trade Log")

if LIVE_TRADING:
    try:
        alpaca_orders = get_orders()
        orders_data = []

        for o in alpaca_orders:
            orders_data.append({
                "Ticker": o.symbol,
                "Side": o.side,
                "Type": o.type,
                "Qty": o.qty,
                "Filled Qty": o.filled_qty,
                "Avg Fill Price": o.filled_avg_price,
                "Status": o.status,
                "Submitted": o.submitted_at
            })

        if orders_data:
            orders_df = pd.DataFrame(orders_data)
            st.dataframe(orders_df, width="stretch")
        else:
            st.info("No Alpaca orders yet.")

    except Exception as e:
        st.error(f"Could not load Alpaca orders: {e}")

else:
    if st.session_state.trade_log:
        trade_log_df = pd.DataFrame(st.session_state.trade_log)
        st.dataframe(trade_log_df, width="stretch")
    else:
        st.info("No paper trades yet.")

st.divider()

st.subheader("⚙️ System Status")

st.success("Market data connected")
st.success("Trained AI model connected")
st.success("BUY / HOLD / SELL signal engine active")

if broker_health["connected"]:

    st.success("Alpaca Paper Broker connected")

    if broker_health["trading_blocked"]:
        st.error("Broker account is currently blocked from trading")
    else:
        st.success("Broker account is approved for trading")

    if broker_health["market_open"]:
        st.success("US stock market is currently OPEN")
    else:
        st.info(
            f"US stock market is currently CLOSED. "
            f"Next open: {broker_health['next_market_open']}"
        )

else:
    st.error(
        f"Alpaca broker connection failed: "
        f"{broker_health['error']}"
    )

try:
    import binance_broker
    binance_health = binance_broker.check_broker_connection()

    if binance_health["connected"]:
        st.success(
            f"Binance testnet connected "
            f"(USDT balance: ${binance_health['cash']:,.2f})"
        )
    else:
        st.warning(
            f"Binance testnet not connected: {binance_health['error']} "
            f"-- crypto trades will be skipped until BINANCE_TESTNET_API_KEY "
            f"and BINANCE_TESTNET_SECRET_KEY are set in .env"
        )
except Exception as e:
    st.warning(f"Binance testnet status unavailable: {e}")

st.markdown("### 🛡️ Broker State Monitor")

broker_state_status = broker_state_health.get(
    "status",
    "CRITICAL"
)

if broker_state_status == "HEALTHY":
    st.success("Broker state is healthy and synchronised.")

elif broker_state_status == "WARNING":
    st.warning("Broker state requires attention.")

else:
    st.error("Broker state health check failed.")

status_col1, status_col2 = st.columns(2)

with status_col1:
    st.metric(
        "Broker Open Positions",
        broker_state_health.get("open_positions", 0)
    )

with status_col2:
    st.metric(
        "Active Broker Orders",
        broker_state_health.get("active_orders", 0)
    )

duplicate_symbols = broker_state_health.get(
    "duplicate_order_symbols",
    []
)

if duplicate_symbols:
    st.error(
        "Duplicate active orders detected for: "
        + ", ".join(duplicate_symbols)
    )

position_order_conflicts = broker_state_health.get(
    "position_order_conflicts",
    []
)

if position_order_conflicts:
    st.warning(
        "Positions with active broker orders: "
        + ", ".join(position_order_conflicts)
    )

for issue in broker_state_health.get("issues", []):
    st.warning(issue)
if LIVE_TRADING:
    st.info(
        "Execution environment: Alpaca Paper Trading — no real money is being used."
    )
else:
    # NOTE: this used to say "no broker orders are being submitted" for
    # both asset classes. That stopped being accurate once crypto's
    # execution pipeline started actually placing real orders on the
    # Binance testnet (see engines/risk_engine.py / binance_broker.py) --
    # only stocks are still a purely local, simulated paper-trade loop.
    st.info(
        "Execution environment: Stocks = Local Paper Trading (simulated, "
        "no broker orders submitted). Crypto = Binance Testnet (real "
        "testnet orders are submitted, no real money is at risk)."
    )
    
    # =========================================
# 🧠 ADD THIS BLOCK RIGHT HERE (DO NOT MOVE)
# =========================================
st.subheader("🧠 Approval Engine Debug")

# =========================================
# 🔥 SAFE DISPLAY FOR DEBUG TABLE
# =========================================
display_cols = [
    "Ticker",
    "Signal",
    "Risk Reward",
    "Trade Decision"
]

available_cols = [col for col in display_cols if col in market_df.columns]

if available_cols:
    st.dataframe(market_df[available_cols], width="stretch")
else:
    st.warning("No debug columns available yet.")