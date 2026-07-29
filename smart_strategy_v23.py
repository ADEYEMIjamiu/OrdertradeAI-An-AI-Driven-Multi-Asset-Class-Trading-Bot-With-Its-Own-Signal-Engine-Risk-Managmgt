import yfinance as yf
import pandas as pd
import time

from trade_journal import log_trade, init_trade_journal
from config import TAKE_PROFIT, STOP_LOSS

from paper_trading.paper_engine import PaperTradingEngine



# =========================
# CONFIG
# =========================
TICKERS = ["SPY", "QQQ", "AAPL"]

INITIAL_CAPITAL = 10000
TRADE_SIZE = 0.2

SLEEP_TIME = 30

# =========================
# STATE
# =========================
cash = INITIAL_CAPITAL
positions = {}
trade_history = []
recently_sold = set()

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
    try:
        df = yf.download(ticker, period="60d", interval="1h")

        if df is None or df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df["Returns"] = df["Close"].pct_change()
        df["SMA20"] = df["Close"].rolling(20).mean()
        df["SMA50"] = df["Close"].rolling(50).mean()

        delta = df["Close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))

        df = df.dropna()

        return df

    except Exception as e:
        print(f"❌ Data error: {e}")
        return None

# =========================
# SIGNAL
# =========================
def generate_signal(row):

    price = float(row["Close"])
    sma20 = float(row["SMA20"])

    # 🔥 SMALLER BUFFER (MORE ACTIVE)
    if price > sma20:
        return "BUY", 0.8

    elif price < sma20:
        return "SELL", 0.8

    else:
        return "HOLD", 0.5

# =========================
# RISK MANAGEMENT
# =========================
def check_risk(ticker, price):
    if ticker not in positions:
        return None

    entry = positions[ticker]["entry_price"]

    if price <= entry * (1 - STOP_LOSS):
        print(f"⚠️ STOP LOSS TRIGGERED for {ticker}")
        return "SELL"

    if price >= entry * (1 + TAKE_PROFIT):
        print(f"💰 TAKE PROFIT TRIGGERED for {ticker}")
        return "SELL"

    return None

# =========================
# EXIT CONDITIONS
# =========================
def check_exit_conditions(ticker, current_price):

    if ticker not in positions:
        return None

    entry_price = positions[ticker]["entry_price"]
    change_pct = (current_price - entry_price) / entry_price

    print(f"DEBUG {ticker} | entry: {entry_price:.2f} | current: {current_price:.2f} | change: {change_pct:.5f}")

    if abs(change_pct) < 0.002:
        return None

    if change_pct >= TAKE_PROFIT:
        return "SELL"

    if change_pct <= -STOP_LOSS:
        return "SELL"

    return None

# =========================
# EXECUTION
# =========================
def execute_trade(ticker, signal, price):

    from streamlit import session_state as ss

    print(f"🚀 EXECUTE TRADE CALLED → {ticker} | {signal}")

    # 🔐 RISK CHECK FIRST
    risk_signal = ss.paper_engine.check_risk(ticker, price)

    if risk_signal == "SELL":
        print(f"⚠️ AUTO SELL (RISK MANAGEMENT) → {ticker}")

        ss.paper_engine.sell(
            ticker=ticker,
            price=price
        )
        return

    # =========================
    # BUY
    # =========================
    if signal == "BUY":

        if len(ss.paper_engine.positions) >= 3:
            print(f"⛔ MAX POSITIONS REACHED — skipping BUY → {ticker}")
            return  # ✅ ONLY return here

        print(f"📥 Routing BUY → Paper Engine: {ticker}")

        ss.paper_engine.buy(
            ticker=ticker,
            price=price,
            allocation=TRADE_SIZE
        )
        return

    # =========================
    # SELL
    # =========================
    if signal == "SELL":

        print(f"📤 Routing SELL → Paper Engine: {ticker}")

        ss.paper_engine.sell(
            ticker=ticker,
            price=price
        )
        return
        
# =========================
# ENGINE
# =========================
def run_engine():
    from streamlit import session_state as ss
    from engines.approval_engine import approve_trade  # ✅ NEW

    recently_sold.clear()

    for ticker in TICKERS:

        print(f"\n📊 Processing {ticker}...")

        df = get_live_data(ticker)

        if df is None or len(df) < 2:
            print(f"⚠️ No data for {ticker}, skipping...")
            continue

        latest = df.iloc[-1]

        # ==============================
        # ✅ SIGNAL
        # ==============================
        signal, confidence = generate_signal(latest)
        signal = str(signal).strip().upper()

        # ==============================
        # ✅ PRICE
        # ==============================
        price = safe_float(latest["Close"])

        # ==============================
        # 🔍 DEBUG
        # ==============================
        print(f"📊 ACTIVE POSITIONS: {ss.paper_engine.positions}")

        # ==============================
        # 🔥 GLOBAL RISK CHECK
        # ==============================
        for pos_ticker, position in ss.paper_engine.positions.items():

            if pos_ticker != ticker:
                continue

            risk_signal = ss.paper_engine.check_risk(pos_ticker, price)

            if risk_signal == "SELL":
                print(f"⚠️ AUTO SELL (GLOBAL RISK) → {pos_ticker}")

                ss.paper_engine.sell(
                    ticker=pos_ticker,
                    price=price
                )

                break

        # ==============================
        # ✅ SMART FILTER
        # ==============================
        if ticker not in ss.paper_engine.positions and signal == "SELL":
            signal = "HOLD"

        # ==============================
        # 🧠 BUILD APPROVAL ROW (NEW)
        # ==============================
        row = {
            "Ticker": ticker,
            "Signal": signal,
            "AI Confidence %": confidence * 100,
            "Trend Score": 1,  # basic placeholder (you can improve later)
            "AI Trade Score": confidence * 100,
            "Risk Reward": 2,
            "Trade Grade": "A",
            "Trade Decision": signal
        }

        # ==============================
        # 🚀 APPROVAL ENGINE (NEW CORE)
        # ==============================
        approved, reason = approve_trade(
            row,
            open_positions_count=len(ss.paper_engine.positions)
        )

        print(f"🧠 APPROVAL → {ticker}: {approved} | {reason}")

        # ==============================
        # ❌ BLOCK IF NOT APPROVED
        # ==============================
        if not approved:
            print(f"⛔ TRADE BLOCKED → {ticker} | {reason}")
            continue

        # ==============================
        # 📊 LOGGING
        # ==============================
        print(f"{ticker} | Price: {price:.2f} | Signal: {signal} | Confidence: {confidence}")

        # ==============================
        # 🚀 EXECUTION
        # ==============================
        print(f"🚀 EXECUTION CHECK → {ticker} | Signal: {signal}")
        print(f"🚀 BEFORE EXECUTION → positions: {ss.paper_engine.positions}")

        if signal in ["BUY", "SELL"]:
            execute_trade(ticker, signal, price)

        print(f"🚀 AFTER EXECUTION → positions: {ss.paper_engine.positions}")
# =========================
# PORTFOLIO
# =========================
def portfolio_value():
    total = cash

    for ticker, pos in positions.items():
        df = yf.download(ticker, period="1d", interval="1m")
        price = safe_float(df["Close"])
        total += pos["shares"] * price

    return total

# =========================
# PERFORMANCE
# =========================
def performance_report():
    print("\n📊 PERFORMANCE REPORT (basic)")

# =========================
# MAIN
# =========================
# if __name__ == "__main__":
#     init_trade_journal()
#
#     while True:
#         run_engine()
#         time.sleep(60)