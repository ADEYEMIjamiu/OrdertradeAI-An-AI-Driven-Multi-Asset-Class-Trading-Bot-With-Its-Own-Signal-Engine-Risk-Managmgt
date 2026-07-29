from engines.market_data_engine import get_market_data
import ta

from config import (
    ATR_STOP_MULTIPLIER,
    ATR_TAKE_PROFIT_MULTIPLIER,
    TRADE_PLAN_LOOKBACK_DAYS,
    GRADE_A_PLUS_CONFIDENCE,
    GRADE_A_PLUS_RISK_REWARD,
    GRADE_A_CONFIDENCE,
    GRADE_A_RISK_REWARD,
    GRADE_B_CONFIDENCE,
    GRADE_B_RISK_REWARD,
    GRADE_C_CONFIDENCE,
)


def prepare_trade_data(ticker):
    df = get_market_data(
        ticker,
        period="6mo",
        interval="1d",
    )

    if df.empty:
        return df

    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)

    return df.dropna()


def create_trade_plan(row):
    """
    Creates a practical trade plan that actually allows execution.
    """

    ticker = row["Ticker"]
    entry_price = float(row["Price ($)"])
    confidence = float(row["AI Confidence %"])
    trend_score = float(row["Trend Score"])
    signal = row["Signal"]

    try:
        df = prepare_trade_data(ticker)

        if df.empty:
            raise ValueError("No market data available")

        atr = ta.volatility.average_true_range(
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            window=14
        ).iloc[-1]

        atr = float(atr.item()) if hasattr(atr, "item") else float(atr)

        # --- TAKE-PROFIT TARGET: real recent price structure ---
        # Deliberately NOT derived from the same atr as the stop below --
        # if both stop and target scale off the same atr value, the
        # atr cancels out of risk_reward entirely and it collapses to a
        # constant (ATR_TAKE_PROFIT_MULTIPLIER / ATR_STOP_MULTIPLIER)
        # regardless of the ticker or signal. Using the actual recent
        # swing high/low means the target -- and therefore risk_reward --
        # genuinely depends on how much room this specific ticker has
        # left to run right now.
        lookback = min(TRADE_PLAN_LOOKBACK_DAYS, len(df))
        recent_high = float(df["High"].tail(lookback).max())
        recent_low = float(df["Low"].tail(lookback).min())

        # --- STOP LOSS / TAKE PROFIT ---
        if signal == "BUY":
            stop_loss = entry_price - (atr * ATR_STOP_MULTIPLIER)
            take_profit = recent_high
            # Signed, not abs(): if entry is already at/above the recent
            # high there's no real room left to run, and that should
            # produce a low or negative reward -- not get masked into a
            # falsely positive ratio.
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
                "Trade Reason": "No active signal"
            }

        # --- RISK / REWARD ---
        risk = abs(entry_price - stop_loss)
        risk_reward = reward / risk if risk > 0 else 0

        decision = signal if confidence >= GRADE_C_CONFIDENCE else "WAIT"

        # --- TRADE GRADE: combines confidence AND risk_reward ---
        # Both must clear a tier's bar -- a high-confidence signal with a
        # weak reward-to-risk isn't actually a great trade, and a great
        # reward-to-risk on a low-confidence signal isn't either. See
        # config.py for why this replaced a binary A/D that made
        # scoring_engine.py's A+/B point tiers permanently unreachable.
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

        # --- REASON ---
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
            "Trade Reason": reason
        }

    except Exception as e:
        return {
            "Trade Decision": "ERROR",
            "Stop Loss": None,
            "Take Profit": None,
            "Risk Reward": None,
            "Trade Grade": "ERROR",
            "Trade Reason": str(e)
        }