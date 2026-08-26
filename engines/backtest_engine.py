"""
Offline backtesting engine.

Replays the SAME decision pipeline live trading uses -- signal_engine's
AI model inference, strategy_engine's labeling, trade_planner's ATR
stop-loss/swing-high-low take-profit/grade math, scoring_engine's trade
score, approval_engine's gate, and risk_engine.calculate_trade_amount's
position sizing -- against historical OHLCV data, so the model/config
can be validated across years of history instead of only forward-tested
in paper trading (which takes months to accumulate enough closed trades
to say anything statistically meaningful).

WHY THIS EXISTS: two months into live/paper operation, every tuning
decision made on approval_engine.py/scoring_engine.py/config.py's
thresholds (confidence gates, RR minimums, trend-direction filters --
see git history for FIX entries on 2026-08-25/26) had to be validated by
waiting days for enough live signals to occur naturally. A backtest
turns that into minutes, over years of data, before either committing
capital to a real change or waiting weeks to see if it worked.

DESIGN PRINCIPLE: reuse, don't reimplement. strategy_engine.py,
scoring_engine.py, approval_engine.py, and risk_engine.calculate_trade_amount
are imported and called completely unmodified below -- if live trading's
config changes, the backtest automatically reflects it, and there is no
risk of the backtest silently drifting from what live decisions actually
do. Only the three functions that make live network calls
(signal_engine.prepare_data/get_ai_signal/get_multi_timeframe_signal,
trade_planner.prepare_trade_data/create_trade_plan, regime_engine.
get_market_regime) have offline counterparts here -- and those
counterparts are line-for-line copies of the live math, just sourcing a
pre-loaded historical DataFrame slice instead of a fresh yfinance call.

SCOPE / KNOWN DIVERGENCES FROM LIVE (read before trusting these numbers):

1. TREND SCORE IS DAILY-ONLY. Live get_multi_timeframe_signal() checks
   1d + 1h + 15m SMA20/50 agreement (Trend Score range -3..+3). Yahoo
   Finance only retains ~2 years of 1h history and ~60 days of 15m
   history, so replaying either intraday timeframe accurately across a
   multi-year backtest isn't possible. This backtest computes Trend
   Score from the 1d timeframe only (range -1..+1, using the exact same
   bullish/bearish/mixed SMA20-vs-SMA50 rule as the live 1d branch) --
   every trend-dependent downstream calculation (get_ai_signal's
   confidence bonus, scoring_engine's trend bonus, strategy_engine's
   strategy label, approval_engine's trend filter) will differ somewhat
   from what live would have decided on the same historical date. This
   is the single biggest known fidelity gap; everything else replicates
   live logic exactly.
2. NO INTRABAR FILL PRECEDENCE. If both stop-loss and take-profit would
   have been hit within the same daily bar (Low <= stop AND High >=
   target for a BUY), this assumes stop-loss triggered first (the
   conservative assumption) since daily OHLC data alone can't tell us
   the real intraday order of events.
3. NO SLIPPAGE/COMMISSION/FUNDING MODEL. Fills happen at the exact
   stop/target/entry price with no spread, slippage, commission, or
   (for leveraged eToro-style CFDs) overnight funding cost. Real fills
   will differ, especially on leveraged FOREX/COMMODITIES positions.
4. VALIDATES THE DECISION LAYER, NOT BROKER EXECUTION MECHANICS. Any
   yfinance-priced instrument can be backtested (stocks, ETFs, crypto
   pairs like BTC-USD, forex pairs like EURUSD=X, futures like GC=F) --
   this proves out the AI signal/config logic, not eToro's margin
   rules, Binance's fee schedule, or Alpaca's fractional-share handling.
5. get_market_regime() (SPY-based) is computed and returned for context
   only -- it does NOT gate trades here, matching live: approval_engine.
   py's gate uses static confidence/RR/trend thresholds from config.py,
   not the regime score.
6. A KNOWN LIVE QUIRK IS DELIBERATELY PRESERVED, NOT FIXED:
   strategy_engine.identify_strategy() reads `row.get("Symbol", "")` to
   check "already holding this ticker" and skip a duplicate BUY label,
   but every row this project builds (live app.py's market_df AND this
   backtest's rows) only ever sets a "Ticker" key, never "Symbol" -- so
   that check silently never matches anything, live or here. Preserving
   this (rather than quietly fixing it just for the backtest) keeps the
   backtest's Strategy labels faithful to what live actually assigns
   today. Worth fixing in strategy_engine.py itself in a follow-up --
   flagged here rather than fixed here since silently changing it would
   make the backtest MORE correct than live, defeating the point of a
   fidelity check.
"""

