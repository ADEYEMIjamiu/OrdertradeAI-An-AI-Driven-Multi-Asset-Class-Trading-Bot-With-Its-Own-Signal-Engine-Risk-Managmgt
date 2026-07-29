import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ================================
# V14: AI FILTERED STRATEGY
# ================================

INITIAL_CAPITAL = 10000

STOP_LOSS = 0.03
TAKE_PROFIT = 0.08
MAX_HOLD_DAYS = 5

# NEW AI FILTER RULES
MIN_CONFIDENCE = 0.55
MIN_VOLATILITY = 0.002  # avoid dead markets

# ================================
# LOAD DATA
# ================================

def load_data():
    df = pd.read_csv("research/v7_profit_backtest/v7_signals.csv")

    df.columns = df.columns.str.strip()

    # Normalize names
    df = df.rename(columns={
        "BUY_Probability": "confidence",
        "Execution_Close": "price",
        "Signal": "signal"
    })

    # Create volatility feature (AI filter)
    df["returns"] = df["price"].pct_change()
    df["volatility"] = df["returns"].rolling(10).std()

    return df.dropna()


# ================================
# AI FILTER (NEW CORE)
# ================================

def ai_trade_filter(row):
    """
    Decide whether to take the trade
    """

    # Rule 1: strong confidence
    if row["confidence"] < MIN_CONFIDENCE:
        return False

    # Rule 2: avoid low volatility markets
    if row["volatility"] < MIN_VOLATILITY:
        return False

    return True


# ================================
# RUN STRATEGY
# ================================

def run_strategy(df):

    capital = INITIAL_CAPITAL
    equity_curve = []
    drawdowns = []

    position = None
    peak = capital
    trades = []

    for i in range(len(df)):

        row = df.iloc[i]

        # =====================
        # ENTRY
        # =====================
        if position is None:

            if row["signal"] == "BUY" and ai_trade_filter(row):

                position = {
                    "entry_price": row["price"],
                    "entry_index": i
                }

        # =====================
        # EXIT
        # =====================
        else:
            entry_price = position["entry_price"]
            days_held = i - position["entry_index"]

            change = (row["price"] - entry_price) / entry_price

            if (
                change <= -STOP_LOSS or
                change >= TAKE_PROFIT or
                days_held >= MAX_HOLD_DAYS
            ):
                profit = change * capital * 0.1
                capital += profit

                trades.append(profit)
                position = None

        # =====================
        # TRACK EQUITY
        # =====================
        equity_curve.append(capital)

        # drawdown
        peak = max(peak, capital)
        dd = (capital - peak) / peak
        drawdowns.append(dd)

    return equity_curve, drawdowns, trades


# ================================
# METRICS
# ================================

def calculate_stats(equity, drawdowns, trades):

    returns = pd.Series(equity).pct_change().dropna()

    return {
        "final_capital": equity[-1],
        "total_return": (equity[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL,
        "max_drawdown": min(drawdowns),
        "sharpe": returns.mean() / returns.std() if returns.std() != 0 else 0,
        "trades": len(trades)
    }


# ================================
# MAIN
# ================================

def main():

    print("\n=== V14 AI FILTER STRATEGY ===")

    df = load_data()

    equity, drawdowns, trades = run_strategy(df)
    stats = calculate_stats(equity, drawdowns, trades)

    print("\n=== PERFORMANCE ===")
    for k, v in stats.items():
        print(f"{k}: {v:.4f}")

    # =====================
    # SAVE TRADE LOG
    # =====================
    print(f"\nTrades taken: {len(trades)}")

    # =====================
    # PLOTS
    # =====================
    plt.figure(figsize=(10, 5))
    plt.plot(equity)
    plt.title("V14 Equity Curve")
    plt.grid()
    plt.show()

    plt.figure(figsize=(10, 4))
    plt.plot(drawdowns, color="red")
    plt.title("V14 Drawdown")
    plt.grid()
    plt.show()


if __name__ == "__main__":
    main()