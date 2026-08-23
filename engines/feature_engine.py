"""
Shared technical-indicator computation -- the exact same formula used by
train_model.py (what the model was fit on) and app.py's prepare_data()
(what feeds the live dashboard's yfinance-based signals). Extracted here
2026-08-22 so crypto_scalping_engine.py's Binance-native data can be run
through the identical calculation, rather than a second hand-copied
version that could quietly drift out of sync with what the model
actually expects.

Do not change the formulas here without also updating train_model.py --
the model's FEATURES contract (SMA20, SMA50, RSI, MACD, Returns,
Volatility) has to match on both sides or its predictions become
meaningless.
"""

import pandas as pd
import ta


def compute_technical_features(df):
    """
    Takes an OHLCV DataFrame (must have a "Close" column -- Open/High/
    Low/Volume are not required by these particular indicators, so this
    works whether the frame came from yfinance or Binance) and returns
    it with SMA20, SMA50, RSI, MACD, Returns, and Volatility columns
    added, rows with insufficient history for those windows dropped.
    """
    if df is None or df.empty:
        raise ValueError("compute_technical_features() received an empty DataFrame")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.dropna()

    if "Close" not in df.columns:
        raise ValueError("compute_technical_features(): 'Close' column is missing")

    close = df["Close"].squeeze()

    df["SMA20"] = ta.trend.sma_indicator(close, window=20)
    df["SMA50"] = ta.trend.sma_indicator(close, window=50)
    df["RSI"] = ta.momentum.rsi(close, window=14)
    df["MACD"] = ta.trend.macd(close)
    df["Returns"] = close.pct_change()
    df["Volatility"] = df["Returns"].rolling(20).std()

    df = df.dropna()

    if len(df) < 2:
        raise ValueError(
            "compute_technical_features(): insufficient rows after indicator warm-up"
        )

    return df
