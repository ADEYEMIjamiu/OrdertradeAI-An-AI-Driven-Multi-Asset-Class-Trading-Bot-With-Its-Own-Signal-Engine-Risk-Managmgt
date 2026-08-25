"""
Per-user broker connections for the multi-tenant SaaS product.

This is the piece that makes "bring your own broker" real: given a
user_id, build a fresh broker client/session from THAT user's own
decrypted credentials (engines/tenant_engine.py) and check it actually
works -- without touching broker.py / binance_broker.py / etoro_broker.py
at all.

Why a separate module instead of extending the existing broker files:
broker.py, binance_broker.py, and etoro_broker.py each build ONE
module-level client from the single owner's global .env credentials at
import time (e.g. broker.py's `client = TradingClient(API_KEY,
SECRET_KEY, paper=True)`), and every function in those files uses that
one shared client. That's correct and intentional for the existing
single-owner bot, but it means there is no way to route a call through
a DIFFERENT user's credentials without either mutating shared global
state (unsafe -- concurrent Streamlit sessions for different users would
race on it) or building fresh clients per user, per call, which is what
this file does instead. Nothing here imports or modifies broker.py /
binance_broker.py / etoro_broker.py, so the existing live single-owner
bot is completely unaffected by anything in this file.

FIX 2026-08-26: this originally did ONLY connection verification
(read-only: fetch account status/balance). Now also includes order
execution for Alpaca (stocks) and Binance (crypto) -- buy_stock_for_user()/
sell_stock_for_user()/buy_crypto_for_user()/sell_crypto_for_user() below.

eToro (forex/commodities) execution is DELIBERATELY NOT included yet --
etoro_broker.py's buy() depends on a full instrument-catalog lookup
(ticker -> instrumentId, see that file's _load_instrument_catalog()) and
leverage/stop-loss-rate computation that would need to be ported here
too, which is real additional work, not a small addition. Only Alpaca
and Binance credentials can actually place trades through this module
right now; a user's connected eToro credentials are still read-only
(Test Connection only) until that follow-up is built.

SAFETY -- read this before changing paper=True / set_sandbox_mode(True)
below: both are hardcoded, not derived from user_settings or the stored
credential's "environment" field. This is deliberate defense-in-depth --
even if user_settings.allow_live_trading were ever flipped to true
somewhere else in the codebase, these specific functions still
physically cannot route an order to a real-money account, because the
paper/testnet flag never comes from anywhere except this hardcoded
constant. Changing that is a real-money decision and should never be an
incidental side effect of an unrelated change.

NOT included: any kill-switch check. The single-owner bot's
EXECUTION_KILL_SWITCH (config.py) is intentionally not wired in here --
that config constant belongs to the single-owner's app.py, not this
multi-tenant module, and reusing it would incorrectly couple one
person's personal kill switch to every SaaS user's trading. A SaaS-wide
(or per-user) emergency stop is a real gap that needs its own design
before this is used for anything beyond manual/local testing.
"""

import ccxt
import requests
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from engines import tenant_engine as tenant


def check_user_alpaca_connection(user_id):
    """
    Builds a fresh Alpaca TradingClient from this user's OWN stored
    paper-trading credentials and validates it with a real account call.
    Mirrors broker.check_broker_connection()'s return shape.
    """
    creds = tenant.get_broker_credentials(user_id, "ALPACA")
    if creds is None:
        return {
            "connected": False,
            "error": "No Alpaca credentials saved for this user.",
        }

    try:
        client = TradingClient(
            creds["api_key"],
            creds["api_secret"],
            paper=True,  # this platform only supports paper trading at launch
        )
        account = client.get_account()

        return {
            "connected": True,
            "account_status": str(account.status),
            "trading_blocked": bool(account.trading_blocked),
            "buying_power": float(account.buying_power),
            "cash": float(account.cash),
            "equity": float(account.equity),
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
            "error": str(e),
        }


def check_user_binance_connection(user_id):
    """
    Builds a fresh ccxt Binance TESTNET exchange instance from this
    user's OWN stored testnet credentials. Mirrors
    binance_broker.check_broker_connection()'s return shape.
    """
    creds = tenant.get_broker_credentials(user_id, "BINANCE")
    if creds is None:
        return {
            "connected": False,
            "error": "No Binance credentials saved for this user.",
        }

    try:
        exchange = ccxt.binance({
            "apiKey": creds["api_key"],
            "secret": creds["api_secret"],
            "enableRateLimit": True,
        })
        exchange.set_sandbox_mode(True)  # testnet only, same as binance_broker.py

        balance = exchange.fetch_balance()
        usdt = balance.get("USDT", {}).get("free", 0)

        return {
            "connected": True,
            "cash": float(usdt),
            "error": None,
        }
    except Exception as e:
        return {
            "connected": False,
            "cash": 0.0,
            "error": str(e),
        }