import os
import time
from datetime import datetime, timedelta

import pandas as pd
import ta
import yfinance as yf
import joblib

from config import (
    BUY_CONFIDENCE,
    SELL_CONFIDENCE,
    ATR_STOP_MULTIPLIER,
    TRADE_PLAN_LOOKBACK_DAYS,
    GRADE_A_PLUS_CONFIDENCE,
    GRADE_A_PLUS_RISK_REWARD,
    GRADE_A_CONFIDENCE,
    GRADE_A_RISK_REWARD,
    GRADE_B_CONFIDENCE,
    GRADE_B_RISK_REWARD,
    GRADE_C_CONFIDENCE,
    MAX_HOLD_DAYS_HARD,
    REGIME_STRONG_BULL_SCORE,
    REGIME_BULL_SCORE,
    REGIME_NEUTRAL_SCORE,
    REGIME_DEFENSIVE_SCORE,
)

from engines.strategy_engine import identify_strategy, score_strategy
from engines.scoring_engine import calculate_trade_score
from engines.approval_engine import approve_trade
from engines.risk_engine import calculate_trade_amount

MODEL_PATH = "models/trading_model.pkl"
FEATURES_PATH = "models/features.pkl"

_CACHE_DIR = "backtest_cache"
_WARMUP_CALENDAR_DAYS = 180  # enough trading days for SMA50/ATR14/rolling-20 warm-up
_MIN_WARMUP_BARS = 55        # SMA50 is the longest rolling window any of these need


# ---------------------------------------------------------------------------
# Historical data loading (download once per ticker, cache locally, slice
# in-memory per simulated day -- never a live call per bar).
# ---------------------------------------------------------------------------

def _cache_path(ticker, start, end):
    os.makedirs(_CACHE_DIR, exist_ok=True)
    safe_ticker = ticker.replace("/", "_").replace("=", "_")
    return os.path.join(_CACHE_DIR, f"{safe_ticker}_{start}_{end}.pkl")


def load_history(ticker, start, end, max_retries=3):
    """
    Downloads daily OHLCV for [start - warmup buffer, end] once, caches
    it to disk (backtest_cache/), and returns it on every subsequent
    call for the same ticker/range without hitting the network again.
    Raises RuntimeError if no data could be fetched after retries --
    callers should treat that ticker as unusable for this backtest run
    rather than silently skipping it.
    """
    warmup_start = (pd.Timestamp(start) - timedelta(days=_WARMUP_CALENDAR_DAYS)).date().isoformat()
    path = _cache_path(ticker, warmup_start, end)

    if os.path.exists(path):
        return pd.read_pickle(path)

    last_error = None
    for attempt in range(max_retries):
        try:
            df = yf.download(ticker, start=warmup_start, end=end, interval="1d", auto_adjust=True, progress=False)
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.dropna()
                if not df.empty:
                    df.to_pickle(path)
                    return df
            last_error = ValueError(f"No data returned for {ticker}")
        except Exception as e:
            last_error = e
        time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Could not load historical data for {ticker}: {last_error}")


# ---------------------------------------------------------------------------
# Offline counterparts to signal_engine.py -- same formulas, sourced from a
# pre-loaded historical slice instead of a live get_market_data() call.
# ---------------------------------------------------------------------------

