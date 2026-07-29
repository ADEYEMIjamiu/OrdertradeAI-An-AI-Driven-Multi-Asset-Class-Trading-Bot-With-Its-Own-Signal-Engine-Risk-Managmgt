import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ================================
# V17: ADAPTIVE AI STRATEGY
# ================================

INITIAL_CAPITAL = 10000

STOP_LOSS = 0.03
TAKE_PROFIT = 0.08
MAX_HOLD_DAYS = 5

# NEW INTELLIGENCE
MIN_CONFIDENCE = 0.50
MIN_VOLATILITY = 0.001

ASSETS = {
    "SPY": "research/v7_profit_backtest/v7_signals.csv",
    "QQQ": "research/v7_profit_backtest/v7_signals_2.csv",
    "AAPL": "research/v7_profit_backtest/v7_signals_3.csv",
}

# ================================
# LOAD DATA
# ================================

def load_data(path):
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()

    df = df.rename(columns={
        "Execution_Close": "price",
        "BUY_Probability": "confidence",
        "Signal": "signal"
    })

    return df


# ================================
# POSITION SIZE LOGIC
# ================================

def get_position_size(confidence):
    if confidence >= 0.75:
        return 0.20
    elif confidence >= 0.65:
        return 0.15
    elif confidence >= 0.55:
        return 0.10
    else:
        return 0.05


# ================================
# STRATEGY ENGINE
# ================================

def run_strategy(df, capital):

    position = None
    equity_curve = []
    trades = []

    df["returns"] = df["price"].pct_change()
    df["volatility"] = df["returns"].rolling(20).std()

    for i in range(len(df)):

        row = df.iloc[i]

        equity_curve.append(capital)

        if np.isnan(row["volatility"]):
            continue

        if position is None:

            if (
                row["signal"] == "BUY"
                and row["confidence"] >= MIN_CONFIDENCE
                and row["volatility"] >= MIN_VOLATILITY
            ):
                size = get_position_size(row["confidence"])

                position = {
                    "entry_price": row["price"],
                    "entry_index": i,
                    "size": size
                }

        else:
            entry_price = position["entry_price"]
            days_held = i - position["entry_index"]

            change = (row["price"] - entry_price) / entry_price

            if (
                change <= -STOP_LOSS
                or change >= TAKE_PROFIT
                or days_held >= MAX_HOLD_DAYS
            ):
                profit = change * capital * position["size"]
                capital += profit

                trades.append(profit)
                position = None

    return capital, equity_curve, trades


# ================================
# PORTFOLIO ENGINE
# ================================

def run_portfolio():

    capital = INITIAL_CAPITAL
    all_equity = []
    all_trades = []

    for name, path in ASSETS.items():

        print(f"Running {name}...")

        df = load_data(path)

        capital, equity, trades = run_strategy(df, capital)

        all_equity.extend(equity)
        all_trades.extend(trades)

    return all_equity, all_trades


# ================================
# METRICS
# ================================

def calculate_stats(equity, trades):

    equity = np.array(equity)
    returns = np.diff(equity) / equity[:-1]

    sharpe = np.mean(returns) / np.std(returns) if np.std(returns) > 0 else 0

    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    max_dd = np.min(drawdown)

    return {
        "final_capital": equity[-1],
        "total_return": (equity[-1] - equity[0]) / equity[0],
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "trades": len(trades)
    }, drawdown


# ================================
# MAIN
# ================================

def main():

    print("\n=== V17 ADAPTIVE AI ENGINE ===\n")

    equity, trades = run_portfolio()

    stats, drawdown = calculate_stats(equity, trades)

    print("\n=== PERFORMANCE ===")
    for k, v in stats.items():
        print(f"{k}: {v:.4f}")

    print(f"\nTotal trades: {len(trades)}")

    # EQUITY
    plt.figure(figsize=(10, 5))
    plt.plot(equity)
    plt.title("V17 Equity Curve")
    plt.grid()
    plt.show()

    # DRAWDOWN
    plt.figure(figsize=(10, 4))
    plt.plot(drawdown, color="red")
    plt.title("V17 Drawdown")
    plt.grid()
    plt.show()


if __name__ == "__main__":
    main()