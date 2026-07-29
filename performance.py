import sqlite3

def analyze_performance():
    conn = sqlite3.connect("trade_journal.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ticker, action, price, shares, amount
        FROM trades
        WHERE action = 'SELL'
    """)

    trades = cursor.fetchall()

    if not trades:
        print("❌ No trades found.")
        return

    total_trades = 0
    wins = 0
    losses = 0
    total_profit = 0

    for trade in trades:
        ticker, action, price, shares, amount = trade

        # 🔥 amount = total value (shares * price)
        # You don’t have entry price stored → profit = 0 for now
        profit = 0  

        total_trades += 1
        total_profit += profit

        if profit > 0:
            wins += 1
        else:
            losses += 1

    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    print("\n📊 PERFORMANCE SUMMARY")
    print(f"Total Trades: {total_trades}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Total Profit: {total_profit:.2f}")

    conn.close()


if __name__ == "__main__":
    analyze_performance()