def _offline_prepare_data(hist_df, as_of_idx):
    """Line-for-line copy of signal_engine.prepare_data()'s indicator math,
    computed over hist_df[: as_of_idx + 1] instead of a fresh live fetch."""
    df = hist_df.iloc[: as_of_idx + 1].copy()
    close = df["Close"].squeeze()

    df["SMA20"] = ta.trend.sma_indicator(close, window=20)
    df["SMA50"] = ta.trend.sma_indicator(close, window=50)
    df["RSI"] = ta.momentum.rsi(close, window=14)
    df["MACD"] = ta.trend.macd(close)
    df["Returns"] = close.pct_change()
    df["Volatility"] = df["Returns"].rolling(20).std()

    return df.dropna()


def _offline_trend_score(hist_df, as_of_idx):
    """Daily-timeframe-only replica of get_multi_timeframe_signal()'s 1d
    branch (see module docstring point 1 for why 1h/15m aren't included)."""
    df = hist_df.iloc[: as_of_idx + 1]
    if len(df) < 50:
        return 0, ["1d: insufficient data"]

    close = df["Close"]
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    latest_close = float(close.iloc[-1])

    if pd.isna(sma20) or pd.isna(sma50):
        return 0, ["1d: insufficient indicator data"]

    sma20, sma50 = float(sma20), float(sma50)

    if latest_close > sma20 > sma50:
        return 1, ["1d: bullish"]
    elif latest_close < sma20 < sma50:
        return -1, ["1d: bearish"]
    else:
        return 0, ["1d: mixed"]


def offline_get_ai_signal(ticker, model, features, hist_df, as_of_idx):
    """Offline counterpart to signal_engine.get_ai_signal() -- identical
    confidence/signal math, sourced from hist_df instead of a live fetch."""
    df = _offline_prepare_data(hist_df, as_of_idx)
    if len(df) < 2:
        raise ValueError(f"Insufficient prepared data for {ticker} at this date")

    latest = df.iloc[-1]
    previous = df.iloc[-2]
    X_live = df[features].tail(1)

    probability_up = model.predict_proba(X_live)[0][1]

    price = float(latest["Close"])
    previous_price = float(previous["Close"])
    daily_change = ((price / previous_price) - 1) * 100

    mtf_score, mtf_details = _offline_trend_score(hist_df, as_of_idx)

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
        "Trend Details": ", ".join(mtf_details),
    }


def offline_create_trade_plan(row, hist_df, as_of_idx):
    """Line-for-line copy of trade_planner.create_trade_plan()'s ATR
    stop-loss / swing-high-low take-profit / grade math, sourced from
    hist_df instead of a live prepare_trade_data() fetch."""
    entry_price = float(row["Price ($)"])
    confidence = float(row["AI Confidence %"])
    trend_score = float(row["Trend Score"])
    signal = row["Signal"]

    try:
        df = hist_df.iloc[: as_of_idx + 1].dropna()
        if df.empty:
            raise ValueError("No market data available")

        atr = ta.volatility.average_true_range(
            high=df["High"], low=df["Low"], close=df["Close"], window=14
        ).iloc[-1]
        atr = float(atr)

        lookback = min(TRADE_PLAN_LOOKBACK_DAYS, len(df))
        recent_high = float(df["High"].tail(lookback).max())
        recent_low = float(df["Low"].tail(lookback).min())

        if signal == "BUY":
            stop_loss = entry_price - (atr * ATR_STOP_MULTIPLIER)
            take_profit = recent_high
            reward = take_profit - entry_price
        elif signal == "SELL":
            stop_loss = entry_price + (atr * ATR_STOP_MULTIPLIER)
            take_profit = recent_low
            reward = entry_price - take_profit
        else:
            return {
                "Trade Decision": "WAIT",
                "Stop Loss": None,
                "Take Profit": None,
                "Risk Reward": None,
                "Trade Grade": "C",
                "Trade Reason": "No active signal",
            }

        risk = abs(entry_price - stop_loss)
        risk_reward = reward / risk if risk > 0 else 0

        decision = signal if confidence >= GRADE_C_CONFIDENCE else "WAIT"

        if confidence >= GRADE_A_PLUS_CONFIDENCE and risk_reward >= GRADE_A_PLUS_RISK_REWARD:
            grade = "A+"
        elif confidence >= GRADE_A_CONFIDENCE and risk_reward >= GRADE_A_RISK_REWARD:
            grade = "A"
        elif confidence >= GRADE_B_CONFIDENCE and risk_reward >= GRADE_B_RISK_REWARD:
            grade = "B"
        elif confidence >= GRADE_C_CONFIDENCE:
            grade = "C"
        else:
            grade = "D"

        reason = (
            f"{signal} | Conf={round(confidence,1)}% | "
            f"Trend={trend_score} | RR={round(risk_reward,2)}"
        )

        return {
            "Trade Decision": decision,
            "Stop Loss": round(stop_loss, 2),
            "Take Profit": round(take_profit, 2),
            "Risk Reward": round(risk_reward, 2),
            "Trade Grade": grade,
            "Trade Reason": reason,
        }

    except Exception as e:
        return {
            "Trade Decision": "ERROR",
            "Stop Loss": None,
            "Take Profit": None,
            "Risk Reward": None,
            "Trade Grade": "ERROR",
            "Trade Reason": str(e),
        }


