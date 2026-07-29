import pandas as pd
import numpy as np
import yfinance as yf

# ================================
# V18: LIVE DATA AI ENGINE
# ================================

INITIAL_CAPITAL = 10000

STOP_LOSS = 0.03
TAKE_PROFIT = 0.08
MAX_HOLD_DAYS = 5

MIN_CONFIDENCE = 0.55

TICKERS = ["SPY", "AAPL", "QQQ"]

# ================================
# FETCH LIVE DATA
# ================================

def get_live_data(ticker):
    df = yf.download(ticker, period="3mo", interval="1d")

    df["Returns"] = df["Close"].pct_change()
    df["Volatility"] = df["Returns"].rolling(20).std()

    df = df.dropna()

    return df


# ================================
# SIMPLE AI SIGNAL (TEMP MODEL)
# ================================

def generate_signal(row):

    returns = row["Returns"]
    volatility = row["Volatility"]

    # Force scalar values (critical fix)
    if hasattr(returns, "item"):
        returns = returns.item()

    if hasattr(volatility, "item"):
        volatility = volatility.item()

    if returns > 0 and volatility > 0.01:
        return "BUY", 0.6
    elif returns < 0:
        return "SELL", 0.6
    else:
        return "HOLD", 0.5


# ================================
# RUN LIVE STRATEGY
# ================================

def run_live_engine():

    results = []

    for ticker in TICKERS:
        print(f"\nProcessing {ticker}...")

        df = get_live_data(ticker)

        latest = df.iloc[-1]

        signal, confidence = generate_signal(latest)

    price = df["Close"]

# Force clean extraction
    if hasattr(price, "iloc"):
        price = price.iloc[-1]

    if hasattr(price, "item"):
        price = price.item()

    price = float(price)

    results.append({
    "ticker": ticker,
    "price": price,
    "signal": signal,
    "confidence": confidence
})

    return results


# ================================
# MAIN
# ================================

def main():

    print("\n=== V18 LIVE AI SIGNAL ENGINE ===")

    results = run_live_engine()

    print("\n=== LIVE SIGNALS ===")

    for r in results:
        print(
        f"{r['ticker']} | Price: {float(r['price']):.2f} | "
        f"Signal: {r['signal']} | Confidence: {r['confidence']}"
    )



if __name__ == "__main__":
    main()