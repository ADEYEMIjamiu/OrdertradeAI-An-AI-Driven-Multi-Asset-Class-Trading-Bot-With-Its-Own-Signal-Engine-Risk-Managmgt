import streamlit as st
import pandas as pd
import yfinance as yf
import ta
import joblib
import os
import time
import json

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
from engines.signal_engine import prepare_data, get_ai_signal, get_multi_timeframe_signal
from engines.order_manager import (
    create_order,
    mark_order_filled,
    mark_order_submitted,
    mark_order_rejected,
    mark_order_failed,
    save_order,
    load_orders,
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
from engines.readiness_engine import calculate_readiness_scorecard
from engines import equity_tracker
from engines.asset_toggle_engine import (
    is_asset_class_enabled,
    set_asset_class_enabled,
    get_all_toggles,
    ASSET_CLASSES,
)
from engines.position_lifecycle_engine import (
    get_position_state,
    update_position_state,
    clear_position_state,
    should_activate_breakeven,
    effective_stop_loss_percent,
    should_take_partial_profit,
    partial_profit_quantity,
    should_time_exit,
)
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
    calculate_bot_attributable_portfolio_value,
    get_open_positions_value,
    get_exposure_percent,
    calculate_trade_amount,
    get_account_balance,
    can_open_position,
    risk_check_before_trade,
    get_dynamic_buy_confidence,
    get_live_forex_commodity_position_count,
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
    reconcile_alpaca_orders,
    reconcile_etoro_orders,
    reconcile_binance_orders,
)
import engines.rotation_history as rotation_history
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
    PARTIAL_PROFIT_TAKE_FRACTION,
    MAX_HOLD_DAYS,
    MAX_HOLD_DAYS_HARD,
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

# Second, independent gate on eToro auto-trading -- see the
# "2026-08-24 (Moderate Finding #5...)" comment near where this is
# checked (search _ETORO_AUTO_TRADING_HARD_BLOCK) for the full
# reasoning. Deliberately defined here in app.py, not config.py --
# turning eToro auto-trading on now requires editing this file
# directly (a deliberate, visible code change), not just flipping a
# config flag, matching the strength the surrounding comments already
# claimed but the code didn't actually enforce.
_ETORO_AUTO_TRADING_HARD_BLOCK = True

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

    # Catch up any Alpaca order the local journal still shows as
    # PENDING/SUBMITTED against Alpaca's real status -- e.g. a market
    # order queued while the market was closed, which finally filled
    # since the last time this page loaded. Cheap and safe: skipped
    # entirely if nothing is pending, and any broker outage here just
    # leaves the journal as-is rather than breaking the page.
    try:
        reconcile_alpaca_orders()
    except Exception:
        pass

if ETORO_LIVE_TRADING:
    # Same reconciliation idea as Alpaca above, for eToro's own
    # unconfirmed-BUY case -- see reconcile_etoro_orders() docstring.
    try:
        reconcile_etoro_orders()
    except Exception:
        pass

# Added 2026-08-24 (Critical Finding #3 from the full codebase audit):
# Binance had no equivalent of the two reconciliations above -- a BUY
# that failed client-side but actually filled on Binance's own side
# would sit invisible to risk management forever, with nothing
# surfacing it. Not gated behind a LIVE_TRADING-style flag since crypto
# always runs through Binance testnet regardless. This only DETECTS the
# mismatch (see reconcile_binance_orders()'s docstring for why it
# deliberately doesn't auto-create a synthetic order) -- surface it
# loudly here so a human can decide how to backfill it, same as the
# 2026-08-23 orphaned-positions cleanup.
try:
    _binance_reconcile_result = reconcile_binance_orders()
    for _suspicious in _binance_reconcile_result.get("suspicious", []):
        _alert_key = f"binance_reconcile_{_suspicious['ticker']}"
        if _alert_key not in st.session_state.setdefault("uncovered_crypto_alerted", set()):
            st.session_state.uncovered_crypto_alerted.add(_alert_key)
            _msg = (
                f"POSSIBLE UNRECORDED FILL for {_suspicious['ticker']}: wallet holds "
                f"{_suspicious['wallet_qty']} units, but the journal has no FILLED BUY "
                f"and there's a FAILED buy order around {_suspicious['failed_order_time']}. "
                f"This coin likely filled on Binance despite the client-side error and has "
                f"zero risk-management coverage -- investigate and consider a manual backfill."
            )
            st.session_state.trade_messages.append(_msg)
            print(f"[reconcile_binance_orders] {_msg}")
except Exception:
    pass

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

# 2026-08-24: EXECUTION_KILL_SWITCH (config.py) is documented as "Master
# emergency switch... True = block all new AI trade entries immediately,"
# but was previously only ever checked inside get_ai_trading_readiness()
# (engines/broker_sync_engine.py), which is itself only called from the
# manual Alpaca-stocks Execute Trades path. It had ZERO effect on Alpaca
# auto-trading, crypto (manual or auto), or eToro (manual or auto) -- an
# operator setting it to True believing it would halt all AI trading, per
# its own docstring, would have every crypto and eToro trade keep firing
# unaffected. Found during a full codebase audit, 2026-08-24.
#
# Fix: make it drive the SAME emergency_stop.flag file the dashboard's
# "EMERGENCY STOP" button already uses -- that mechanism is proven correct,
# already gates all four execution paths (execute_paper_trades,
# execute_alpaca_trades, execute_binance_trades, execute_etoro_trades), and
# deliberately does NOT touch apply_risk_management()/
# apply_crypto_risk_management()/apply_etoro_trailing_lock() -- so
# protective exits (stop-loss/take-profit/trailing) correctly keep running
# even while this is active, exactly matching EXECUTION_KILL_SWITCH's own
# documented "block new entries" (not exits) intent.
#
# The reason string lets this tell apart a stop IT activated from one a
# person activated manually via the dashboard button, so toggling the
# config flag back to False can't silently undo a manual stop, and clicking
# "Resume" on the dashboard can't silently leave the kill switch
# unenforced while it's still set True in config.
_KILL_SWITCH_REASON = "EXECUTION_KILL_SWITCH=True in config.py"


def sync_kill_switch_with_emergency_stop():
    currently_stopped = emergency_stop.is_stopped()
    current_reason = emergency_stop.get_reason() if currently_stopped else ""

    if EXECUTION_KILL_SWITCH and not currently_stopped:
        emergency_stop.activate(_KILL_SWITCH_REASON)
    elif not EXECUTION_KILL_SWITCH and currently_stopped and current_reason == _KILL_SWITCH_REASON:
        emergency_stop.deactivate()


