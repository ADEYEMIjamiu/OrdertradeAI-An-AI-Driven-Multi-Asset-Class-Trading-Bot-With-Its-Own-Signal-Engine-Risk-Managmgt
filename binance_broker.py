"""
Binance TESTNET connector -- crypto's equivalent of broker.py (Alpaca).

Uses Binance's public testnet (https://testnet.binance.vision), a free
sandbox with fake funds. This is completely separate from any real
Binance account -- no real money is ever touched by this file.

Crypto markets trade 24/7, so this connector doesn't need the market-hours
logic that broker.py has for Alpaca (get_market_clock, etc).
"""

import os
import ccxt
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BINANCE_TESTNET_API_KEY")
SECRET_KEY = os.getenv("BINANCE_TESTNET_SECRET_KEY")

exchange = ccxt.binance({
    "apiKey": API_KEY,
    "secret": SECRET_KEY,
    "enableRateLimit": True,
})
exchange.set_sandbox_mode(True)  # routes every call to testnet, never real Binance


def _to_binance_symbol(ticker: str) -> str:
    """
    This project uses tickers like 'BTC-USD'; ccxt/Binance expects 'BTC/USDT'
    (Binance testnet trades against USDT, not USD directly).
    """
    base = ticker.replace("-USD", "")
    return f"{base}/USDT"


def check_broker_connection():
    """
    Validate the Binance testnet connection. Mirrors the shape of
    broker.check_broker_connection() so the dashboard can display both
    consistently. Crypto has no market-hours concept, so market_open
    is always True.
    """
    try:
        balance = exchange.fetch_balance()
        usdt = balance.get("USDT", {}).get("free", 0)

        return {
            "connected": True,
            "account_status": "ACTIVE",
            "trading_blocked": False,
            "buying_power": float(usdt),
            "cash": float(usdt),
            "equity": float(usdt),
            "market_open": True,
            "error": None,
        }

    except Exception as e:
        return {
            "connected": False,
            "account_status": None,
            "trading_blocked": True,
            "buying_power": 0.0,
            "cash": 0.0,
            "equity": 0.0,
            "market_open": True,
            "error": str(e),
        }


def get_available_usdt():
    """
    USDT actually available to spend on Binance testnet right now.
    Used by risk_engine's cash check for crypto trades -- previously that
    check read st.session_state.cash (the local stock paper-trading pool)
    even for crypto orders, so crypto could get blocked as "insufficient
    cash" once stocks used up the stock cash pool, despite this testnet
    balance being untouched and available.
    """
    try:
        balance = exchange.fetch_balance()
        return float(balance.get("USDT", {}).get("free", 0))
    except Exception as e:
        print(f"Error fetching Binance testnet USDT balance: {e}")
        return 0.0


def get_positions():
    """
    Return current non-zero crypto holdings for ONLY the assets this
    project actually trades (BTC, ETH, SOL, BNB).

    Important: Binance testnet accounts come pre-seeded with dozens of
    unrelated fake test tokens (random dust) that have nothing to do with
    this bot's trades. Without filtering, every one of those would show
    up here as a "position", which is misleading. We only care about
    assets that match our own crypto asset universe.
    """
    TRACKED_ASSETS = {"BTC", "ETH", "SOL", "BNB"}

    try:
        balance = exchange.fetch_balance()
        positions = []

        for asset, amounts in balance.get("total", {}).items():
            if asset not in TRACKED_ASSETS:
                continue
            if amounts and amounts > 0:
                positions.append({
                    "symbol": f"{asset}-USD",
                    "qty": amounts,
                })

        return positions

    except Exception as e:
        print(f"Error fetching Binance testnet positions: {e}")
        return []


def get_crypto_positions_value(market_df):
    """
    Total USD value of all current Binance testnet crypto positions,
    priced using the same live market_df the rest of the app already uses
    (avoids a redundant/inconsistent price lookup against Binance itself).
    """
    positions = get_positions()

    if not positions or market_df is None or market_df.empty:
        return 0.0

    total_value = 0.0

    for position in positions:
        ticker = position["symbol"]
        qty = float(position["qty"])

        price_rows = market_df.loc[market_df["Ticker"] == ticker, "Price ($)"]

        if not price_rows.empty:
            total_value += qty * float(price_rows.iloc[0])

    return total_value


def buy_crypto(ticker: str, usd_amount: float):
    """Market BUY on Binance testnet, sized by dollar amount."""
    symbol = _to_binance_symbol(ticker)
    ticker_data = exchange.fetch_ticker(symbol)
    price = ticker_data["last"]
    quantity = usd_amount / price

    order = exchange.create_market_buy_order(symbol, quantity)
    return order, price, quantity


def sell_crypto(ticker: str, quantity: float):
    """Market SELL on Binance testnet."""
    symbol = _to_binance_symbol(ticker)
    order = exchange.create_market_sell_order(symbol, quantity)
    return order