def check_user_etoro_connection(user_id):
    """
    Calls eToro's portfolio endpoint directly with this user's OWN
    stored API key / user key headers (mirrors etoro_broker.py's
    _headers()/_fetch_client_portfolio() pattern) -- does not touch
    etoro_broker.py's global module state at all. Mirrors
    etoro_broker.check_broker_connection()'s return shape.
    """
    creds = tenant.get_broker_credentials(user_id, "ETORO")
    if creds is None:
        return {
            "connected": False,
            "error": "No eToro credentials saved for this user.",
        }

    is_demo = creds["environment"] != "real"
    portfolio_path = "trading/info/demo/portfolio" if is_demo else "trading/info/portfolio"
    api_base = "https://public-api.etoro.com/api/v1"

    headers = {
        "x-api-key": creds["api_key"],
        "x-user-key": creds["api_secret"],  # stored as "api_secret" slot; eToro calls this the user key
    }

    try:
        response = requests.get(f"{api_base}/{portfolio_path}", headers=headers, timeout=15)
        response.raise_for_status()
        portfolio = response.json().get("clientPortfolio", {})

        credit = float(portfolio.get("credit", 0.0))
        positions = portfolio.get("positions", [])
        unrealized_pnl = sum(float(p.get("netProfit", 0) or 0) for p in positions)

        return {
            "connected": True,
            "account_status": "DEMO" if is_demo else "REAL",
            "cash": credit,
            "equity": credit + unrealized_pnl,
            "error": None,
        }
    except Exception as e:
        return {
            "connected": False,
            "account_status": None,
            "cash": 0.0,
            "equity": 0.0,
            "error": str(e),
        }


_CHECKERS = {
    "ALPACA": check_user_alpaca_connection,
    "BINANCE": check_user_binance_connection,
    "ETORO": check_user_etoro_connection,
}


def check_user_broker_connection(user_id, broker):
    """Dispatch helper -- check_user_broker_connection(user_id, "ALPACA")."""
    checker = _CHECKERS.get(broker.upper())
    if checker is None:
        return {"connected": False, "error": f"Unknown broker: {broker}"}
    return checker(user_id)


# ============================================================
# ORDER EXECUTION -- Alpaca (stocks) and Binance (crypto) only.
# See module docstring for why eToro isn't here yet and why
# paper=True / set_sandbox_mode(True) below are hardcoded, not
# settings-driven.
# ============================================================

def _require_alpaca_client(user_id):
    creds = tenant.get_broker_credentials(user_id, "ALPACA")
    if creds is None:
        raise ValueError("No Alpaca credentials saved for this user.")
    return TradingClient(creds["api_key"], creds["api_secret"], paper=True)


def _require_binance_exchange(user_id):
    creds = tenant.get_broker_credentials(user_id, "BINANCE")
    if creds is None:
        raise ValueError("No Binance credentials saved for this user.")
    exchange = ccxt.binance({
        "apiKey": creds["api_key"],
        "secret": creds["api_secret"],
        "enableRateLimit": True,
    })
    exchange.set_sandbox_mode(True)
    return exchange


def _to_binance_symbol(ticker):
    """Same conversion as binance_broker.py's _to_binance_symbol() --
    duplicated here (small and stateless) rather than imported, to keep
    this module fully independent of the single-owner broker files."""
    return f"{ticker.replace('-USD', '')}/USDT"


def buy_stock_for_user(user_id, symbol, dollars):
    """
    Per-user Alpaca market BUY, sized by dollar amount. Mirrors
    broker.py's buy_stock(), but against THIS user's own paper account
    instead of the single owner's.

    Raises on insufficient buying power or any Alpaca API error --
    callers (the future per-user execution loop) are expected to catch
    and log/journal failures per user, the same way app.py's
    execute_alpaca_trades() already does for the single-owner bot.
    """
    client = _require_alpaca_client(user_id)
    account = client.get_account()
    buying_power = float(account.buying_power)

    if dollars > buying_power:
        raise Exception(f"Not enough buying power (have ${buying_power:.2f}, need ${dollars:.2f}).")

    order = MarketOrderRequest(
        symbol=symbol,
        notional=dollars,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
    return client.submit_order(order_data=order)


def sell_stock_for_user(user_id, symbol, qty):
    """Per-user Alpaca market SELL. Mirrors broker.py's sell_stock()."""
    client = _require_alpaca_client(user_id)

    order = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    return client.submit_order(order_data=order)


def buy_crypto_for_user(user_id, ticker, usd_amount):
    """
    Per-user Binance testnet market BUY, sized by dollar amount. Mirrors
    binance_broker.py's buy_crypto(), against THIS user's own testnet
    account. Returns (order, price, quantity) same as the original.
    """
    exchange = _require_binance_exchange(user_id)
    symbol = _to_binance_symbol(ticker)

    ticker_data = exchange.fetch_ticker(symbol)
    price = ticker_data["last"]
    quantity = usd_amount / price

    order = exchange.create_market_buy_order(symbol, quantity)
    return order, price, quantity


def sell_crypto_for_user(user_id, ticker, quantity):
    """Per-user Binance testnet market SELL. Mirrors binance_broker.py's sell_crypto()."""
    exchange = _require_binance_exchange(user_id)
    symbol = _to_binance_symbol(ticker)
    return exchange.create_market_sell_order(symbol, quantity)
