"""
AI signal generation -- extracted verbatim from app.py (2026-08-26).

prepare_data(), get_ai_signal(), and get_multi_timeframe_signal() used
to live inside app.py itself, unlike every other piece of decision
logic in this project (strategy_engine.py, scoring_engine.py,
risk_engine.py, trade_planner.py, approval_engine.py, regime_engine.py
are all already separate importable modules). That was a real blocker
for the SaaS work: these three functions are what actually produce
every AI trade signal, and the SaaS per-user execution loop needs to
call the exact same signal logic the single-owner bot uses -- but
importing directly from app.py isn't safe (it's a Streamlit script with
top-level st.* calls and session-state-dependent setup that runs at
import time), and duplicating 150+ lines of feature-engineering/model-
inference logic by hand would risk a silent mismatch between what the
live bot decides and what the SaaS decides for identical inputs.

This file is a byte-for-byte logic extraction, not a rewrite: all three
functions are unchanged from their original app.py versions except for
imports (pandas/ta/get_market_data/BUY_CONFIDENCE/SELL_CONFIDENCE, all
already used identically in app.py). app.py itself now imports these
three functions from here instead of defining them locally -- same
names, same signatures, same call sites, zero behavior change for the
existing single-owner bot. Verified via py_compile plus a live deploy/
restart/log check before this was trusted with the live bot (see
git log for that verification).
"""

import pandas as pd
import ta

from engines.market_data_engine import get_market_data
from config import BUY_CONFIDENCE, SELL_CONFIDENCE


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
