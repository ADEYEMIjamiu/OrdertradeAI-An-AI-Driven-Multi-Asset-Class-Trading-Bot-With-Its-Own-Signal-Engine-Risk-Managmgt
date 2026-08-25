"""
oanda_broker.py -- OANDA v20 REST API connector (practice/demo environment).

Added 2026-08-24, alongside researching alternative forex brokers (#120)
after discovering eToro's native "trailing" stop-loss reports itself as
enabled but doesn't actually ratchet (see etoro_broker.py's
ETORO_USE_TRAILING_STOP docstring for the full story). OANDA's v20 API
supports a genuinely broker-managed trailing stop -- trailingStopLossOnFill,
attached as a dependent order at the moment a position fills and tracked
entirely on OANDA's servers -- which is the real version of what we had to
fake for eToro with apply_etoro_trailing_lock()'s poll-and-PATCH workaround.

IMPORTANT -- this is a SCAFFOLD, not yet a tested connector:
This was written from OANDA's public v20 API documentation while an OANDA
Asia Pacific demo account application was pending review, so none of it has
been exercised against a live API response yet. Follow the same path
etoro_broker.py did: build this, run check_broker_connection() and a
standalone buy/close test script once OANDA_API_TOKEN/OANDA_ACCOUNT_ID
exist, fix whatever the real API responses reveal is wrong, THEN wire it
into app.py -- manual-confirm only at first, same as eToro was. Do not
treat this file as production-ready before that's happened.

Credentials (add to .env, following the same pattern as ETORO_API_KEY/
ETORO_USER_KEY):
    OANDA_API_TOKEN=<personal access token, from hub.oanda.com Trading
        Tools -> API, or My Account -> Manage API Access>
    OANDA_ACCOUNT_ID=<looks like 101-004-XXXXXXX-001 for a practice account>
    OANDA_ENVIRONMENT=practice   (or "live" -- defaults to practice so this
        can never accidentally hit a real-money account by omission)
"""
import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv("OANDA_API_TOKEN")
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
ENVIRONMENT = os.getenv("OANDA_ENVIRONMENT", "practice")

BASE_URL = (
    "https://api-fxpractice.oanda.com"
    if ENVIRONMENT == "practice"
    else "https://api-fxtrade.oanda.com"
)

# OANDA's v20 API defaults units to whole numbers for most instruments,
# and margin/leverage is account-level (set by OANDA per regulatory
# division), not something we choose per-trade the way ETORO_LEVERAGE
# is a bot-side constant. Left unset here deliberately -- get_positions()
# below reads the account's actual margin rate rather than assuming one,
# once this is live-tested.
OANDA_STOP_LOSS_PCT = 0.03
OANDA_TAKE_PROFIT_PCT = 0.05

# 2026-08-24: unlike eToro (where the trailing stop has to be faked by
# polling and re-patching a fixed stop level -- see
# apply_etoro_trailing_lock() in app.py), OANDA's trailingStopLossOnFill
# is a genuine dependent order attached at fill time and trailed by
# OANDA's own servers. TRAILING_DISTANCE below is in PRICE UNITS (e.g.
# 0.0050 for EUR_USD = 50 pips), not a percentage -- OANDA's API requires
# an absolute distance, not a percent. This needs tuning per-instrument
# once real pricing is available; treat this value as a placeholder.
OANDA_USE_NATIVE_TRAILING_STOP = True
OANDA_TRAILING_DISTANCE_PLACEHOLDER = 0.0050


# 2026-08-24: OANDA instrument codes use underscores (EUR_USD), not the
# yfinance-style suffix format this project's ticker universe uses
# (EURUSD=X). Metals are confirmed OANDA instruments (XAU_USD = gold,
# XAG_USD = silver); the two energy tickers (CL=F WTI crude, and any
# Brent equivalent) are NOT yet confirmed against OANDA's actual
# instrument list and must be checked (GET /v3/accounts/{id}/instruments)
# before this mapping is trusted for commodities -- WTI crude on OANDA
# is commonly WTICO_USD but that needs live verification, not a guess.
_TICKER_MAP = {
    "EURUSD=X": "EUR_USD",
    "GBPUSD=X": "GBP_USD",
    "USDJPY=X": "USD_JPY",
    "GC=F": "XAU_USD",   # gold -- confirmed standard OANDA instrument
    "SI=F": "XAG_USD",   # silver -- confirmed standard OANDA instrument
    "CL=F": "WTICO_USD",  # UNVERIFIED -- confirm via /instruments before use
}


def resolve_project_ticker(project_ticker: str) -> str:
    """
    Maps this project's yfinance-style ticker (e.g. "EURUSD=X") to
    OANDA's instrument code (e.g. "EUR_USD"). Raises rather than
    guessing if a ticker isn't in the confirmed map -- same philosophy
    as etoro_broker.py's resolve_project_ticker().
    """
    ticker = str(project_ticker).upper().strip()
    if ticker not in _TICKER_MAP:
        raise ValueError(
            f"No confirmed OANDA instrument mapping for {ticker!r}. "
            f"Check GET /v3/accounts/{{id}}/instruments and add it to "
            f"_TICKER_MAP rather than guessing."
        )
    return _TICKER_MAP[ticker]


def _headers():
    return {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }


def check_broker_connection():
    """
    Confirms the API token/account ID actually authenticate, mirroring
    etoro_broker.check_broker_connection(). Returns (True, summary_dict)
    on success, (False, error_message) on failure -- deliberately never
    raises, so callers can surface a clean status instead of a traceback.
    """
    if not API_TOKEN or not ACCOUNT_ID:
        return False, "OANDA_API_TOKEN / OANDA_ACCOUNT_ID not set in .env"

    try:
        response = requests.get(
            f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/summary",
            headers=_headers(),
            timeout=15,
        )
        if response.status_code != 200:
            return False, f"OANDA API returned {response.status_code}: {response.text[:300]}"
        return True, response.json().get("account", {})
    except requests.exceptions.RequestException as e:
        return False, f"OANDA connection failed: {e}"


