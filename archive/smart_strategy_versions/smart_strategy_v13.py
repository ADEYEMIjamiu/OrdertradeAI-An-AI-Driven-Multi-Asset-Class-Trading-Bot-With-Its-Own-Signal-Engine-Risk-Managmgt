import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ================================
# V13: REAL TRADING ENGINE
# ================================

INITIAL_CAPITAL = 10000

STOP_LOSS = 0.03
TAKE_PROFIT = 0.08
MAX_HOLD_DAYS = 5

BASE_CONF = 0.50
MIN_TRADES = 20

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
# RUN STRATEGY
# ================================

def run_strategy(df):

    capital = INITIAL_CAPITAL
    equity_curve = []
    drawdowns = []

    position = None
    peak = INITIAL_CAPITAL

    trades = []
    trade_log = []

    for i in range(len(df)):

        row = df.iloc[i]

        confidence = row["confidence"]

        # DYNAMIC POSITION SIZE
        position_size = min(0.3, 0.05 + confidence)

        # ================= ENTRY =================
        if position is None:

            if row["signal"] == "BUY" and confidence >= BASE_CONF:

                position = {
                    "entry_price": row["price"],
                    "entry_index": i,
                    "confidence": confidence,
                    "size": position_size
                }

        # ================= EXIT =================
        else:

            entry_price = position["entry_price"]
            days_held = i - position["entry_index"]

            change = (row["price"] - entry_price) / entry_price

            if (
                change <= -STOP_LOSS or
                change >= TAKE_PROFIT or
                days_held >= MAX_HOLD_DAYS
            ):

                profit = change * capital * position["size"]
                capital += profit

                trades.append(profit)

                # LOG TRADE
                trade_log.append({
                    "entry_price": entry_price,
                    "exit_price": row["price"],
                    "profit": profit,
                    "days_held": days_held,
                    "confidence": position["confidence"]
                })

                position = None

        # ================= EQUITY =================
        equity_curve.append(capital)

        peak = max(peak, capital)
        dd = (capital - peak) / peak
        drawdowns.append(dd)

    return equity_curve, drawdowns, trades, trade_log


# ================================
# STATS
# ================================

def calculate_stats(equity, drawdowns, trades):

    total_return = (equity[-1] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    max_dd = min(drawdowns)

    returns = np.diff(equity)
    sharpe = np.mean(returns) / (np.std(returns) + 1e-9)

    trade_count = len(trades)

    return {
        "final_capital": equity[-1],
        "total_return": total_return,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "trades": trade_count
    }


# ================================
# MAIN
# ================================

def main():

    print("\n=== V13 REAL TRADING ENGINE ===\n")

    df = load_data()

    equity, drawdowns, trades, trade_log = run_strategy(df)

    stats = calculate_stats(equity, drawdowns, trades)

    print("=== PERFORMANCE ===")
    for k, v in stats.items():
        print(f"{k}: {v:.4f}")

    # ==========================
    # TRADE QUALITY CHECK
    # ==========================

    if stats["trades"] < MIN_TRADES:
        print("\n⚠️ WARNING: Too few trades — system not reliable")

    # ==========================
    # SAVE TRADE LOG
    # ==========================

    trade_df = pd.DataFrame(trade_log)
    trade_df.to_csv("research/v13_trade_log.csv", index=False)

    print("\nTrade log saved → research/v13_trade_log.csv")

    # ==========================
    # PLOTS
    # ==========================

    plt.figure(figsize=(10, 5))
    plt.plot(equity)
    plt.title("V13 Equity Curve")
    plt.grid()
    plt.show()

    plt.figure(figsize=(10, 4))
    plt.plot(drawdowns, color="red")
    plt.title("V13 Drawdown")
    plt.grid()
    plt.show()


if __name__ == "__main__":
    main()