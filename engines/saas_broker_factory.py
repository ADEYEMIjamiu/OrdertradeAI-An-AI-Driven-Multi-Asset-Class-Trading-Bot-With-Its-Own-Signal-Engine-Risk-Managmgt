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

This module deliberately does ONLY connection verification for now
(read-only: fetch account status/balance) -- not order placement. That
is the next phase, once this foundation is confirmed working.
"""

import ccxt
import requests
from alpaca.trading.client import TradingClient

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
