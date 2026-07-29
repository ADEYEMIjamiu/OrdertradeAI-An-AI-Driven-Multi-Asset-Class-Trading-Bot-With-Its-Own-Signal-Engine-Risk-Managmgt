import pandas as pd
import numpy as np

# ================================
# V10: ROBUSTNESS TEST (FINAL)
# ================================

INITIAL_CAPITAL = 10000

# Fixed V9 policy (NO OPTIMISATION)
STOP_LOSS = 0.03
TAKE_PROFIT = 0.08
MAX_HOLD_DAYS = 5
MIN_BUY_CONF = 0.50


# ================================
# LOAD DATA (SAFE VERSION)
# ================================
def load_v7_data():
    path = "research/v7_profit_backtest/v7_signals.csv"
    df = pd.read_csv(path)

    # Standardise column names (VERY IMPORTANT)
    df.columns = df.columns.str.strip()

    # Rename to safe internal names
    rename_map = {
        "BUY_Probability": "confidence",
        "Execution_Close": "price",
        "Signal": "signal"
    }

    df = df.rename(columns=rename_map)

    # Validate required columns
    required = ["signal", "confidence", "price"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    return df


# ================================
# SPLIT INTO WINDOWS
# ================================
def split_windows(df, window_size=250):
    return [
        df.iloc[i:i + window_size]
        for i in range(0, len(df) - window_size, window_size)
    ]


# ================================
# STRATEGY ENGINE
# ================================
def run_strategy(df):
    capital = INITIAL_CAPITAL
    position = None
    trades = []

    for i in range(len(df)):
        row = df.iloc[i]

        # ENTRY
        if position is None:
            if row["signal"] == "BUY" and row["confidence"] >= MIN_BUY_CONF:
                position = {
                    "entry_price": row["price"],
                    "entry_index": i
                }

        # EXIT
        else:
            entry_price = position["entry_price"]
            days_held = i - position["entry_index"]

            change = (row["price"] - entry_price) / entry_price

            if (
                change <= -STOP_LOSS or
                change >= TAKE_PROFIT or
                days_held >= MAX_HOLD_DAYS
            ):
                profit = change * capital * 0.1  # 10% position size
                capital += profit
                trades.append(profit)
                position = None

    return capital, trades


# ================================
# METRICS
# ================================
def calculate_metrics(capital, trades):
    if len(trades) == 0:
        return None

    trades = np.array(trades)

    win_rate = np.mean(trades > 0)

    profit_factor = (
        trades[trades > 0].sum() /
        abs(trades[trades < 0].sum())
        if np.any(trades < 0) else np.inf
    )

    expectancy = trades.mean()

    return {
        "ending_capital": capital,
        "net_profit": capital - INITIAL_CAPITAL,
        "trades": len(trades),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy": expectancy
    }


# ================================
# MAIN EXECUTION
# ================================
def main():

    print("\n=== V10 ROBUSTNESS TEST ===\n")

    df = load_v7_data()
    print("Columns detected:", df.columns.tolist())

    windows = split_windows(df)

    if len(windows) == 0:
        print("Not enough data for window testing.")
        return

    results = []

    for i, window in enumerate(windows):

        capital, trades = run_strategy(window)
        metrics = calculate_metrics(capital, trades)

        if metrics:
            results.append(metrics)

            print(f"\nWindow {i+1}:")
            print(metrics)

    if len(results) == 0:
        print("\nNo valid trading windows.")
        return

    # ==========================
    # STABILITY ANALYSIS
    # ==========================
    profits = [r["net_profit"] for r in results]
    pf = [r["profit_factor"] for r in results]
    exp = [r["expectancy"] for r in results]

    print("\n=== STABILITY SUMMARY ===")

    print(f"Windows tested: {len(results)}")
    print(f"Avg Net Profit: {np.mean(profits):.2f}")
    print(f"Std Net Profit: {np.std(profits):.2f}")

    print(f"Avg Profit Factor: {np.mean(pf):.2f}")
    print(f"Avg Expectancy: {np.mean(exp):.2f}")

    # ==========================
    # ROBUSTNESS GATE
    # ==========================
    passed = (
        np.mean(profits) > 0 and
        np.mean(pf) >= 1.1 and
        np.mean(exp) > 0
    )

    print("\n=== V10 RESULT ===")

    if passed:
        print("PASSED ✅ ROBUST STRATEGY")
    else:
        print("FAILED ❌ NOT ROBUST")


if __name__ == "__main__":
    main()