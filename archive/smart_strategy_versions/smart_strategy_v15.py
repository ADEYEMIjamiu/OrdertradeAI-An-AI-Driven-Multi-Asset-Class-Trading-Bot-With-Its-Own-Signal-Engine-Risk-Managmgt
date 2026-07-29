import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ================================
# V15: AI POSITION SIZING
# ================================

INITIAL_CAPITAL = 10000

STOP_LOSS = 0.03
TAKE_PROFIT = 0.08
MAX_HOLD_DAYS = 5

MIN_CONFIDENCE = 0.55
MIN_VOLATILITY = 0.002

# ================================
# LOAD DATA
# ================================

def load_data():
    df = pd.read_csv("research/v7_profit_backtest/v7_signals.csv")

    df.columns = df.columns.str.strip()

    df = df.rename(columns={
        "BUY_Probability": "confidence",
        "Execution_Close": "price",
        "Signal": "signal"
    })

    df["returns"] = df["price"].pct_change()
    df["volatility"] = df["returns"].rolling(10).std()

    return df.dropna()


# ================================
# AI FILTER
# ================================

def ai_trade_filter(row):

    if row["confidence"] < MIN_CONFIDENCE:
        return False

    if row["volatility"] < MIN_VOLATILITY:
        return False

    return True


# ================================
# 🔥 NEW: POSITION SIZING AI
# ================================

def get_position_size(row):
    """
    Decide how much capital to risk
    """

    confidence = row["confidence"]
    volatility = row["volatility"]

    # Normalize volatility (avoid division issues)
    vol_factor = min(max(volatility * 100, 0.5), 2)

    # Confidence scaling
    if confidence > 0.7:
        size = 0.25   # 25% of capital
    elif confidence > 0.6:
        size = 0.15   # 15%
    else:
        size = 0.07   # 7%

    # Adjust by volatility (high vol = reduce size)
    size = size / vol_factor

    # Clamp final size
    size = max(0.05, min(size, 0.3))

    return size


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

                size = get_position_size(row)

                position = {
                    "entry_price": row["price"],
                    "entry_index": i,
                    "size": size
                }

        # =====================
        # EXIT
        # =====================
        else:
            entry_price = position["entry_price"]
            days_held = i - position["entry_index"]
            size = position["size"]

            change = (row["price"] - entry_price) / entry_price

            if (
                change <= -STOP_LOSS or
                change >= TAKE_PROFIT or
                days_held >= MAX_HOLD_DAYS
            ):
                profit = change * capital * size
                capital += profit

                trades.append(profit)
                position = None

        # =====================
        # TRACK EQUITY
        # =====================
        equity_curve.append(capital)

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

    print("\n=== V15 AI POSITION SIZING ===")

    df = load_data()

    equity, drawdowns, trades = run_strategy(df)
    stats = calculate_stats(equity, drawdowns, trades)

    print("\n=== PERFORMANCE ===")
    for k, v in stats.items():
        print(f"{k}: {v:.4f}")

    print(f"\nTrades taken: {len(trades)}")

    # =====================
    # PLOTS
    # =====================
    plt.figure(figsize=(10, 5))
    plt.plot(equity)
    plt.title("V15 Equity Curve")
    plt.grid()
    plt.show()

    plt.figure(figsize=(10, 4))
    plt.plot(drawdowns, color="red")
    plt.title("V15 Drawdown")
    plt.grid()
    plt.show()


if __name__ == "__main__":
    main()