def offline_get_market_regime(spy_hist_df, as_of_idx):
    """Offline counterpart to regime_engine.get_market_regime() -- context
    only, does not gate trades (see module docstring point 5)."""
    try:
        spy = spy_hist_df.iloc[: as_of_idx + 1].copy()
        if len(spy) < 200:
            return "UNKNOWN", 0

        spy["SMA50"] = spy["Close"].rolling(50).mean()
        spy["SMA200"] = spy["Close"].rolling(200).mean()
        spy["Return_20D"] = spy["Close"].pct_change(20)
        spy["Volatility_20D"] = spy["Close"].pct_change().rolling(20).std()

        latest = spy.iloc[-1]
        score = 0
        if latest["Close"] > latest["SMA50"]:
            score += 25
        if latest["Close"] > latest["SMA200"]:
            score += 25
        if latest["Return_20D"] > 0:
            score += 25
        if latest["Volatility_20D"] < spy["Volatility_20D"].mean():
            score += 25

        if score >= REGIME_STRONG_BULL_SCORE:
            regime = "STRONG BULL"
        elif score >= REGIME_BULL_SCORE:
            regime = "BULL"
        elif score >= REGIME_NEUTRAL_SCORE:
            regime = "NEUTRAL"
        elif score >= REGIME_DEFENSIVE_SCORE:
            regime = "DEFENSIVE"
        else:
            regime = "BEAR"

        return regime, score
    except Exception:
        return "UNKNOWN", 0


# ---------------------------------------------------------------------------
# Simulation loop
# ---------------------------------------------------------------------------

