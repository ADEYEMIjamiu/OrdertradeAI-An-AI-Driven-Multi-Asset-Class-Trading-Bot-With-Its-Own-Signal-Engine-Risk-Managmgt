import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ================================
# V12: SMART STRATEGY ENGINE
# ================================

INITIAL_CAPITAL = 10000

STOP_LOSS = 0.03
TAKE_PROFIT = 0.08
MAX_HOLD_DAYS = 5


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

    return df


# ================================
# POSITION SIZING (NEW)
# ================================
def get_position_size(conf):

    if conf < 0.55:
        return 0.05
    elif conf < 0.60:
        return 0.10
    elif conf < 0.70:
        return 0.15
    else:
        return 0.20


# ================================
# STRATEGY
# ================================
def run_strategy(df):

    capital = INITIAL_CAPITAL
    equity_curve = []

    position = None
    peak = capital
    drawdowns = []

    for i in range(len(df)):
        row = df.iloc[i]

        # ENTRY
        if position is None:
            if row["signal"] == "BUY" and row["confidence"] >= 0.55:

                size = get_position_size(row["confidence"])

                position = {
                    "entry_price": row["price"],
                    "entry_index": i,
                    "size": size
                }

        # EXIT
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
                position = None

        # TRACK EQUITY
        equity_curve.append(capital)

        # DRAWNDOWN
        if capital > peak:
            peak = capital

        dd = (capital - peak) / peak
        drawdowns.append(dd)

    return equity_curve, drawdowns


# ================================
# METRICS
# ================================
def calculate_stats(equity, drawdowns):

    equity = np.array(equity)

    total_return = (equity[-1] - equity[0]) / equity[0]
    max_drawdown = np.min(drawdowns)

    returns = np.diff(equity) / equity[:-1]
    sharpe = np.mean(returns) / np.std(returns) if np.std(returns) != 0 else 0

    return {
        "Final Capital": equity[-1],
        "Total Return": total_return,
        "Max Drawdown": max_drawdown,
        "Sharpe": sharpe
    }


# ================================
# MAIN
# ================================
def main():

    print("\n=== V12 SMART STRATEGY TEST ===\n")

    df = load_data()

    equity, drawdowns = run_strategy(df)

    stats = calculate_stats(equity, drawdowns)

    print("\n=== PERFORMANCE ===")
    for k, v in stats.items():
        print(f"{k}: {v:.4f}")

    # EQUITY
    plt.figure(figsize=(10, 5))
    plt.plot(equity)
    plt.title("V12 Equity Curve")
    plt.grid()
    plt.show()

    # DRAWDOWN
    plt.figure(figsize=(10, 4))
    plt.plot(drawdowns, color='red')
    plt.title("V12 Drawdown")
    plt.grid()
    plt.show()


if __name__ == "__main__":
    main()