def get_account_summary():
    """
    Raw account summary (balance, unrealized P&L, margin used/available,
    open trade/position counts) straight from OANDA -- the equivalent of
    etoro_broker._fetch_client_portfolio() / broker.get_account() for
    Alpaca. Raises on failure rather than silently returning an empty
    dict, matching this project's broker-agnostic app.py pattern of
    wrapping every broker call in its own try/except.
    """
    response = requests.get(
        f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/summary",
        headers=_headers(),
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["account"]


def get_current_price(ticker: str) -> float:
    """
    Mid price (average of bid/ask) for `ticker`, matching
    etoro_broker.get_current_price()'s return shape (a single float).
    """
    instrument = resolve_project_ticker(ticker)
    response = requests.get(
        f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/pricing",
        headers=_headers(),
        params={"instruments": instrument},
        timeout=15,
    )
    response.raise_for_status()
    prices = response.json()["prices"]
    if not prices:
        raise RuntimeError(f"No pricing returned for {instrument}")

    bid = float(prices[0]["bids"][0]["price"])
    ask = float(prices[0]["asks"][0]["price"])
    return (bid + ask) / 2


def get_positions():
    """
    Open positions in the same shape etoro_broker.get_positions() and
    binance_broker.get_positions() already use elsewhere in this
    project (list of dicts with at least "symbol" and "qty"), so
    find_rotation_candidates() / _get_held_positions_for_rotation() in
    app.py can eventually treat OANDA the same way it treats the other
    three brokers, once this is wired in.

    NOTE: OANDA's openPositions endpoint reports units as a signed
    string ("long"/"short" sub-objects, each with its own "units"), not
    a single signed qty -- this flattens that into this project's
    existing {"symbol", "qty", "side", ...} convention, but hasn't been
    checked against a real response yet. Verify field names once a live
    account with an actual open position exists.
    """
    response = requests.get(
        f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/openPositions",
        headers=_headers(),
        timeout=15,
    )
    response.raise_for_status()
    raw_positions = response.json().get("positions", [])

    positions = []
    for p in raw_positions:
        long_units = float(p.get("long", {}).get("units", 0) or 0)
        short_units = float(p.get("short", {}).get("units", 0) or 0)

        if long_units != 0:
            positions.append({
                "symbol": p["instrument"],
                "qty": abs(long_units),
                "side": "long",
                "avg_price": float(p.get("long", {}).get("averagePrice", 0) or 0),
                "unrealized_pnl": float(p.get("long", {}).get("unrealizedPL", 0) or 0),
            })
        if short_units != 0:
            positions.append({
                "symbol": p["instrument"],
                "qty": abs(short_units),
                "side": "short",
                "avg_price": float(p.get("short", {}).get("averagePrice", 0) or 0),
                "unrealized_pnl": float(p.get("short", {}).get("unrealizedPL", 0) or 0),
            })

    return positions


def buy(ticker: str, units: int, use_trailing_stop: bool = True):
    """
    Places a market order, optionally with broker-managed stop-loss/
    take-profit/trailing-stop dependent orders attached at fill time --
    this is the piece eToro fundamentally can't do reliably (see the
    module docstring above).

    `units` is a whole number of the instrument's base currency (NOT a
    USD notional amount like etoro_broker.buy()'s usd_amount -- OANDA's
    API works in units, not dollars, so whatever calls this needs to
    convert position-sizing $ amounts to units using the current price
    first). Negative units would open a short; this project's use case
    is long-only for forex/commodities (matching eToro's usage), so this
    intentionally doesn't accept a side parameter yet.

    UNTESTED against a live response -- the order payload structure
    below follows OANDA's documented v20 order-creation schema, but the
    dependent-order nesting (stopLossOnFill/trailingStopLossOnFill) needs
    to be confirmed against a real fill before this is trusted with
    money, same as every other connector in this project was verified
    live before being wired into app.py.
    """
    instrument = resolve_project_ticker(ticker)
    price = get_current_price(ticker)

    order = {
        "order": {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(int(units)),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {
                "price": f"{price * (1 - OANDA_STOP_LOSS_PCT):.5f}",
            },
        }
    }

    if use_trailing_stop and OANDA_USE_NATIVE_TRAILING_STOP:
        order["order"]["trailingStopLossOnFill"] = {
            "distance": f"{OANDA_TRAILING_DISTANCE_PLACEHOLDER:.5f}",
        }
    else:
        order["order"]["takeProfitOnFill"] = {
            "price": f"{price * (1 + OANDA_TAKE_PROFIT_PCT):.5f}",
        }

    response = requests.post(
        f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/orders",
        headers=_headers(),
        data=json.dumps(order),
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def close_position(ticker: str, side: str = "long"):
    """
    Closes an entire open position for `ticker`. OANDA's close endpoint
    takes longUnits/shortUnits set to "ALL" rather than a position ID
    the way eToro's close_position(position_id) does -- there's no
    per-trade position ID needed here since OANDA nets same-instrument
    positions by default (positionFill="DEFAULT" above).
    """
    instrument = resolve_project_ticker(ticker)
    payload = {"longUnits": "ALL"} if side == "long" else {"shortUnits": "ALL"}

    response = requests.put(
        f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/positions/{instrument}/close",
        headers=_headers(),
        data=json.dumps(payload),
        timeout=15,
    )
    response.raise_for_status()
    return response.json()