def run_backtest(tickers, start, end, initial_balance=10000.0, leverage=1, max_open_positions=None, verbose=False):
    """
    Walk-forward simulation over [start, end] for the given ticker list,
    calling the same signal -> strategy -> trade_plan -> score -> approval
    -> position-size pipeline live trading uses (see module docstring).

    Returns a dict: {"trades": [...closed round-trips...],
    "equity_curve": [(date, equity), ...], "metrics": {...},
    "final_equity": float, "final_regime": (regime, score)}.
    """
    model = joblib.load(MODEL_PATH)
    features = joblib.load(FEATURES_PATH)

    hist = {}
    for ticker in tickers:
        hist[ticker] = load_history(ticker, start, end)

    spy_hist = load_history("SPY", start, end)

    start_ts = pd.Timestamp(start)
    all_dates = sorted(set().union(*[
        set(df.index[df.index >= start_ts]) for df in hist.values()
    ]))

    cash = float(initial_balance)
    open_positions = {}   # ticker -> position dict
    trades = []            # closed round-trips
    equity_curve = []

    for date in all_dates:
        # --- 1. Check exits for every open position using today's bar ---
        for ticker in list(open_positions):
            df = hist[ticker]
            if date not in df.index:
                continue
            bar = df.loc[date]
            pos = open_positions[ticker]

            exit_price, exit_reason = None, None
            days_open = (date - pos["entry_date"]).days

            if pos["side"] == "BUY":
                if float(bar["Low"]) <= pos["stop_loss"]:
                    exit_price, exit_reason = pos["stop_loss"], "Stop-loss hit"
                elif float(bar["High"]) >= pos["take_profit"]:
                    exit_price, exit_reason = pos["take_profit"], "Take-profit hit"
            else:  # SELL
                if float(bar["High"]) >= pos["stop_loss"]:
                    exit_price, exit_reason = pos["stop_loss"], "Stop-loss hit"
                elif float(bar["Low"]) <= pos["take_profit"]:
                    exit_price, exit_reason = pos["take_profit"], "Take-profit hit"

            if exit_price is None and days_open >= MAX_HOLD_DAYS_HARD:
                exit_price = float(bar["Close"])
                exit_reason = f"Max hold time exceeded ({days_open}d)"

            if exit_price is not None:
                if pos["side"] == "BUY":
                    pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
                else:
                    pnl = (pos["entry_price"] - exit_price) * pos["quantity"]

                cash += pos["cost"] + pnl
                pnl_percent = (pnl / pos["cost"]) * 100 if pos["cost"] else 0.0

                trades.append({
                    "ticker": ticker,
                    "side": pos["side"],
                    "entry_date": pos["entry_date"],
                    "exit_date": date,
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "quantity": pos["quantity"],
                    "confidence": pos["confidence"],
                    "trend_score": pos["trend_score"],
                    "trade_grade": pos["trade_grade"],
                    "strategy": pos["strategy"],
                    "exit_reason": exit_reason,
                    "pnl": pnl,
                    "pnl_percent": pnl_percent,
                })
                del open_positions[ticker]

        # --- 2. Generate today's signals across the whole universe ---
        rows = []
        for ticker in tickers:
            df = hist[ticker]
            if date not in df.index:
                continue
            idx = df.index.get_loc(date)
            if idx < _MIN_WARMUP_BARS:
                continue
            try:
                rows.append(offline_get_ai_signal(ticker, model, features, df, idx))
            except Exception as e:
                if verbose:
                    print(f"[{date.date()}] {ticker}: signal error -- {e}")
                continue

        if not rows:
            equity_curve.append((date, _mark_to_market(cash, open_positions, hist, date)))
            continue

        market_df = pd.DataFrame(rows)

        # --- 3. Strategy label / trade plan / score / approval / sizing ---
        for i in range(len(market_df)):
            row = market_df.iloc[i].to_dict()
            ticker = row["Ticker"]

            if ticker in open_positions:
                continue  # already holding -- mirrors has_open_position_for_user's intent
            if max_open_positions is not None and len(open_positions) >= max_open_positions:
                continue

            idx = hist[ticker].index.get_loc(date)

            row["Strategy"] = identify_strategy(row)
            row["Strategy Score"] = score_strategy(row)

            plan = offline_create_trade_plan(row, hist[ticker], idx)
            row.update(plan)

            row["AI Trade Score"] = calculate_trade_score(row)

            approved, reason = approve_trade(row, open_positions_count=len(open_positions))
            if not approved:
                if verbose:
                    print(f"[{date.date()}] {ticker}: rejected -- {reason}")
                continue
            if row.get("Stop Loss") is None or row.get("Take Profit") is None:
                continue

            confidence = row["AI Confidence %"]
            entry_price = row["Price ($)"]
            stop_loss = row["Stop Loss"]

            trade_amount = calculate_trade_amount(
                confidence=confidence,
                market_df=market_df,
                entry_price=entry_price,
                stop_loss=stop_loss,
                leverage=leverage,
                account_balance=cash,
            )

            if trade_amount <= 0 or trade_amount > cash:
                if verbose:
                    print(f"[{date.date()}] {ticker}: skipped -- insufficient balance (${trade_amount:.2f} vs ${cash:.2f} free)")
                continue

            quantity = trade_amount / entry_price
            cash -= trade_amount

            open_positions[ticker] = {
                "side": row["Signal"],
                "entry_date": date,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": row["Take Profit"],
                "quantity": quantity,
                "cost": trade_amount,
                "confidence": confidence,
                "trend_score": row["Trend Score"],
                "trade_grade": row["Trade Grade"],
                "strategy": row["Strategy"],
            }

            if verbose:
                print(f"[{date.date()}] {ticker}: OPENED {row['Signal']} @ {entry_price} "
                      f"(${trade_amount:.2f}, grade={row['Trade Grade']}, conf={confidence}%)")

        equity_curve.append((date, _mark_to_market(cash, open_positions, hist, date)))

    # --- Force-close anything still open at the end of the range at last close ---
    for ticker, pos in list(open_positions.items()):
        df = hist[ticker]
        last_date = df.index[df.index <= pd.Timestamp(end)][-1]
        exit_price = float(df.loc[last_date, "Close"])
        if pos["side"] == "BUY":
            pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
        else:
            pnl = (pos["entry_price"] - exit_price) * pos["quantity"]
        cash += pos["cost"] + pnl
        pnl_percent = (pnl / pos["cost"]) * 100 if pos["cost"] else 0.0
        trades.append({
            "ticker": ticker,
            "side": pos["side"],
            "entry_date": pos["entry_date"],
            "exit_date": last_date,
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "quantity": pos["quantity"],
            "confidence": pos["confidence"],
            "trend_score": pos["trend_score"],
            "trade_grade": pos["trade_grade"],
            "strategy": pos["strategy"],
            "exit_reason": "Backtest window ended (forced close)",
            "pnl": pnl,
            "pnl_percent": pnl_percent,
        })

    final_regime = offline_get_market_regime(spy_hist, len(spy_hist) - 1)

    return {
        "trades": trades,
        "equity_curve": equity_curve,
        "metrics": compute_metrics(trades),
        "final_equity": cash,
        "final_regime": final_regime,
    }