sync_kill_switch_with_emergency_stop()

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
    
    
# prepare_data() / get_ai_signal() / get_multi_timeframe_signal() moved to
# engines/signal_engine.py on 2026-08-26 (imported near the top of this
# file, see "from engines.signal_engine import ..."), so the SaaS
# per-user execution loop can reuse the exact same signal logic without
# importing this Streamlit script directly. Verified as a byte-for-byte
# logic extraction (diffed against the original) before this file was
# changed -- see that module's docstring for the full reasoning.


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

    # Fetched once per pass (not per ticker) -- feeds calculate_trade_amount()
    # so trade sizing scales with this account's real balance instead of a
    # fixed dollar band. See risk_engine.get_account_balance()/
    # calculate_trade_amount() docstrings (2026-08-25).
    account_balance = get_account_balance("US_STOCKS")

    # =========================================================
    # BUY EXECUTION
    # =========================================================
    # NOTE: toggled-off only skips the BUY loop below (via the empty
    # buy_signals swap) -- SELL EXECUTION further down in this same
    # function must always still run, so this must never be a `return`.
    if not is_asset_class_enabled("US_STOCKS") and not buy_signals.empty:
        st.session_state.trade_messages.append(
            "BUY skipped for all US_STOCKS signals: asset class is "
            "toggled off in Asset Class Controls."
        )
        buy_signals = buy_signals.iloc[0:0]

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
            # Stocks trade unleveraged -- leverage=1. entry_price/stop_loss
            # come from trade_planner's per-ticker ATR stop (row["Stop Loss"],
            # already populated by the time this runs, see market_df
            # concat with trade_plans_df earlier in the script) so wider
            # or tighter stops size the trade accordingly.
            trade_amount = calculate_trade_amount(
                row["AI Confidence %"],
                market_df,
                entry_price=row.get("Price ($)"),
                stop_loss=row.get("Stop Loss"),
                leverage=1,
                account_balance=account_balance,
            )

            # trade_amount == 0 means the account can't cover even
            # MIN_TRADE_AMOUNT -- skip rather than submit a sub-floor order.
            if trade_amount <= 0:
                st.session_state.trade_messages.append(
                    f"BUY skipped for {ticker}: insufficient account "
                    f"balance to open a minimum-size position."
                )
                continue

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

            alpaca_response = buy_stock(ticker, trade_amount)

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

            broker_order_id = str(getattr(alpaca_response, "id", "") or "") or None
            response_status = str(getattr(alpaca_response, "status", "") or "").lower()

            order = mark_order_submitted(order, broker_order_id=broker_order_id)

            # Alpaca's response right after submit_order() usually still
            # reads "accepted"/"pending_new" even when the real fill
            # happens a moment later, so only trust an explicit "filled"
            # here. Anything else stays SUBMITTED and is caught up by
            # reconcile_alpaca_orders() on the next page load, instead of
            # being optimistically marked FILLED before Alpaca actually
            # confirms it (see 2026-08-08 rotation incident, where a
            # market-closed order was logged FILLED here while Alpaca
            # itself still showed it pending).
            if "filled" in response_status:
                response_filled_qty = getattr(alpaca_response, "filled_qty", None)
                response_filled_price = getattr(alpaca_response, "filled_avg_price", None)
                order = mark_order_filled(
                    order,
                    filled_price=float(response_filled_price) if response_filled_price else current_price,
                    filled_quantity=float(response_filled_qty) if response_filled_qty else estimated_shares,
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

                    alpaca_response = sell_stock(ticker, qty)

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

                    broker_order_id = str(getattr(alpaca_response, "id", "") or "") or None
                    response_status = str(getattr(alpaca_response, "status", "") or "").lower()

                    sell_order = mark_order_submitted(sell_order, broker_order_id=broker_order_id)

                    # See the matching comment in the BUY loop above --
                    # only trust an explicit "filled" status from the
                    # submit response itself; otherwise leave it SUBMITTED
                    # for reconcile_alpaca_orders() to catch up later.
                    if "filled" in response_status:
                        response_filled_qty = getattr(alpaca_response, "filled_qty", None)
                        response_filled_price = getattr(alpaca_response, "filled_avg_price", None)
                        sell_order = mark_order_filled(
                            sell_order,
                            filled_price=float(response_filled_price) if response_filled_price else current_price,
                            filled_quantity=float(response_filled_qty) if response_filled_qty else qty,
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

    # Fetched once per pass -- see execute_alpaca_trades()'s matching
    # comment for why (2026-08-25).
    account_balance = get_account_balance("CRYPTO")

    # NOTE: toggled-off only skips the BUY loop below -- SELL EXECUTION
    # further down in this same function must always still run, so this
    # must never be a `return`.
    if not is_asset_class_enabled("CRYPTO") and not buy_signals.empty:
        st.session_state.trade_messages.append(
            "BUY skipped for all CRYPTO signals: asset class is toggled "
            "off in Asset Class Controls."
        )
        buy_signals = buy_signals.iloc[0:0]

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
            # Crypto trades unleveraged on Binance -- leverage=1. Same
            # stop-aware sizing as stocks above.
            trade_amount = calculate_trade_amount(
                row["AI Confidence %"],
                market_df,
                entry_price=row.get("Price ($)"),
                stop_loss=row.get("Stop Loss"),
                leverage=1,
                account_balance=account_balance,
            )

            if trade_amount <= 0:
                st.session_state.trade_messages.append(
                    f"BUY skipped for {ticker}: insufficient account "
                    f"balance to open a minimum-size position."
                )
                continue

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
    AUTO_ETORO_TRADING_LOCKED and _ETORO_AUTO_TRADING_HARD_BLOCK in the
    Auto-Trading section further down (two independent gates as of
    2026-08-24), which deliberately never call this, per explicit user
    request (2026-08-03) that eToro trades require a human click, unlike
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

    # Fetched once per pass -- see execute_alpaca_trades()'s matching
    # comment for why (2026-08-25). Covers both FOREX and COMMODITIES
    # (both trade against the same eToro account cash).
    account_balance = get_account_balance("FOREX")

    # =========================================================
    # BUY EXECUTION
    # =========================================================
    for _, row in buy_signals.iterrows():
        ticker = str(row["Ticker"]).upper().strip()
        asset_class = row.get("Asset Class", "FOREX")

        if not is_asset_class_enabled(asset_class):
            st.session_state.trade_messages.append(
                f"BUY skipped for {ticker}: {asset_class} is toggled off "
                f"in Asset Class Controls."
            )
            continue

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
            # eToro forex/commodities trade on ETORO_LEVERAGE (10x, see
            # etoro_broker.buy()) -- the same price-distance stop carries
            # 10x the dollar risk it would unleveraged, so leverage is
            # passed through here to shrink sizing accordingly.
            trade_amount = calculate_trade_amount(
                row["AI Confidence %"],
                market_df,
                entry_price=row.get("Price ($)"),
                stop_loss=row.get("Stop Loss"),
                leverage=etoro_broker.ETORO_LEVERAGE,
                account_balance=account_balance,
            )

            if trade_amount <= 0:
                st.session_state.trade_messages.append(
                    f"BUY skipped for {ticker}: insufficient account "
                    f"balance to open a minimum-size position."
                )
                continue

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
            # 2026-08-08: this used to call mark_order_filled()
            # unconditionally right here, before ever checking whether
            # eToro actually confirmed a position -- so the "no position
            # confirmed yet" case handled a few lines below still got
            # logged as FILLED in the Order Book. Same class of bug just
            # found and fixed for Alpaca's execute_alpaca_trades(): only
            # mark FILLED when the broker has actually confirmed it.
            if result.get("position_id") is not None:
                oms_order["broker_order_id"] = result["position_id"]
                oms_order = mark_order_filled(
                    oms_order,
                    filled_price=fill_price,
                    filled_quantity=trade_amount / fill_price if fill_price else 0,
                )
            else:
                oms_order = mark_order_submitted(oms_order, broker_order_id=None)
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


# =========================================================
# POSITION-CAP ROTATION (manual-approval-first)
# =========================================================
# Built 2026-08-06 at explicit user request, after repeatedly watching a
# real, currently-strong BUY signal get skipped with "position limit
# reached" while a much weaker position from earlier sat occupying that
# asset class's slot. User-confirmed design (via AskUserQuestion):
#   - Manual approval first -- this only ever SUGGESTS a swap on the
#     dashboard with a Confirm button. Nothing here closes or opens a
#     position on its own.
#   - 20-point minimum Strategy Score gap between the candidate and the
#     weakest held position before a swap is even suggested.
#   - 24-hour cooldown -- a position isn't eligible to be rotated out
#     until it's been held at least this long, so it has a real chance to
#     work before being judged.
ROTATION_MIN_SCORE_GAP = 20
ROTATION_COOLDOWN_HOURS = 24


def _get_position_opened_at(broker_name, ticker):
    """
    Best-effort lookup of when a currently-held position was opened, used
    only to enforce ROTATION_COOLDOWN_HOURS -- never relied on for
    anything trade-critical. None of the three brokers hand back a clean
    "position opened at" timestamp directly here (Alpaca's Position object
    doesn't carry one the way this code reads it, and the Binance/eToro
    positions used elsewhere in this file are plain dicts built fresh from
    their own APIs with no open-time field). This instead looks at the
    persistent order book (trade_journal.db, via engines.order_manager --
    the same store every BUY/SELL in this file already writes to) for the
    most recent FILLED BUY order matching this broker/ticker.

    Returns None if nothing is found, and callers treat "unknown" as "do
    not offer rotation for this position" rather than assuming it's safe
    to rotate out something whose open time can't actually be confirmed.
    """
    try:
        orders = load_orders(limit=500)
    except Exception:
        return None

    matches = [
        o for o in orders
        if o.get("broker") == broker_name
        and str(o.get("ticker", "")).upper().strip() == ticker.upper().strip()
        and o.get("side") == "BUY"
        and o.get("status") == "FILLED"
    ]
    if not matches:
        return None

    matches.sort(
        key=lambda o: o.get("filled_at") or o.get("updated_at") or o.get("created_at") or "",
        reverse=True,
    )
    timestamp_text = (
        matches[0].get("filled_at")
        or matches[0].get("updated_at")
        or matches[0].get("created_at")
    )
    if not timestamp_text:
        return None

    try:
        return datetime.fromisoformat(timestamp_text)
    except Exception:
        return None


def _get_held_positions_for_rotation(asset_class, etoro_positions_by_symbol):
    """
    Returns currently-held positions for one asset class in a common
    shape rotation logic can compare across all four asset classes:
        {"ticker": project ticker, "broker": broker name, "identifier":
         whatever the matching close_* call needs -- qty for
         Alpaca/Binance, position_id for eToro}.
    `etoro_positions_by_symbol` is passed in rather than fetched here so
    FOREX and COMMODITIES share a single eToro network call per rotation
    check instead of doing two.
    """
    held = []

    if asset_class == "US_STOCKS":
        try:
            for position in get_open_positions():
                held.append({
                    "ticker": str(position.symbol).upper().strip(),
                    "broker": "alpaca",
                    "identifier": float(position.qty),
                })
        except Exception:
            pass

    elif asset_class == "CRYPTO":
        import binance_broker
        try:
            for position in binance_broker.get_positions():
                held.append({
                    "ticker": str(position["symbol"]).upper().strip(),
                    "broker": "binance",
                    "identifier": float(position["qty"]),
                })
        except Exception:
            pass

    elif asset_class in ("FOREX", "COMMODITIES"):
        for project_ticker in _ETORO_ASSET_CLASS_TICKERS.get(asset_class, []):
            etoro_symbol = etoro_broker.resolve_project_ticker(project_ticker)
            position = etoro_positions_by_symbol.get(etoro_symbol)
            if position is not None:
                held.append({
                    "ticker": project_ticker,
                    "broker": "etoro",
                    "identifier": position["position_id"],
                })

    return held


def find_rotation_candidates(market_df, buy_signals):
    """
    For each asset class, compare the CURRENT Strategy Score of the
    weakest currently-held position against the CURRENT Strategy Score of
    the strongest not-yet-held approved BUY candidate in the same asset
    class. Both scores are read fresh from THIS pass's market_df -- not
    whatever a position happened to score when it was originally bought
    -- since a position that scored well a week ago can easily be
    outscored by conditions today. Returns a list of suggestion dicts;
    see the module comment above this function for the full design.
    """
    candidates = []

    if buy_signals is None or buy_signals.empty:
        return candidates
    if "Strategy Score" not in market_df.columns or "Strategy Score" not in buy_signals.columns:
        return candidates

    try:
        etoro_positions_by_symbol = {p["symbol"]: p for p in etoro_broker.get_positions()}
    except Exception:
        etoro_positions_by_symbol = {}

    for asset_class in ["US_STOCKS", "CRYPTO", "FOREX", "COMMODITIES"]:
        held = _get_held_positions_for_rotation(asset_class, etoro_positions_by_symbol)
        if not held:
            continue

        class_buy_signals = filter_by_asset_class(buy_signals, asset_class)
        if class_buy_signals is None or class_buy_signals.empty:
            continue

        held_tickers = {h["ticker"] for h in held}
        open_candidates = class_buy_signals[
            ~class_buy_signals["Ticker"].astype(str).str.upper().str.strip().isin(held_tickers)
        ]
        if open_candidates.empty:
            continue

        best_candidate_row = open_candidates.loc[open_candidates["Strategy Score"].idxmax()]

        scored_held = []
        for position in held:
            match = market_df.loc[
                market_df["Ticker"].astype(str).str.upper().str.strip() == position["ticker"]
            ]
            if match.empty:
                continue
            scored_held.append({**position, "score": float(match.iloc[0]["Strategy Score"])})

        if not scored_held:
            continue

        weakest = min(scored_held, key=lambda p: p["score"])
        candidate_score = float(best_candidate_row["Strategy Score"])
        gap = candidate_score - weakest["score"]

        if gap < ROTATION_MIN_SCORE_GAP:
            continue

        opened_at = _get_position_opened_at(weakest["broker"], weakest["ticker"])
        if opened_at is None:
            # Can't confirm how long it's been held -- don't guess.
            continue

        hours_held = (datetime.now() - opened_at).total_seconds() / 3600
        if hours_held < ROTATION_COOLDOWN_HOURS:
            continue

        candidates.append({
            "asset_class": asset_class,
            "weak_ticker": weakest["ticker"],
            "weak_broker": weakest["broker"],
            "weak_score": weakest["score"],
            "hours_held": hours_held,
            "candidate_ticker": str(best_candidate_row["Ticker"]).upper().strip(),
            "candidate_score": candidate_score,
            "candidate_row": best_candidate_row,
            "gap": gap,
        })

    return candidates


def _rotation_position_still_open(asset_class, ticker):
    """
    Re-check directly with the broker whether a position is still open,
    used by execute_rotation() between its close and open legs.

    2026-08-08: execute_rotation() fired both legs unconditionally --
    close the weak position, then open the replacement -- with nothing
    checking that the close actually worked in between. For Alpaca this
    happened to be caught accidentally (a queued-but-unfilled sell order
    trips the broker health WARNING gate, which then blocks the buy), but
    that protection is Alpaca-specific: get_broker_state_health() only
    looks at Alpaca positions/orders. eToro has no equivalent gate, so if
    an eToro close ever failed or didn't confirm (market closed, timeout,
    anything), the buy leg would still have fired right after it --
    opening a new leveraged position without ever having closed the old
    one, and quietly breaching the asset class's position cap in the
    process. This checks reality directly with the broker instead of
    trusting that "no exception was raised" means "the position is
    actually gone" -- covering all three brokers the same way, and
    failing safe (treats the position as still open, so it blocks the
    buy) if the broker can't even be reached to check.
    """
    ticker = str(ticker).upper().strip()

    try:
        if asset_class == "US_STOCKS":
            for position in get_open_positions():
                if str(position.symbol).upper().strip() == ticker:
                    return True
            return False

        elif asset_class == "CRYPTO":
            import binance_broker
            for position in binance_broker.get_positions():
                if str(position["symbol"]).upper().strip() == ticker:
                    return True
            return False

        else:
            return etoro_broker.find_position_by_symbol(ticker) is not None

    except Exception:
        # Broker unreachable -- can't confirm the close actually
        # happened, so fail safe and assume it's still open rather than
        # risk opening a second position on top of an unconfirmed close.
        return True


def execute_rotation(candidate, market_df):
    """
    Closes the weak position and opens the suggested replacement, by
    reusing the exact same execute_alpaca_trades/execute_binance_trades/
    execute_etoro_trades functions every other trade in this app already
    goes through -- same risk checks, same order-journal logging, same
    Telegram notifications, same error handling. Rotation only decides
    WHICH two trades to submit; it never reimplements HOW to submit them.
    Called only from the "Confirm Rotation" button below -- per the
    user's explicit "manual approval first" choice, nothing upstream of
    that click can trigger this.
    """
    asset_class = candidate["asset_class"]

    # 2026-08-08: A live rotation fired on a Saturday. The SELL leg was
    # accepted by Alpaca but queued (US market closed, can't fill until
    # next open) rather than executing immediately. Because that queued
    # order and the still-open position existed at the same time, the
    # broker health check (see get_broker_state_health -- it treats an
    # open position with an active order on it as a conflict) flipped to
    # WARNING, which silently blocked the BUY leg from ever being
    # attempted -- leaving the swap half-done with no clear explanation.
    # Rotation assumes the SELL clears before the BUY fires in the same
    # pass, so for US_STOCKS it only makes sense while the market is
    # actually open. Gate it here instead of letting it fail confusingly
    # downstream.
    if asset_class == "US_STOCKS":
        try:
            stock_broker_health = check_broker_connection()
            market_is_open = bool(stock_broker_health.get("market_open", False))
        except Exception:
            stock_broker_health = {}
            market_is_open = False

        if not market_is_open:
            next_open = stock_broker_health.get("next_market_open", "the next session")
            st.session_state.trade_messages.append(
                f"🔄 Rotation not attempted for {candidate['weak_ticker']} -> "
                f"{candidate['candidate_ticker']}: the US stock market is "
                f"closed. Selling now would only queue the order, and the "
                f"buy leg would then be blocked by the broker's own "
                f"position/order safety check -- so nothing was submitted "
                f"to avoid a half-completed swap. Next market open: "
                f"{next_open}. Try again once the market reopens."
            )
            return

    weak_row = market_df.loc[
        market_df["Ticker"].astype(str).str.upper().str.strip() == candidate["weak_ticker"]
    ].copy()
    if weak_row.empty:
        st.session_state.trade_messages.append(
            f"Rotation failed: could not find current market data for "
            f"{candidate['weak_ticker']} to close it."
        )
        return
    weak_row["Signal"] = "SELL"

    candidate_row_df = pd.DataFrame([candidate["candidate_row"]])
    empty_df = pd.DataFrame()

    # Close leg only, for now -- the open leg is gated below on actually
    # confirming this worked, not just on it not having raised.
    if asset_class == "US_STOCKS":
        execute_alpaca_trades(empty_df, weak_row)
    elif asset_class == "CRYPTO":
        execute_binance_trades(empty_df, weak_row)
    else:
        execute_etoro_trades(empty_df, weak_row)

    if _rotation_position_still_open(asset_class, candidate["weak_ticker"]):
        st.session_state.trade_messages.append(
            f"🔄 Rotation stopped after attempting to close "
            f"{candidate['weak_ticker']}: it still shows as an open "
            f"position with the broker, so {candidate['candidate_ticker']} "
            f"was NOT opened -- avoiding a double position on top of an "
            f"unconfirmed close. Check the broker/Order Book for why the "
            f"close didn't complete; if it's just delayed, try the "
            f"rotation again once it clears."
        )
        return

    if asset_class == "US_STOCKS":
        execute_alpaca_trades(candidate_row_df, empty_df)
    elif asset_class == "CRYPTO":
        execute_binance_trades(candidate_row_df, empty_df)
    else:
        execute_etoro_trades(candidate_row_df, empty_df)

    # 2026-08-11: This final message used to fire unconditionally right
    # after the buy leg was attempted, regardless of whether it actually
    # succeeded. A live rotation showed exactly that gap -- AAPL closed
    # cleanly, but the WMT buy was skipped by the daily trade limit
    # (a real, separate safety gate, working as intended), and yet this
    # message still declared "Rotation executed: closed AAPL... to open
    # WMT..." -- claiming success on a swap that was only half done. Same
    # principle as the close-leg check above: confirm reality with the
    # broker before declaring success, instead of trusting that "no
    # exception was raised" means the buy went through.
    if _rotation_position_still_open(asset_class, candidate["candidate_ticker"]):
        st.session_state.trade_messages.append(
            f"🔄 Rotation executed: closed {candidate['weak_ticker']} "
            f"(score {candidate['weak_score']:.1f}, held {candidate['hours_held']:.1f}h) "
            f"to open {candidate['candidate_ticker']} (score {candidate['candidate_score']:.1f})."
        )
    else:
        st.session_state.trade_messages.append(
            f"⚠️ Rotation partially completed: {candidate['weak_ticker']} "
            f"was closed, but {candidate['candidate_ticker']} was NOT "
            f"opened -- see the buy message above for why (e.g. daily "
            f"trade limit, insufficient cash, broker rejection). You're "
            f"now holding cash instead of {candidate['candidate_ticker']}; "
            f"try the rotation again once the blocking condition clears, "
            f"or place that buy manually."
        )


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



# 2026-08-06: st.session_state.highest_profit (the peak profit % reached
# per stock position, used below to trail an exit down from that peak
# rather than a fixed take-profit level) used to live ONLY in
# st.session_state. That's fine within one running process, but this
# app's systemd service restarts on every deploy (and can restart for
# other reasons too) -- session_state is wiped clean on restart, so a
# position that had climbed to +5% and was being actively trailed would
# silently forget that peak the moment the service bounced, and either
# re-arm from whatever the price happens to be at that instant (losing
# the lock it had already earned) or, worse, never trigger the trailing
# exit it should have. Persisting to a small JSON file survives restarts,
# the same fix already applied to eToro's position snapshot
# (etoro_broker._POSITION_STATE_FILE) for the identical underlying
# reason.
_HIGHEST_PROFIT_STATE_FILE = "alpaca_highest_profit_state.json"


def _load_highest_profit_state():
    try:
        with open(_HIGHEST_PROFIT_STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_highest_profit_state(state):
    try:
        with open(_HIGHEST_PROFIT_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[apply_risk_management] could not persist highest_profit state: {e}")


def _journal_alpaca_risk_exit(ticker, qty, current_price, exit_reason):
    """
    Submit a SELL to Alpaca for an automatic risk-management exit
    (stop-loss, take-profit, or trailing-stop) and persist it to the
    order journal -- the same create_order/mark_order_submitted/
    mark_order_filled/save_order sequence execute_alpaca_trades() already
    uses for every signal-driven trade.

    2026-08-10: discovered apply_risk_management()'s three exit branches
    called sell_stock() directly with no order-journal write at all, so
    every automatic stock exit that has EVER fired -- not just the one
    that triggered this fix -- is missing from trade_journal.db's orders
    table. This closes that gap going forward; it does not (and can't,
    since the real fill price was never captured) backfill exits that
    already happened before this fix deployed.

    FIX 2026-08-24: the note above claimed this made stock exits visible
    to "the table the Performance Digest and Readiness Scorecard
    calculate win rate/profit factor from" -- that's wrong. This writes
    to order_manager's `orders` table (create_order/save_order), which
    is a DIFFERENT table from trade_journal.py's `trades` table that
    engines/performance_engine.py (and everything built on it -- the
    Performance Digest, Readiness Scorecard, Sharpe/Sortino, monthly
    returns, and the new per-strategy breakdown) actually reads via
    load_trade_journal(). Crypto's equivalent
    (apply_crypto_risk_management) calls BOTH create_order/save_order
    AND log_trade() for every exit; this function only ever did the
    former. So every stock position that closed via stop-loss,
    take-profit, break-even, partial-profit, trailing-lock, or the
    time-limit exit -- as opposed to a normal AI SELL signal -- has been
    silently invisible to every one of those metrics this whole time,
    not just before 2026-08-10. Added the missing log_trade() call below,
    matching crypto's pattern exactly (reason=exit_reason, confidence/
    trend_score=0 since risk exits aren't signal-driven, mode=
    "ALPACA_LIVE" since this function only ever runs inside
    apply_risk_management()'s `if LIVE_TRADING:` block).
    """
    alpaca_response = sell_stock(ticker, qty)

    order = create_order(
        ticker=ticker,
        side="SELL",
        quantity=qty,
        trade_amount=qty * current_price,
        price=current_price,
        asset_class="US_STOCKS",
        broker="alpaca",
        strategy=exit_reason,
        confidence=0,
        ai_trade_score=0,
        priority="N/A",
    )

    broker_order_id = str(getattr(alpaca_response, "id", "") or "") or None
    response_status = str(getattr(alpaca_response, "status", "") or "").lower()

    order = mark_order_submitted(order, broker_order_id=broker_order_id)

    if "filled" in response_status:
        response_filled_qty = getattr(alpaca_response, "filled_qty", None)
        response_filled_price = getattr(alpaca_response, "filled_avg_price", None)
        order = mark_order_filled(
            order,
            filled_price=float(response_filled_price) if response_filled_price else current_price,
            filled_quantity=float(response_filled_qty) if response_filled_qty else qty,
        )

    save_order(order)

    log_trade(
        ticker=ticker,
        action="SELL",
        price=current_price,
        shares=qty,
        amount=qty * current_price,
        confidence=0,
        trend_score=0,
        reason=exit_reason,
        mode="ALPACA_LIVE",
    )

    return alpaca_response


def apply_risk_management(market_df):
    if LIVE_TRADING:
        try:
            alpaca_positions = get_open_positions()

            if "highest_profit" not in st.session_state:
                # Restore from disk instead of starting empty, so a
                # position that was already being trailed before a
                # restart keeps its earned peak instead of losing it.
                st.session_state.highest_profit = _load_highest_profit_state()

            # Drop any ticker no longer actually held (closed manually on
            # Alpaca's side, or closed by this same function while the
            # service happened to be down) -- otherwise a stale peak could
            # sit in the file forever and, worse, wrongly seed the
            # trailing calculation if that ticker is ever bought again.
            # Deliberately NOT normalized (upper/strip) -- must match the
            # exact, unmodified `position.symbol` key used as `ticker`
            # throughout the rest of this function below.
            held_tickers = {position.symbol for position in alpaca_positions}
            for stale_ticker in list(st.session_state.highest_profit.keys()):
                if stale_ticker not in held_tickers:
                    del st.session_state.highest_profit[stale_ticker]

            # STOP_LOSS_PERCENT/TAKE_PROFIT_PERCENT come from config.py
            # as fractions (0.03 = "3%", per the inline comment there),
            # but change_percent below is a real percentage number
            # (e.g. -3.5 meaning -3.5%) -- comparing them directly was
            # off by 100x, so a "3%" stop-loss was actually triggering
            # at a 0.03 PERCENTAGE-POINT move (a rounding error's worth
            # of price noise), not an actual 3% move. This is almost
            # certainly the real reason positions were being closed
            # within seconds of opening rather than the intended band
            # being merely "tight". TRAILING_PROFIT_START/DROP below
            # are already defined directly in percentage-points (1.5,
            # 0.75) so they don't need this conversion.
            # 2026-08-06: none of these three exits used to notify
            # Telegram at all -- only crypto's equivalent risk-
            # management block (apply_crypto_risk_management() below)
            # already did. A user would only find out a stock's stop
            # loss / take profit / trailing lock fired by opening the
            # dashboard themselves. Added notify_trade_fill() to all
            # three, matching exactly the call already used for
            # crypto exits, including realized_pnl so the alert shows
            # profit/loss, not just that a sale happened.
            for position in alpaca_positions:
                # Each position's exit attempt gets its own try/except so
                # a problem on one ticker (e.g. a journal-write hiccup)
                # can't silently skip stop-loss/take-profit protection for
                # every OTHER held position in the same pass -- this used
                # to be one big try/except around the whole loop.
                try:
                    ticker = position.symbol
                    entry_price = float(position.avg_entry_price)
                    current_price = float(position.current_price)
                    qty = float(position.qty)

                    change_percent = ((current_price / entry_price) - 1) * 100

                    previous_high = st.session_state.highest_profit.get(ticker, change_percent)
                    st.session_state.highest_profit[ticker] = max(previous_high, change_percent)

                    highest_profit = st.session_state.highest_profit[ticker]
                    trailing_exit_level = highest_profit - TRAILING_PROFIT_DROP

                    # --- Position lifecycle management (break-even stop,
                    # partial profit-taking, time-based exit) -- see
                    # engines/position_lifecycle_engine.py's module
                    # docstring for why this exists. ---
                    lifecycle_key = f"US_STOCKS:{ticker}"
                    lifecycle_state = get_position_state(lifecycle_key)

                    if should_activate_breakeven(change_percent, lifecycle_state["breakeven_active"]):
                        update_position_state(lifecycle_key, breakeven_active=True)
                        lifecycle_state["breakeven_active"] = True
                        st.session_state.trade_messages.append(
                            f"{ticker}: profit reached {round(change_percent, 2)}% -- "
                            f"stop-loss moved to break-even."
                        )

                    if should_take_partial_profit(change_percent, lifecycle_state["partial_taken"]):
                        partial_qty = round(partial_profit_quantity(qty), 6)
                        if partial_qty > 0:
                            _journal_alpaca_risk_exit(ticker, partial_qty, current_price, "PARTIAL_PROFIT")
                            update_position_state(lifecycle_key, partial_taken=True)
                            realized = partial_qty * (current_price - entry_price)
                            st.session_state.trade_messages.append(
                                f"PARTIAL PROFIT taken for {ticker}: sold {round(partial_qty, 4)} "
                                f"shares ({int(PARTIAL_PROFIT_TAKE_FRACTION * 100)}%) at "
                                f"${round(current_price, 2)} (+{round(change_percent, 2)}%), "
                                f"banking ${round(realized, 2)}. Remainder still open."
                            )
                            telegram_notifier.notify_trade_fill(
                                ticker=ticker,
                                action="SELL",
                                price=current_price,
                                shares=partial_qty,
                                amount=partial_qty * current_price,
                                asset_class="US_STOCKS",
                                mode="ALPACA_LIVE" if LIVE_TRADING else "LOCAL_PAPER",
                                realized_pnl=realized,
                            )
                        # Alpaca's position object won't reflect the reduced
                        # size until the next refresh -- skip the exit
                        # checks below for this pass rather than evaluate
                        # them against a qty that's now stale.
                        continue

                    effective_stop_pct = effective_stop_loss_percent(
                        -(STOP_LOSS_PERCENT * 100), lifecycle_state["breakeven_active"]
                    )
                    stop_exit_reason = (
                        "BREAKEVEN_STOP"
                        if lifecycle_state["breakeven_active"]
                        and change_percent > -(STOP_LOSS_PERCENT * 100)
                        else "STOP_LOSS"
                    )

                    if change_percent <= effective_stop_pct:
                        _journal_alpaca_risk_exit(ticker, qty, current_price, stop_exit_reason)
                        st.session_state.trade_messages.append(
                            f"{stop_exit_reason.replace('_', ' ')} triggered for {ticker}. "
                            f"Sold {round(qty, 4)} shares at ${round(current_price, 2)}"
                        )
                        telegram_notifier.notify_trade_fill(
                            ticker=ticker,
                            action="SELL",
                            price=current_price,
                            shares=qty,
                            amount=qty * current_price,
                            asset_class="US_STOCKS",
                            mode="ALPACA_LIVE" if LIVE_TRADING else "LOCAL_PAPER",
                            realized_pnl=qty * (current_price - entry_price),
                        )
                        del st.session_state.highest_profit[ticker]
                        clear_position_state(lifecycle_key)

                    elif change_percent >= (TAKE_PROFIT_PERCENT * 100):
                        _journal_alpaca_risk_exit(ticker, qty, current_price, "TAKE_PROFIT")
                        st.session_state.trade_messages.append(
                            f"TAKE PROFIT triggered for {ticker}. Sold {round(qty, 4)} shares at ${round(current_price, 2)}"
                        )
                        telegram_notifier.notify_trade_fill(
                            ticker=ticker,
                            action="SELL",
                            price=current_price,
                            shares=qty,
                            amount=qty * current_price,
                            asset_class="US_STOCKS",
                            mode="ALPACA_LIVE" if LIVE_TRADING else "LOCAL_PAPER",
                            realized_pnl=qty * (current_price - entry_price),
                        )
                        del st.session_state.highest_profit[ticker]
                        clear_position_state(lifecycle_key)

                    elif highest_profit >= TRAILING_PROFIT_START and change_percent <= trailing_exit_level:
                        _journal_alpaca_risk_exit(ticker, qty, current_price, "TRAILING_STOP")
                        st.session_state.trade_messages.append(
                            f"TRAILING PROFIT LOCK triggered for {ticker}. Highest profit was {round(highest_profit, 2)}%, sold at {round(change_percent, 2)}%"
                        )
                        telegram_notifier.notify_trade_fill(
                            ticker=ticker,
                            action="SELL",
                            price=current_price,
                            shares=qty,
                            amount=qty * current_price,
                            asset_class="US_STOCKS",
                            mode="ALPACA_LIVE" if LIVE_TRADING else "LOCAL_PAPER",
                            realized_pnl=qty * (current_price - entry_price),
                        )
                        del st.session_state.highest_profit[ticker]
                        clear_position_state(lifecycle_key)

                    elif should_time_exit(lifecycle_state["opened_at"], change_percent):
                        _journal_alpaca_risk_exit(ticker, qty, current_price, "TIME_LIMIT_EXIT")
                        # Distinguish which tier fired for a clear message --
                        # see position_lifecycle_engine.should_time_exit().
                        exit_tier = (
                            f"hard {MAX_HOLD_DAYS_HARD}-day limit (regardless of P&L)"
                            if change_percent < 0
                            else f"{MAX_HOLD_DAYS}-day limit, flat-or-better"
                        )
                        st.session_state.trade_messages.append(
                            f"TIME LIMIT EXIT triggered for {ticker}: hit the "
                            f"{exit_tier}. Sold {round(qty, 4)} shares at "
                            f"${round(current_price, 2)} ({round(change_percent, 2)}%)."
                        )
                        telegram_notifier.notify_trade_fill(
                            ticker=ticker,
                            action="SELL",
                            price=current_price,
                            shares=qty,
                            amount=qty * current_price,
                            asset_class="US_STOCKS",
                            mode="ALPACA_LIVE" if LIVE_TRADING else "LOCAL_PAPER",
                            realized_pnl=qty * (current_price - entry_price),
                        )
                        del st.session_state.highest_profit[ticker]
                        clear_position_state(lifecycle_key)

                except Exception as e:
                    st.session_state.trade_messages.append(
                        f"Risk management failed for {getattr(position, 'symbol', '?')}: {e}"
                    )

            # One write per call covers every update and deletion above --
            # cheap (small JSON, local disk) and keeps the on-disk state
            # from ever drifting more than one script pass behind memory.
            _save_highest_profit_state(st.session_state.highest_profit)

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


# 2026-08-24: crypto's own peak-tracking trailing-profit-lock, mirroring
# stocks' st.session_state.highest_profit above. Crypto already had
# break-even-stop + partial-profit-taking (see the lifecycle comment
# inside apply_crypto_risk_management() below), which stops a position
# from going net-negative and banks a slice of profit at a fixed +2.5%
# trigger -- but neither one continues to protect the REMAINDER of a
# position once it keeps running past that trigger. A position that ran
# to +20% and reversed could give back most of that before hitting the
# fixed take-profit band or the 5/7-day time exit. This closes that gap
# on the remaining size, without replacing the fixed take-profit ceiling
# or the time exit -- both stay in place as hard backstops, so this can
# only ever improve the outcome, never hold capital longer than those
# caps already allow.
#
# Reuses the same TRAILING_PROFIT_START/TRAILING_PROFIT_DROP values
# already defined for stocks (no crypto-specific data yet to justify
# separate, wider thresholds for crypto's extra volatility -- revisit if
# this turns out to trigger prematurely on ordinary crypto noise).
#
# Persisted to disk for the same reason as stocks' state file: this
# service restarts on every deploy, and session_state alone would forget
# an already-earned peak on restart.
_CRYPTO_HIGHEST_PROFIT_STATE_FILE = "crypto_highest_profit_state.json"


def _load_crypto_highest_profit_state():
    try:
        with open(_CRYPTO_HIGHEST_PROFIT_STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_crypto_highest_profit_state(state):
    try:
        with open(_CRYPTO_HIGHEST_PROFIT_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[apply_crypto_risk_management] could not persist highest_profit state: {e}")


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

    FIX 2026-08-23: entry-price lookup now queries order_manager directly
    per-ticker (get_most_recent_filled_buy) instead of filtering a capped
    200-order recent-orders list -- see that function's docstring for why.
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

    if "crypto_highest_profit" not in st.session_state:
        # Restore from disk instead of starting empty, so a position
        # already being trailed before a restart keeps its earned peak.
        st.session_state.crypto_highest_profit = _load_crypto_highest_profit_state()

    # Drop any ticker no longer actually held -- otherwise a stale peak
    # could sit in the file forever and wrongly seed the trailing
    # calculation if that coin is ever bought again later.
    held_tickers = {str(p["symbol"]).upper().strip() for p in positions}
    for stale_ticker in list(st.session_state.crypto_highest_profit.keys()):
        if stale_ticker not in held_tickers:
            del st.session_state.crypto_highest_profit[stale_ticker]

    for position in positions:
        ticker = str(position["symbol"]).upper().strip()
        qty = float(position["qty"])

        # FIX 2026-08-23: was filtering order_manager.load_orders(limit=200)
        # -- the 200 most recent orders ACROSS ALL TICKERS -- in Python,
        # which silently lost entry-price coverage for any position whose
        # original BUY had aged out of that shared window (confirmed live:
        # 13 of 21 held positions had no discoverable entry this way). This
        # queries the database directly for THIS ticker, so unrelated order
        # volume on other tickers can never starve it out.
        try:
            entry_order = order_manager.get_most_recent_filled_buy(ticker, "binance")
        except Exception as e:
            st.session_state.trade_messages.append(
                f"Crypto risk management skipped for {ticker}: could not "
                f"load order history: {e}"
            )
            continue

        if entry_order is None:
            # FIX 2026-08-24: this used to be a bare `continue` -- a
            # position landing here has ZERO stop-loss/take-profit/
            # lifecycle protection, silently, forever, with nothing on
            # the dashboard or in the logs to say so. This is exactly
            # how 12 positions went unprotected until
            # adopt_entry_for_orphaned_positions.py caught it manually
            # on 2026-08-23 -- that fixed the positions that existed
            # THEN, but did nothing to alert on a new one forming today.
            # Found during a full codebase audit, 2026-08-24. Throttled
            # to once per ticker per process lifetime (not every 5-min
            # cycle) via a session_state set, so this can't flood
            # trade_messages/Telegram on every autorefresh while still
            # guaranteeing it's surfaced at least once.
            msg = (
                f"NO RISK MANAGEMENT for {ticker}: no FILLED BUY order "
                f"found in the journal, so stop-loss/take-profit/"
                f"lifecycle protection cannot be applied. Investigate "
                f"and consider adopt_entry_for_orphaned_positions.py-"
                f"style backfill if this is a real held position."
            )
            # print() fires every cycle (5 min) so journalctl always shows
            # this is still an open, unresolved problem -- session_state
            # is wiped on a service restart, so relying on it alone could
            # go quiet again exactly when a restart happens. The
            # dashboard message is throttled to once per ticker per
            # process lifetime so it doesn't flood trade_messages/
            # Telegram every single autorefresh.
            print(f"[apply_crypto_risk_management] {msg}")
            if ticker not in st.session_state.setdefault("uncovered_crypto_alerted", set()):
                st.session_state.uncovered_crypto_alerted.add(ticker)
                st.session_state.trade_messages.append(msg)
            continue

        entry_price = float(entry_order["filled_price"])
        if entry_price <= 0:
            msg = (
                f"NO RISK MANAGEMENT for {ticker}: found a BUY order "
                f"but its filled_price is {entry_price} (invalid). "
                f"Investigate the order journal for this ticker."
            )
            print(f"[apply_crypto_risk_management] {msg}")
            if ticker not in st.session_state.setdefault("uncovered_crypto_alerted", set()):
                st.session_state.uncovered_crypto_alerted.add(ticker)
                st.session_state.trade_messages.append(msg)
            continue

        try:
            current_price = binance_broker.get_current_price(ticker)
        except Exception:
            continue

        change_percent = ((current_price / entry_price) - 1) * 100

        previous_high = st.session_state.crypto_highest_profit.get(ticker, change_percent)
        st.session_state.crypto_highest_profit[ticker] = max(previous_high, change_percent)
        highest_profit = st.session_state.crypto_highest_profit[ticker]
        trailing_exit_level = highest_profit - TRAILING_PROFIT_DROP

        # --- Position lifecycle management (break-even stop, partial
        # profit-taking, time-based exit) -- see
        # engines/position_lifecycle_engine.py's module docstring for
        # why this exists. Crypto previously had no trailing-lock or any
        # profit-protection at all beyond the binary stop-loss/
        # take-profit below, so a position could ride all the way from
        # near +5% back down to -3% and give back everything it earned.
        # 2026-08-24: the trailing-lock check below (see the elif chain
        # further down) now closes that gap on whatever's left after
        # break-even/partial-profit have already fired. ---
        lifecycle_key = f"CRYPTO:{ticker}"
        lifecycle_state = get_position_state(lifecycle_key)

        if should_activate_breakeven(change_percent, lifecycle_state["breakeven_active"]):
            update_position_state(lifecycle_key, breakeven_active=True)
            lifecycle_state["breakeven_active"] = True
            st.session_state.trade_messages.append(
                f"{ticker}: profit reached {round(change_percent, 2)}% -- "
                f"stop-loss moved to break-even."
            )

        if should_take_partial_profit(change_percent, lifecycle_state["partial_taken"]):
            partial_qty = round(partial_profit_quantity(qty), 6)
            if partial_qty > 0:
                try:
                    binance_broker.sell_crypto(ticker, partial_qty)
                except Exception as e:
                    st.session_state.trade_messages.append(
                        f"Crypto PARTIAL PROFIT failed for {ticker}: {e}"
                    )
                    continue

                partial_order = create_order(
                    ticker=ticker,
                    side="SELL",
                    quantity=partial_qty,
                    trade_amount=partial_qty * current_price,
                    price=current_price,
                    asset_class="CRYPTO",
                    broker="binance",
                    strategy="Partial Profit Taking",
                    confidence=0,
                    ai_trade_score=0,
                    priority="N/A",
                )
                partial_order = mark_order_filled(
                    partial_order, filled_price=current_price, filled_quantity=partial_qty
                )
                save_order(partial_order)

                partial_realized = partial_qty * (current_price - entry_price)

                log_trade(
                    ticker=ticker,
                    action="SELL",
                    price=current_price,
                    shares=partial_qty,
                    amount=partial_qty * current_price,
                    confidence=0,
                    trend_score=0,
                    reason="PARTIAL_PROFIT",
                    mode="BINANCE_TESTNET",
                )

                telegram_notifier.notify_trade_fill(
                    ticker=ticker,
                    action="SELL",
                    price=current_price,
                    shares=partial_qty,
                    amount=partial_qty * current_price,
                    asset_class="CRYPTO",
                    mode="BINANCE_TESTNET",
                    realized_pnl=partial_realized,
                )

                update_position_state(lifecycle_key, partial_taken=True)
                st.session_state.trade_messages.append(
                    f"PARTIAL PROFIT taken for {ticker}: sold {round(partial_qty, 6)} "
                    f"units ({int(PARTIAL_PROFIT_TAKE_FRACTION * 100)}%) at "
                    f"${round(current_price, 2)} (+{round(change_percent, 2)}%), "
                    f"banking ${round(partial_realized, 2)}. Remainder still open."
                )
            # Binance's wallet balance won't reflect the reduced size
            # until the next refresh -- skip the exit checks below for
            # this pass rather than evaluate them against a stale qty.
            continue

        effective_stop_pct = effective_stop_loss_percent(
            -(STOP_LOSS_PERCENT * 100), lifecycle_state["breakeven_active"]
        )

        if change_percent <= effective_stop_pct:
            exit_reason = (
                "BREAKEVEN STOP"
                if lifecycle_state["breakeven_active"]
                and change_percent > -(STOP_LOSS_PERCENT * 100)
                else "STOP LOSS"
            )
        elif change_percent >= (TAKE_PROFIT_PERCENT * 100):
            exit_reason = "TAKE PROFIT"
        elif highest_profit >= TRAILING_PROFIT_START and change_percent <= trailing_exit_level:
            exit_reason = (
                f"TRAILING PROFIT LOCK (peak {round(highest_profit, 2)}%)"
            )
        elif should_time_exit(lifecycle_state["opened_at"], change_percent):
            # Distinguish which tier fired -- see
            # position_lifecycle_engine.should_time_exit() for the two-tier
            # rule (soft: flat-or-better after MAX_HOLD_DAYS; hard: closes
            # regardless of P&L after MAX_HOLD_DAYS_HARD).
            exit_reason = (
                f"TIME LIMIT EXIT (hard {MAX_HOLD_DAYS_HARD}-day limit)"
                if change_percent < 0
                else f"TIME LIMIT EXIT ({MAX_HOLD_DAYS}-day limit, flat-or-better)"
            )
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
        clear_position_state(lifecycle_key)
        if ticker in st.session_state.crypto_highest_profit:
            del st.session_state.crypto_highest_profit[ticker]

    # One write per call covers every update/deletion above, mirroring
    # stocks' _save_highest_profit_state() call.
    _save_crypto_highest_profit_state(st.session_state.crypto_highest_profit)


_ETORO_HIGHEST_PRICE_STATE_FILE = "etoro_highest_price_state.json"


def _load_etoro_highest_price_state():
    try:
        with open(_ETORO_HIGHEST_PRICE_STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_etoro_highest_price_state(state):
    try:
        with open(_ETORO_HIGHEST_PRICE_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[apply_etoro_trailing_lock] could not persist highest_price state: {e}")


def apply_etoro_trailing_lock():
    """
    Bot-side replacement for eToro's own broker "trailing" stop, which
    was confirmed live 2026-08-24 to NOT actually work: the OIL position
    opened 2026-08-18 had isTslEnabled=True the entire time (both the
    live eToro API and this process's own logs confirmed
    set_trailing_stop() succeeded), yet its stopLossRate never moved
    even once during a sustained two-day rally to +4.17% above entry
    (2026-08-20 to 08-21) -- a working trailing stop should have
    ratcheted the stop from 81.67 up to roughly 85.08 by that peak. It
    stayed at 81.67, meaning the position was never actually protected
    beyond its original static stop despite eToro reporting trailing as
    "enabled." See etoro_broker.set_fixed_stop_loss()'s docstring for
    the full investigation.

    This function now does the ratcheting itself: for every open eToro
    LONG position, track the highest underlying price seen so far
    (persisted to disk, same reasoning as _HIGHEST_PROFIT_STATE_FILE --
    survives a service restart instead of forgetting an earned peak),
    and whenever that peak rises by at least ETORO_TRAILING_STEP_PCT
    since the last pushed update, PATCH an updated FIXED stop-loss to
    eToro at (peak * (1 - ETORO_STOP_LOSS_PCT)) -- the same
    distance-from-peak eToro's own "trailing" docs describe, just
    enforced by this bot instead of trusted to their black box. Never
    moves the stop down, and never touches take-profit.

    SHORT positions are deliberately skipped (not currently held by
    this project, and the ratchet direction inverts for shorts --
    would need its own dedicated logic, not a blind sign-flip).

    Runs on every script pass (autorefresh + manual button clicks),
    same rhythm as apply_risk_management()/apply_crypto_risk_management()
    above. Each position gets its own try/except so a problem on one
    ticker can't silently skip protection for every other open eToro
    position in the same pass.
    """
    try:
        positions = etoro_broker.get_positions()
    except Exception as e:
        st.session_state.trade_messages.append(f"eToro trailing-lock check failed to fetch positions: {e}")
        return

    highest_price_state = _load_etoro_highest_price_state()
    held_position_ids = {str(p["position_id"]) for p in positions}

    # Drop stale entries for positions no longer open -- otherwise a
    # closed position's old peak could sit in the file forever and,
    # worse, wrongly seed the ratchet if that same position_id number
    # were ever reused.
    for stale_id in list(highest_price_state.keys()):
        if stale_id not in held_position_ids:
            del highest_price_state[stale_id]

    for position in positions:
        if position["direction"] != "LONG":
            continue  # see docstring -- shorts not supported here yet

        position_id = position["position_id"]
        ticker = position["symbol"]
        if position_id is None or ticker is None:
            continue

        try:
            current_price = etoro_broker.get_current_price(ticker)
            open_price = float(position["open_price"])
            current_stop = position.get("stop_loss_rate")
            take_profit = position.get("take_profit_rate")

            state_key = str(position_id)
            previous_peak = highest_price_state.get(state_key, open_price)
            peak_price = max(previous_peak, current_price)
            highest_price_state[state_key] = peak_price

            if peak_price <= open_price:
                continue  # never below water yet -- nothing to ratchet

            candidate_stop = round(peak_price * (1 - etoro_broker.ETORO_STOP_LOSS_PCT), 5)

            # Only push an update if it's a real, meaningful improvement
            # over what's already set -- both a minimum step size (avoid
            # spamming PATCH on every cent of noise) and a hard guarantee
            # this never moves the stop down.
            if current_stop is not None:
                min_step = current_stop * etoro_broker.ETORO_TRAILING_STEP_PCT
                if candidate_stop < current_stop + min_step:
                    continue

            etoro_broker.set_fixed_stop_loss(position_id, candidate_stop, take_profit_rate=take_profit)
            st.session_state.trade_messages.append(
                f"{ticker}: trailing lock moved stop-loss to {candidate_stop} "
                f"(peak price {round(peak_price, 5)})."
            )
            print(
                f"[apply_etoro_trailing_lock] {ticker} (position {position_id}): "
                f"stop-loss pushed to {candidate_stop} (peak={peak_price})"
            )
        except Exception as e:
            st.session_state.trade_messages.append(
                f"eToro trailing-lock update failed for {ticker or position_id}: {e}"
            )

    _save_etoro_highest_price_state(highest_price_state)


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


st.caption(
    f"🕒 {datetime.now().strftime('%A, %B %d, %Y — %H:%M:%S')} (server time, UTC) "
    "— updates on each page refresh."
)

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

# ============================================================
# ASSET CLASS CONTROLS
# ============================================================
# Turns AI trade ENTRY on/off per asset class -- e.g. "only crypto today."
# Only gates new BUYs; SELL exits, stop-loss, take-profit, and trailing
# stops always fire regardless of these toggles (see
# engines/asset_toggle_engine.py's module docstring for why). State is
# persisted in asset_toggles.db, so it survives a service restart.
st.subheader("🎛️ Asset Class Controls")
st.caption(
    "Turn AI trade entry on/off per asset class. Existing positions are "
    "never affected -- SELLs, stop-losses, and take-profits always run "
    "regardless of these switches."
)
_current_toggles = get_all_toggles()
_toggle_cols = st.columns(len(ASSET_CLASSES))
for _col, _asset_class in zip(_toggle_cols, ASSET_CLASSES):
    with _col:
        _new_value = st.toggle(
            _asset_class.replace("_", " ").title(),
            value=_current_toggles[_asset_class],
            key=f"asset_toggle_{_asset_class}",
        )
        if _new_value != _current_toggles[_asset_class]:
            set_asset_class_enabled(_asset_class, _new_value)
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
apply_etoro_trailing_lock()

# eToro forex/commodities exits (stop-loss, take-profit, trailing stop)
# happen entirely on eToro's own servers -- unlike the two calls just
# above, this app never decides to close these itself, so there's no
# point in the code where it "already knows" a close just happened. This
# is the only way to notice: compare currently-open eToro positions
# against a saved snapshot of what was open last time this ran (see
# etoro_broker.check_for_closed_positions()'s docstring). Runs on every
# script pass -- manual button clicks AND the autorefresh cycle -- same
# rhythm as apply_risk_management()/apply_crypto_risk_management() above,
# so a closed position gets noticed on the next refresh, not just the
# next time someone clicks Execute Trades.
try:
    for closed_position in etoro_broker.check_for_closed_positions():
        telegram_notifier.notify_position_closed_automatically(
            ticker=closed_position["symbol"],
            position_id=closed_position["position_id"],
        )
        st.session_state.trade_messages.append(
            f"eToro position {closed_position['position_id']} "
            f"({closed_position['symbol']}) closed automatically -- "
            f"likely stop-loss, take-profit, or trailing stop. Check the "
            f"eToro dashboard for the exact close price/reason."
        )
except Exception as e:
    st.session_state.trade_messages.append(
        f"eToro closed-position check failed: {e}"
    )

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

# REMOVED 2026-08-24 (Moderate Finding #4 from the full codebase audit):
# apply_risk_management()/apply_crypto_risk_management()/
# apply_etoro_trailing_lock() used to be called a SECOND time right here,
# identically to the call at the top of this section. Everything between
# the two call sites (the eToro closed-position check, portfolio value/
# exposure/regime calcs, position sizing, trade queue, and the portfolio-
# limits/executable-trades filters) is read-only -- none of it submits a
# broker order -- so the second call re-fetched the exact same live
# broker positions and re-evaluated the exact same exit conditions with
# no new information to act on. Its only actual effect was risk: if a
# SELL submitted by the FIRST call hadn't fully propagated on the
# broker's own side by the time this second call re-queried moments
# later in the same script pass, it could attempt to exit the same
# position again. Removed entirely rather than guarded, since nothing
# below this point needs risk-management's exit *decisions* re-run --
# only fresh portfolio_value/asset_allocation numbers, which the calls
# below already recompute from whatever state the first call above left
# broker positions in.

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

# Durable equity-curve snapshot for the Real-Money Readiness
# Scorecard's max-drawdown calculation (engines/equity_tracker.py).
# Throttled internally to once per 15 minutes, so it's safe to call on
# every script run. Deliberately NOT the same portfolio_value used for
# the net-worth display above -- that includes the full raw Binance
# wallet balance (testnet dust, etc.), which isn't attributable to the
# bot's own trading and would misrepresent drawdown. See
# calculate_bot_attributable_portfolio_value()'s docstring.
equity_tracker.log_equity_snapshot(
    calculate_bot_attributable_portfolio_value(market_df)
)

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

st.divider()

# =========================
# POSITION-CAP ROTATION (manual approval required)
# =========================
st.subheader("🔄 Rotation Candidates")
st.caption(
    "When an asset class's position cap is full, a strong new BUY signal "
    "gets skipped entirely -- even if it's a much better opportunity than "
    "the weakest thing currently held. This suggests swapping the two "
    "when the gap is big enough and the held position has had a fair "
    "chance to work. Nothing here executes on its own -- confirm each "
    "swap below."
)

rotation_candidates = find_rotation_candidates(market_df, buy_signals)

# 2026-08-24: candidates used to be purely live -- recomputed fresh every
# render with nothing written anywhere, so a suggestion that scrolled by
# unconfirmed left zero trace of ever having existed. Recording each one
# here (deduped against the last logged pair per asset class, so an
# unconfirmed suggestion sitting on screen across autorefresh cycles logs
# once, not every 5 minutes) feeds the history table rendered below.
for candidate in rotation_candidates:
    rotation_history.record_candidate_seen(candidate)

if not rotation_candidates:
    st.info("No rotation suggestions right now.")
else:
    for candidate in rotation_candidates:
        candidate_key = (
            f"{candidate['asset_class']}_{candidate['weak_ticker']}_"
            f"{candidate['candidate_ticker']}"
        )
        with st.container(border=True):
            st.markdown(
                f"**{candidate['asset_class']}** -- close "
                f"**{candidate['weak_ticker']}** (score "
                f"{candidate['weak_score']:.1f}, held "
                f"{candidate['hours_held']:.1f}h) to open "
                f"**{candidate['candidate_ticker']}** (score "
                f"{candidate['candidate_score']:.1f}) -- gap "
                f"{candidate['gap']:.1f} points."
            )
            if candidate["asset_class"] == "US_STOCKS" and not broker_health.get("market_open", False):
                st.caption(
                    "US stock market is currently closed -- confirming this "
                    "now won't complete. Next open: "
                    f"{broker_health.get('next_market_open', 'Unknown')}."
                )
            if st.button("✅ Confirm Rotation", key=f"confirm_rotation_{candidate_key}"):
                # 2026-08-11: this button never had the same autorefresh
                # guard the manual Execute Trades button and Auto-Trading
                # already use (see the AUTO REFRESH section comment near
                # the top of this file) -- so the 5-minute refresh timer
                # could fire mid-rotation and cut the script off before
                # execute_rotation() finished, with no error and no
                # record of what happened. Setting the same flag here
                # closes that gap the same way it's already closed
                # elsewhere.
                st.session_state.trade_execution_in_progress = True

                # Record the confirmation regardless of how execute_rotation()
                # below turns out -- an approved-and-attempted swap is the
                # useful signal for the history table, separate from whether
                # it fully completed (that detail lives in trade_messages/
                # last_execution_result like every other execution path).
                rotation_history.mark_candidate_confirmed(candidate)

                st.session_state.trade_messages = []
                execute_rotation(candidate, market_df)

                # Also record this in last_execution_result -- previously
                # only the manual Execute Trades button did this, so a
                # rotation attempt never showed up in the "Last Execution
                # Attempt" section at all, even when it worked. That made
                # a genuinely-interrupted rotation look identical to a
                # successful one that just wasn't visible anywhere.
                st.session_state.last_execution_result = {
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "messages": list(st.session_state.trade_messages),
                }
                st.session_state.trade_execution_in_progress = False
                st.rerun()

with st.expander("Rotation Candidate History"):
    st.caption(
        "Every distinct rotation suggestion that's appeared, oldest "
        "action first not shown here -- newest at the top. 'Confirmed' "
        "means you clicked Confirm Rotation for it; a suggestion that "
        "was never confirmed just means the picture changed (the weak "
        "position, or the best open candidate, was different) before "
        "you acted on it -- not necessarily a missed opportunity."
    )
    history_rows = rotation_history.get_recent_history(limit=30)
    if not history_rows:
        st.caption("No rotation candidates recorded yet.")
    else:
        history_df = pd.DataFrame(history_rows).rename(columns={
            "recorded_at": "First Seen",
            "asset_class": "Asset Class",
            "weak_ticker": "Weak Ticker",
            "weak_score": "Weak Score",
            "hours_held": "Hours Held",
            "candidate_ticker": "Candidate",
            "candidate_score": "Candidate Score",
            "gap": "Gap",
            "confirmed": "Confirmed",
            "confirmed_at": "Confirmed At",
        })
        history_df["Confirmed"] = history_df["Confirmed"].map({1: "Yes", 0: "No"})
        history_df["Weak Score"] = history_df["Weak Score"].round(1)
        history_df["Candidate Score"] = history_df["Candidate Score"].round(1)
        history_df["Gap"] = history_df["Gap"].round(1)
        history_df["Hours Held"] = history_df["Hours Held"].round(1)
        st.dataframe(history_df, use_container_width=True, hide_index=True)

st.divider()

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
        #
        # 2026-08-24 (Moderate Finding #5, full codebase audit): this
        # paragraph's "must never" was only actually backed by
        # AUTO_ETORO_TRADING_LOCKED alone -- a single config.py flag, no
        # different in strength from any other soft setting, despite the
        # comment implying a hard guarantee. Now gated on BOTH that flag
        # AND _ETORO_AUTO_TRADING_HARD_BLOCK below, a second, independent
        # gate that lives in this file rather than config.py -- so a
        # changed/misconfigured/reset config.py alone can no longer
        # enable eToro auto-trading. Both would have to change before
        # this could ever fire, which is what "must never" should
        # actually mean.
        auto_paper_only_buy = collect_paper_only_signals(auto_buy)
        auto_paper_only_sell = collect_paper_only_signals(auto_sell)

        if not auto_paper_only_buy.empty or not auto_paper_only_sell.empty:
            if ETORO_LIVE_TRADING and (AUTO_ETORO_TRADING_LOCKED or _ETORO_AUTO_TRADING_HARD_BLOCK):
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

# Sharpe/Sortino and the monthly breakdown are computed from the trade
# journal (performance_engine), same as the block above -- but unlike
# that block, these render regardless of LIVE_TRADING. The journal is
# broker-agnostic (every execution path writes to it), so there's no
# reason to hide risk-adjusted stats just because stocks are routing
# through real Alpaca fills instead of the local simulator.
risk_adjusted_metrics = performance_engine.calculate_risk_adjusted_metrics()

e1, e2 = st.columns(2)

with e1:
    sharpe_ratio = risk_adjusted_metrics.get("sharpe_ratio")
    st.metric(
        "Sharpe Ratio (per trade)",
        "N/A" if sharpe_ratio is None else f"{sharpe_ratio:.2f}"
    )

with e2:
    sortino_ratio = risk_adjusted_metrics.get("sortino_ratio")
    st.metric(
        "Sortino Ratio (per trade)",
        "N/A" if sortino_ratio is None else f"{sortino_ratio:.2f}"
    )

if risk_adjusted_metrics.get("sample_size", 0) < 2:
    st.caption(
        "Sharpe/Sortino need at least 2 closed trades to compute a "
        "meaningful standard deviation -- showing N/A until then."
    )
else:
    st.caption(
        "Computed per-trade from closed-trade % returns (not an "
        "annualized daily-return Sharpe/Sortino) -- risk-free rate "
        "assumed 0."
    )

monthly_returns = performance_engine.calculate_monthly_returns()
if monthly_returns:
    st.markdown("**Monthly Return Breakdown**")
    monthly_returns_df = pd.DataFrame(monthly_returns).rename(columns={
        "month": "Month",
        "trades_closed": "Trades Closed",
        "wins": "Wins",
        "losses": "Losses",
        "win_rate": "Win Rate %",
        "total_pnl": "Total P&L ($)",
    })
    monthly_returns_df["Win Rate %"] = monthly_returns_df["Win Rate %"].round(1)
    monthly_returns_df["Total P&L ($)"] = monthly_returns_df["Total P&L ($)"].round(2)
    st.dataframe(monthly_returns_df, width="stretch", hide_index=True)

# 2026-08-24: same journal, grouped by what actually closed each trade
# (stop-loss, take-profit, break-even, partial-profit, trailing-lock,
# time-limit exit, or a plain AI SELL signal) instead of one blended
# total -- lets us see which exit mechanism is actually earning money.
# See performance_engine.calculate_strategy_breakdown()'s docstring for
# why eToro (forex/commodities) exits don't appear here: those close
# broker-side once a stop-loss/take-profit level is hit, so this bot
# never logs a reason for them the way it does for stocks/crypto.
strategy_breakdown = performance_engine.calculate_strategy_breakdown()
if strategy_breakdown:
    st.markdown("**Per-Strategy Performance Breakdown**")
    st.caption(
        "Realized P&L grouped by exit type, across stocks and crypto. "
        "eToro (forex/commodities) exits aren't included -- they close "
        "broker-side, not via a bot-initiated sell."
    )
    strategy_df = pd.DataFrame(strategy_breakdown).rename(columns={
        "strategy": "Strategy",
        "trades_closed": "Trades Closed",
        "wins": "Wins",
        "losses": "Losses",
        "win_rate": "Win Rate %",
        "total_pnl": "Total P&L ($)",
        "average_pnl": "Avg P&L/Trade ($)",
    })
    strategy_df["Win Rate %"] = strategy_df["Win Rate %"].round(1)
    strategy_df["Total P&L ($)"] = strategy_df["Total P&L ($)"].round(2)
    strategy_df["Avg P&L/Trade ($)"] = strategy_df["Avg P&L/Trade ($)"].round(2)
    st.dataframe(strategy_df, width="stretch", hide_index=True)

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

# digest["open_positions_by_asset_class"]["US_STOCKS"] counts distinct
# tickers with an unmatched BUY still sitting in the trade journal --
# not what Alpaca actually holds right now. Any SELL that historically
# failed to get journaled (this project has had real bugs of that
# exact shape) leaves that ticker "open" in the journal forever, even
# after it was really closed on the broker. Overriding with a live,
# broker-verified count here so this table can't overstate real
# exposure; falls back to the journal count if the broker call fails,
# same defensive pattern used everywhere else a broker call touches
# the dashboard.
if LIVE_TRADING:
    try:
        digest["open_positions_by_asset_class"]["US_STOCKS"] = len(get_open_positions())
    except Exception:
        pass

# Same staleness bug as US_STOCKS above, for FOREX/COMMODITIES: the real
# eToro-backed position cap (engines/risk_engine.py's
# _get_etoro_position_count, fixed 2026-08-06) already reads live eToro
# data, but this digest table was still reading the same stale journal
# count as stocks were. Overriding with the live-verified count here too;
# falls back to the journal count if the eToro call fails.
if ETORO_LIVE_TRADING:
    for _asset_class in ("FOREX", "COMMODITIES"):
        try:
            digest["open_positions_by_asset_class"][_asset_class] = (
                get_live_forex_commodity_position_count(_asset_class)
            )
        except Exception:
            pass

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
        # Kept as a string in both branches deliberately -- mixing a str
        # ("N/A") and a float in the same pandas column crashes Streamlit's
        # Arrow-based table renderer with "Expected bytes, got a 'float'
        # object" the first time any asset class has zero losing trades
        # (pf is None) while another has real closed trades (pf is a
        # float). Confirmed live 2026-08-23 via journalctl. The
        # scorecard_rows table just below already does it this way.
        "Profit Factor": "N/A" if pf is None else f"{pf:.2f}",
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

st.subheader("🎯 Real-Money Readiness Scorecard")
st.caption(
    "A data-driven checklist for when this bot has actually earned a "
    "move from paper/demo/testnet to real capital -- not a decision "
    "this makes on its own, just an honest read of the numbers so far."
)

scorecard = calculate_readiness_scorecard()

sc1, sc2 = st.columns(2)
with sc1:
    st.metric(
        "Validation Days",
        f"{scorecard['days_elapsed']}/{scorecard['days_required']}",
    )
with sc2:
    drawdown_display = (
        "N/A (not enough history yet)"
        if scorecard["max_drawdown_percent"] is None
        else f"{scorecard['max_drawdown_percent']:.1f}% / {scorecard['max_drawdown_limit']}% limit"
    )
    st.metric("Max Drawdown", drawdown_display)

scorecard_rows = []
for asset_class, stats in scorecard["rows"].items():
    pf = stats["profit_factor"]
    scorecard_rows.append({
        "Asset Class": asset_class,
        "Trades Closed": f"{stats['trades_closed']} / {scorecard['min_trades_required']}",
        "Win Rate %": round(stats["win_rate"], 1),
        "Profit Factor": "N/A" if pf is None else f"{pf:.2f} (min {scorecard['min_profit_factor']})",
        "Total P&L": f"${stats['total_pnl']:,.2f}",
        "Ready?": "✅ Yes" if stats["ready"] else "⏳ Not yet",
    })

# OVERALL first, then asset classes alphabetically.
scorecard_rows.sort(key=lambda r: (r["Asset Class"] != "OVERALL", r["Asset Class"]))
st.dataframe(pd.DataFrame(scorecard_rows), width="stretch", hide_index=True)

if not scorecard["time_ready"]:
    st.info(
        f"Still building track record -- {scorecard['days_required'] - scorecard['days_elapsed']} "
        "more day(s) before the time bar alone is met, regardless of stats."
    )
elif scorecard["rows"].get("OVERALL", {}).get("ready"):
    st.success(
        "Overall stats have cleared every bar. Worth a real, separate "
        "decision before actually switching any broker off paper/demo/"
        "testnet -- this scorecard informs that decision, it doesn't make it."
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

            # Real Alpaca trades here are sized around $100 against a
            # $100k base account, so real day-to-day moves are only a
            # few dollars -- invisible against st.line_chart's default
            # y-axis (which scales from near zero up to the full
            # portfolio value). Zooming to the data's own range instead,
            # same fix already used below for the local-paper fallback
            # chart, so real movement is actually visible here too.
            import altair as alt

            broker_values = broker_equity_df["Portfolio Value"]
            broker_value_min = float(broker_values.min())
            broker_value_max = float(broker_values.max())

            if broker_value_max == broker_value_min:
                broker_padding = max(abs(broker_value_max) * 0.01, 1.0)
            else:
                broker_padding = (broker_value_max - broker_value_min) * 0.1

            broker_equity_chart = (
                alt.Chart(broker_equity_df)
                .mark_line(point=True)
                .encode(
                    x=alt.X("Time:T", title="Time"),
                    y=alt.Y(
                        "Portfolio Value:Q",
                        title="Portfolio Value ($)",
                        scale=alt.Scale(
                            domain=[
                                broker_value_min - broker_padding,
                                broker_value_max + broker_padding,
                            ]
                        ),
                    ),
                    tooltip=["Time", "Portfolio Value"],
                )
                .properties(height=320)
            )

            st.altair_chart(broker_equity_chart, use_container_width=True)
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