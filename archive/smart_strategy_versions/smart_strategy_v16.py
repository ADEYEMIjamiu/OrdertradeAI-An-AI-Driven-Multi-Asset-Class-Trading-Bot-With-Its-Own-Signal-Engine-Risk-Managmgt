import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ================================
# V16: MULTI-ASSET AI TRADING
# ================================

INITIAL_CAPITAL = 10000

STOP_LOSS = 0.03
TAKE_PROFIT = 0.08
MAX_HOLD_DAYS = 5

MIN_CONFIDENCE = 0.55
MIN_VOLATILITY = 0.002

# 🔥 MULTI-ASSET FILES (ADD MORE LATER)
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
        "BUY_Probability": "confidence",
        "Execution_Close": "price",
        "Signal": "signal"
    })

    df["returns"] = df["price"].pct_change()
    df["volatility"] = df["returns"].rolling(10).std()

    return df.dropna()


# ================================
# FILTER
# ================================

def ai_trade_filter(row):
    return (
        row["confidence"] >= MIN_CONFIDENCE and
        row["volatility"] >= MIN_VOLATILITY
    )


# ================================
# POSITION SIZING
# ================================

def get_position_size(row):
    confidence = row["confidence"]
    volatility = row["volatility"]

    vol_factor = min(max(volatility * 100, 0.5), 2)

    if confidence > 0.7:
        size = 0.25
    elif confidence > 0.6:
        size = 0.15
    else:
        size = 0.07

    size = size / vol_factor
    size = max(0.05, min(size, 0.3))

    return size


# ================================
# RUN STRATEGY PER ASSET
# ================================

def run_strategy(df):

    capital = INITIAL_CAPITAL
    equity = []
    drawdowns = []

    position = None
    peak = capital
    trades = []

    for i in range(len(df)):
        row = df.iloc[i]

        if position is None:
            if row["signal"] == "BUY" and ai_trade_filter(row):

                position = {
                    "entry_price": row["price"],
                    "entry_index": i,
                    "size": get_position_size(row)
                }

        else:
            entry_price = position["entry_price"]
            size = position["size"]
            days_held = i - position["entry_index"]

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

        equity.append(capital)

        peak = max(peak, capital)
        drawdowns.append((capital - peak) / peak)

    return equity, drawdowns, trades


# ================================
# 🔥 MULTI-ASSET ENGINE
# ================================

def run_portfolio():

    portfolio_equity = None
    all_trades = []

    for name, path in ASSETS.items():

        print(f"\nRunning {name}...")

        df = load_data(path)

        equity, drawdowns, trades = run_strategy(df)

        all_trades.extend(trades)

        # Combine equity curves
        if portfolio_equity is None:
            portfolio_equity = np.array(equity)
        else:
            min_len = min(len(portfolio_equity), len(equity))
            portfolio_equity = portfolio_equity[:min_len] + np.array(equity[:min_len]) - INITIAL_CAPITAL

    return portfolio_equity, all_trades


# ================================
# METRICS
# ================================

def calculate_stats(equity, trades):

    returns = pd.Series(equity).pct_change().dropna()

    return {
        "final_capital": equity[-1],
        "total_return": (equity[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL,
        "sharpe": returns.mean() / returns.std() if returns.std() != 0 else 0,
        "trades": len(trades)
    }


# ================================
# MAIN
# ================================

def main():

    print("\n=== V16 MULTI-ASSET AI ENGINE ===")

    equity, trades = run_portfolio()

    stats = calculate_stats(equity, trades)

    print("\n=== PORTFOLIO PERFORMANCE ===")
    for k, v in stats.items():
        print(f"{k}: {v:.4f}")

    print(f"\nTotal trades: {len(trades)}")

    # =====================
    # PLOT
    # =====================
    plt.figure(figsize=(10, 5))
    plt.plot(equity)
    plt.title("V16 Portfolio Equity Curve")
    plt.grid()
    plt.show()


if __name__ == "__main__":
    main()