import yfinance as yf
import pandas as pd
import time

# =========================
# CONFIG
# =========================
TICKERS = ["SPY", "QQQ", "AAPL"]

INITIAL_CAPITAL = 10000
TRADE_SIZE = 0.2   # 20% per trade
SLEEP_TIME = 60    # seconds between cycles

# =========================
# PORTFOLIO STATE
# =========================
cash = INITIAL_CAPITAL
positions = {}

# =========================
# SAFE FLOAT
# =========================
def safe_float(x):
    if hasattr(x, "iloc"):
        x = x.iloc[-1]
    if hasattr(x, "item"):
        x = x.item()
    return float(x)

# =========================
# DATA
# =========================
def get_live_data(ticker):
    df = yf.download(ticker, period="5d", interval="1h")
    df["Returns"] = df["Close"].pct_change()
    df["Volatility"] = df["Returns"].rolling(5).std()
    df = df.dropna()
    return df

# =========================
# SIGNAL (FOR TESTING — ALWAYS TRADES)
# =========================
def generate_signal(row):
    returns = safe_float(row["Returns"])

    # 🔥 Force activity so bot actually trades
    if returns > -0.001:
        return "BUY", 0.6
    else:
        return "SELL", 0.6

# =========================
# EXECUTION
# =========================
def execute_trade(ticker, signal, price):
    global cash, positions

    # BUY
    if signal == "BUY" and ticker not in positions:
        allocation = cash * TRADE_SIZE
        shares = allocation / price

        if shares > 0:
            positions[ticker] = {
                "shares": shares,
                "entry_price": price
            }
            cash -= shares * price

            print(f"✅ BUY {ticker} | Price: {price:.2f} | Shares: {shares:.2f}")

    # SELL
    elif signal == "SELL" and ticker in positions:
        shares = positions[ticker]["shares"]
        entry_price = positions[ticker]["entry_price"]

        proceeds = shares * price
        profit = proceeds - (shares * entry_price)

        cash += proceeds
        del positions[ticker]

        print(f"❌ SELL {ticker} | Price: {price:.2f} | Profit: {profit:.2f}")

# =========================
# ENGINE (ONE CYCLE)
# =========================
def run_engine():
    results = []

    for ticker in TICKERS:
        print(f"\n📊 Processing {ticker}...")

        df = get_live_data(ticker)
        latest = df.iloc[-1]

        signal, confidence = generate_signal(latest)

        price = safe_float(df["Close"])

        execute_trade(ticker, signal, price)

        results.append({
            "ticker": ticker,
            "price": price,
            "signal": signal,
            "confidence": confidence
        })

    return results

# =========================
# PORTFOLIO VALUE
# =========================
def portfolio_value():
    total = cash

    for ticker, pos in positions.items():
        df = yf.download(ticker, period="1d", interval="1m")
        price = safe_float(df["Close"])

        total += pos["shares"] * price

    return total

# =========================
# MAIN LOOP (🔥 V20 CORE)
# =========================
def main():
    print("\n🚀 === V20 AUTO TRADING ENGINE STARTED === 🚀\n")

    while True:
        print("\n================ NEW CYCLE ================\n")

        results = run_engine()

        print("\n📢 SIGNALS:")
        for r in results:
            print(f"{r['ticker']} | {r['signal']} | Price: {r['price']:.2f}")

        print("\n💼 PORTFOLIO:")
        print(f"Cash: {cash:.2f}")
        print(f"Positions: {positions}")

        total = portfolio_value()
        print(f"\n💰 Total Portfolio Value: {total:.2f}")

        print(f"\n⏳ Sleeping for {SLEEP_TIME} seconds...\n")
        time.sleep(SLEEP_TIME)

# =========================
if __name__ == "__main__":
    main()