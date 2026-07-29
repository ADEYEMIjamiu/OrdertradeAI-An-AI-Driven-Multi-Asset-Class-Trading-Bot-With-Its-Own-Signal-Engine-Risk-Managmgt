import yfinance as yf
import pandas as pd
import time

# =========================
# CONFIG
# =========================
TICKERS = ["SPY", "QQQ", "AAPL"]

INITIAL_CAPITAL = 10000
TRADE_SIZE = 0.2

STOP_LOSS = 0.03
TAKE_PROFIT = 0.05

SLEEP_TIME = 60

# =========================
# STATE
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
# SIGNAL
# =========================
def generate_signal(row):
    returns = row["Returns"]

    if hasattr(returns, "iloc"):
        returns = returns.iloc[-1]

    returns = float(returns)

    # 🎯 ENTRY + EXIT LOGIC

    # Strong positive → BUY
    if returns > 0.0005:
        return "BUY", 0.7

    # Strong negative → SELL
    elif returns < -0.0005:
        return "SELL", 0.7

    # Slight negative → STILL BUY DIP
    elif returns > -0.002:
        return "BUY", 0.55

    # Otherwise → HOLD
    else:
        return "HOLD", 0.5

# =========================
# RISK MANAGEMENT (🔥 NEW)
# =========================
def check_risk(ticker, current_price):
    if ticker not in positions:
        return None

    entry_price = positions[ticker]["entry_price"]

    change = (current_price - entry_price) / entry_price

    if change <= -STOP_LOSS:
        return "STOP_LOSS"

    if change >= TAKE_PROFIT:
        return "TAKE_PROFIT"

    return None

# =========================
# EXECUTION
# =========================
def execute_trade(ticker, signal, price):
    global cash, positions

    # 🔥 FIRST: CHECK RISK EXIT
    risk_action = check_risk(ticker, price)

    if risk_action and ticker in positions:
        shares = positions[ticker]["shares"]
        entry_price = positions[ticker]["entry_price"]

        proceeds = shares * price
        profit = proceeds - (shares * entry_price)

        cash += proceeds
        del positions[ticker]

        print(f"⚠️ {risk_action} EXIT {ticker} | Price: {price:.2f} | P/L: {profit:.2f}")
        return

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
    print("\n🚀 === V21 RISK-MANAGED TRADING ENGINE === 🚀\n")

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