def _mark_to_market(cash, open_positions, hist, date):
    equity = cash
    for ticker, pos in open_positions.items():
        df = hist[ticker]
        if date in df.index:
            price = float(df.loc[date, "Close"])
        else:
            price = pos["entry_price"]
        if pos["side"] == "BUY":
            equity += pos["cost"] + (price - pos["entry_price"]) * pos["quantity"]
        else:
            equity += pos["cost"] + (pos["entry_price"] - price) * pos["quantity"]
    return equity


# ---------------------------------------------------------------------------
# Metrics -- same definitions as engines/performance_engine.py's
# calculate_performance_metrics()/calculate_risk_adjusted_metrics(),
# reimplemented against an in-memory trades list instead of trade_journal.db
# (see that module's docstrings for why risk-free rate = 0 and why these
# are per-trade, not annualized, Sharpe/Sortino ratios).
# ---------------------------------------------------------------------------

def compute_metrics(trades):
    if not trades:
        return {
            "trades_closed": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "total_pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0,
            "profit_factor": None, "expectancy": 0.0, "average_win": 0.0,
            "average_loss": 0.0, "max_drawdown": 0.0,
            "sharpe_ratio": None, "sortino_ratio": None,
        }

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    total_pnl = gross_profit - gross_loss

    win_rate = (len(wins) / len(trades)) * 100
    average_win = (gross_profit / len(wins)) if wins else 0.0
    average_loss = (gross_loss / len(losses)) if losses else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    expectancy = total_pnl / len(trades)

    cumulative, peak, max_drawdown = 0.0, 0.0, 0.0
    for t in trades:
        cumulative += t["pnl"]
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    n = len(trades)
    sharpe_ratio, sortino_ratio = None, None
    if n >= 2:
        returns = [t["pnl_percent"] / 100 for t in trades]
        mean_return = sum(returns) / n
        variance = sum((r - mean_return) ** 2 for r in returns) / (n - 1)
        std_dev = variance ** 0.5
        sharpe_ratio = (mean_return / std_dev) if std_dev > 0 else None

        downside_sq_sum = sum(min(r, 0.0) ** 2 for r in returns)
        downside_deviation = (downside_sq_sum / n) ** 0.5
        sortino_ratio = (mean_return / downside_deviation) if downside_deviation > 0 else None

    return {
        "trades_closed": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "average_win": average_win,
        "average_loss": average_loss,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
    }
