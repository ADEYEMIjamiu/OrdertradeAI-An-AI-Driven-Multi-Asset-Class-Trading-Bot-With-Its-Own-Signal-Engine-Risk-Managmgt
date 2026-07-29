import pandas as pd

from engines.market_data_engine import get_market_data
from config import (
    MARKET_RISK_LOW,
    MARKET_RISK_MEDIUM,
    MARKET_RISK_HIGH,
    AGGRESSIVE_RISK_MULTIPLIER,
    NORMAL_RISK_MULTIPLIER,
    DEFENSIVE_RISK_MULTIPLIER,
    DANGER_RISK_MULTIPLIER,
    REGIME_STRONG_BULL_SCORE,
    REGIME_BULL_SCORE,
    REGIME_NEUTRAL_SCORE,
    REGIME_DEFENSIVE_SCORE,
)

def get_market_risk_level(market_df):
    """
    Measures market risk using average absolute daily movement.
    """

    # ✅ Safe column handling
    if "Daily Change %" not in market_df.columns:
        market_df["Daily Change %"] = 0

    # ✅ Convert safely
    daily_change = pd.to_numeric(
        market_df["Daily Change %"], errors="coerce"
    ).fillna(0)

    avg_volatility = daily_change.abs().mean()

    if avg_volatility < MARKET_RISK_LOW:
        return "LOW", AGGRESSIVE_RISK_MULTIPLIER

    elif avg_volatility < MARKET_RISK_MEDIUM:
        return "NORMAL", NORMAL_RISK_MULTIPLIER

    elif avg_volatility < MARKET_RISK_HIGH:
        return "DEFENSIVE", DEFENSIVE_RISK_MULTIPLIER

    else:
        return "DANGER", DANGER_RISK_MULTIPLIER
    
def get_market_regime():
    """
    Determines the current broad market regime using SPY.
    """

    try:
        spy = get_market_data(
    "SPY",
    period="1y",
    interval="1d",
)
        if spy.empty:
            raise ValueError(
                "No SPY market data returned for regime analysis."
    )
        if isinstance(spy.columns, pd.MultiIndex):
            spy.columns = spy.columns.get_level_values(0)

        spy = spy.dropna()

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

    except Exception as e:
        return "UNKNOWN", 0