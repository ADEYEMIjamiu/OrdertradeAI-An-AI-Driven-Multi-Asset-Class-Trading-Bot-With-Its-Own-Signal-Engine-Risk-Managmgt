import yfinance as yf
import pandas as pd
import time

# =========================
# CONFIG
# =========================
TICKERS = ["SPY", "QQQ", "AAPL"]

INITIAL_CAPITAL = 10000
TRADE_SIZE = 0.2   # 20% per trade

STOP_LOSS = 0.03     # 3% loss
TAKE_PROFIT = 0.05   # 5% profit

SLEEP_TIME = 60

# =========================
# PORTFOLIO STATE
# =========================
cash = INITIAL_CAPITAL
positions = {}

# =========================
# SAFE FLOAT
# =========================
def safe_float(val):
    if hasattr(val, "iloc"):
        val = val.iloc[-1]
    if hasattr(val, "item"):
        val = val.item()
    return float(val)

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
# SIGNAL
# =========================
def generate_signal(row):
    returns = row["Returns"]

    if hasattr(returns, "iloc"):
        returns = returns.iloc[-1]

    returns = float(returns)

    # 🔥 BALANCED TRADING LOGIC

    if returns > 0.0005:
        return "BUY", 0.7

    elif returns < -0.001:
        return "SELL", 0.7

    # 🔥 BUY SMALL DIPS (CRITICAL FIX)
    elif returns > -0.002:
        return "BUY", 0.55

    else:
        return "HOLD", 0.5

# =========================
# RISK MANAGEMENT (NEW 🔥)
# =========================
def check_risk(ticker, price):
    global positions

    if ticker not in positions:
        return None

    entry_price = positions[ticker]["entry_price"]

    # STOP LOSS
    if price <= entry_price * (1 - STOP_LOSS):
        print(f"⚠️ STOP LOSS TRIGGERED for {ticker}")
        return "SELL"

    # TAKE PROFIT
    if price >= entry_price * (1 + TAKE_PROFIT):
        print(f"💰 TAKE PROFIT TRIGGERED for {ticker}")
        return "SELL"

    return None

# =========================
# EXECUTION ENGINE
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
# ENGINE
# =========================
def run_engine():
    results = []

    for ticker in TICKERS:
        print(f"\n📊 Processing {ticker}...")

        df = get_live_data(ticker)
        latest = df.iloc[-1]

        signal, confidence = generate_signal(latest)

        price = safe_float(df["Close"])

        # 🔥 RISK CHECK FIRST
        risk_action = check_risk(ticker, price)
        if risk_action:
            signal = risk_action

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
# MAIN LOOP
# =========================
def main():
    print("\n🚀 === V22 RISK-MANAGED TRADING ENGINE === 🚀")

    while True:
        print("\n================ NEW CYCLE ================\n")

        results = run_engine()

        print("\n📊 SIGNALS:")
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