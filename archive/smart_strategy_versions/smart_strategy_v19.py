import yfinance as yf
import pandas as pd

# =========================
# CONFIG
# =========================
TICKERS = ["SPY", "QQQ", "AAPL"]

INITIAL_CAPITAL = 10000
TRADE_SIZE = 0.2   # 20% per trade

# =========================
# PORTFOLIO STATE
# =========================
cash = INITIAL_CAPITAL
positions = {}  # {ticker: {"shares": x, "entry_price": y}}

# =========================
# UTIL (CRITICAL FIX)
# =========================
def safe_float(value):
    """
    Converts pandas Series / numpy types safely to float
    """
    if hasattr(value, "iloc"):
        value = value.iloc[-1]

    if hasattr(value, "item"):
        value = value.item()

    return float(value)

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

    returns = safe_float(row["Returns"])

    # 🔥 FORCE TRADING (for testing engine)
    if returns > -0.001:
        return "BUY", 0.6
    else:
        return "SELL", 0.6

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

            print(f"BUY {ticker} | Price: {price:.2f} | Shares: {shares:.2f}")

    # SELL
    elif signal == "SELL" and ticker in positions:
        shares = positions[ticker]["shares"]
        entry_price = positions[ticker]["entry_price"]

        proceeds = shares * price
        profit = proceeds - (shares * entry_price)

        cash += proceeds
        del positions[ticker]

        print(f"SELL {ticker} | Price: {price:.2f} | Profit: {profit:.2f}")

# =========================
# ENGINE
# =========================
def run_engine():
    results = []

    for ticker in TICKERS:
        print(f"\nProcessing {ticker}...")

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
# PORTFOLIO VALUE (FIXED)
# =========================
def portfolio_value():
    total = cash

    for ticker, pos in positions.items():

        # 🔥 GET FRESH LIVE PRICE (NOT OLD DATA)
        df = yf.download(ticker, period="1d", interval="1m")

        price = df["Close"]

        if hasattr(price, "iloc"):
            price = price.iloc[-1]

        if hasattr(price, "item"):
            price = price.item()

        price = float(price)

        total += pos["shares"] * price

    return total

# =========================
# MAIN
# =========================
def main():
    print("\n=== V19 PAPER TRADING ENGINE ===")

    results = run_engine()

    print("\n=== SIGNALS ===")
    for r in results:
        print(f"{r['ticker']} | {r['signal']} | Price: {r['price']:.2f}")

    print("\n=== PORTFOLIO ===")
    print(f"Cash: {cash:.2f}")
    print(f"Positions: {positions}")

    total = portfolio_value()
    print(f"\nTotal Portfolio Value: {total:.2f}")

# =========================
if __name__ == "__main__":
    main()