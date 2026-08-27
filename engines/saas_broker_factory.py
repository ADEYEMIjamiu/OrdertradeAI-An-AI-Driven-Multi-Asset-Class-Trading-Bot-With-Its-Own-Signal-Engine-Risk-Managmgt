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

FOLLOW-UP 2026-08-26: eToro (forex/commodities) execution is now also
included -- buy_etoro_for_user() below, plus the per-user instrument-
catalog lookup and leverage/stop-loss-rate computation it needs. This
imports (does not duplicate) the pure, credential-independent helpers
from etoro_broker.py -- resolve_project_ticker(), _is_forex_or_commodity_
ticker(), and the ETORO_LEVERAGE/ETORO_STOP_LOSS_PCT/ETORO_TAKE_PROFIT_PCT/
ETORO_USE_TRAILING_STOP constants -- since those don't touch that file's
module-level client (API_KEY/USER_KEY/_headers()) at all, so importing
them doesn't create the cross-user coupling risk described above for why
this file exists in the first place. The instrument catalog itself IS
duplicated (not imported) as a per-user cache keyed by user_id, rather
than reusing etoro_broker.py's single global _instrument_catalog -- that
cache is populated via an authenticated API call using whichever
credentials first triggered it, and reusing one global copy across users
would mean one user's connected eToro key silently becomes a dependency
for every other user's ticker lookups. Slightly wasteful (the catalog
is ~16k identical instruments regardless of whose key fetches it) in
exchange for the same no-shared-state-across-users guarantee every other
per-user function in this file already has.

KNOWN GAP: buy_etoro_for_user() places the initial order (with a real
broker-side fixed stop-loss/take-profit, same as etoro_broker.py's own
buy()) and best-effort upgrades it to a broker-side TRAILING stop, but
there is no per-user equivalent of app.py's apply_etoro_trailing_lock()
(the single-owner bot's own workaround for eToro's trailing stop not
actually working as documented -- see etoro_broker.py's 2026-08-24
comment). SaaS eToro positions get the initial fixed 3%/5% band and
whatever eToro's own (unreliable) trailing flag does, not the
single-owner bot's proven local ratcheting. Also NOT included: eToro
positions in engines/saas_exit_engine.py -- that module only checks
US_STOCKS/CRYPTO for exits (relies on saas_order_manager's simple
most-recent-FILLED tracking); an eToro SELL signal from the AI is still
shown in results but never acted on, same "BUY-side new entries first"
scoping this whole decision loop already has, just extended to this
broker too. Both are real gaps worth closing before this is trusted
beyond supervised testing, same caveat as everything else in this file.

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

import time
import uuid

import ccxt
import requests
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from engines import tenant_engine as tenant
from engines import saas_order_manager as journal
from etoro_broker import (
    resolve_project_ticker,
    _is_forex_or_commodity_ticker,
    ETORO_LEVERAGE,
    ETORO_STOP_LOSS_PCT,
    ETORO_TAKE_PROFIT_PCT,
    ETORO_USE_TRAILING_STOP,
)


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
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
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


def get_user_account_balance(user_id, asset_class):
    """
    Real, current spendable balance for this user's own broker account,
    for whichever broker owns this asset class. Mirrors risk_engine.
    get_account_balance()'s per-asset-class dispatch and never-raise
    contract, but reads from THIS user's own credentials via the
    checkers above instead of the single owner's global broker.py/
    binance_broker.py. Used by the per-user decision loop
    (saas_decision_engine.py) to size trades with calculate_trade_amount().

    US_STOCKS (Alpaca), CRYPTO (Binance), and -- as of the 2026-08-26
    eToro follow-up -- FOREX/COMMODITIES (eToro) are all wired here.
    """
    if asset_class == "CRYPTO":
        result = check_user_binance_connection(user_id)
        if not result.get("connected"):
            return 0.0
        return float(result.get("cash", 0) or 0)

    if asset_class == "US_STOCKS":
        result = check_user_alpaca_connection(user_id)
        if not result.get("connected"):
            return 0.0
        # buying_power (not cash) so it reflects whatever margin/settlement
        # rules Alpaca's own paper account already applies -- same field
        # buy_stock_for_user() above checks before submitting an order.
        return float(result.get("buying_power", 0) or 0)

    if asset_class in ("FOREX", "COMMODITIES"):
        result = check_user_etoro_connection(user_id)
        if not result.get("connected"):
            return 0.0
        # "cash" here is eToro's "credit" field (see check_user_etoro_
        # connection()'s docstring) -- the same balance both FOREX and
        # COMMODITIES draw from, since they share one eToro account per
        # user rather than separate sub-balances.
        return float(result.get("cash", 0) or 0)

    return 0.0


def get_user_exposure_percent(user_id, asset_class):
    """
    Added 2026-08-27 to close the gap saas_decision_engine.py's own
    module docstring flagged: "NOT included: portfolio-level exposure
    cap (MAX_PORTFOLIO_EXPOSURE)". Per-user equivalent of risk_engine.
    get_exposure_percent() -- % of this user's account equity already
    tied up in open positions, for whichever broker owns this
    asset_class.

    Computed PER BROKER, not blended across a user's Alpaca/Binance/eToro
    accounts -- those are three separate, unrelated external broker
    accounts (bring-your-own-broker, see this module's own docstring),
    not one shared portfolio, so a single blended number across all
    three would be meaningless. This mirrors the single-owner bot's own
    get_exposure_percent(), which (in its LIVE_TRADING branch) is
    likewise Alpaca-specific, not a cross-broker blend.

    Never raises -- returns 0.0 on any failure, same never-raise
    contract as get_user_account_balance() above. A genuinely broken
    connection is already caught earlier in the caller's own balance
    check; this failing open (0% exposure) rather than closed just means
    a bad connection blocks trading via the balance==0 gate, not this one
    silently double-blocking with a less useful error message.
    """
    if asset_class == "US_STOCKS":
        result = check_user_alpaca_connection(user_id)
        if not result.get("connected"):
            return 0.0
        equity = float(result.get("equity", 0) or 0)
        cash = float(result.get("cash", 0) or 0)
        if equity <= 0:
            return 0.0
        invested = max(equity - cash, 0.0)
        return (invested / equity) * 100

    if asset_class == "CRYPTO":
        return _get_binance_exposure_percent(user_id)

    if asset_class in ("FOREX", "COMMODITIES"):
        return _get_etoro_exposure_percent(user_id)

    return 0.0


def _get_binance_exposure_percent(user_id):
    """Values only TRACKED_ASSETS (this project's actual crypto universe)
    at a fresh per-coin ticker price -- mirrors binance_broker.
    get_positions()'s own dust-filtering reasoning (a testnet account
    commonly holds dozens of unrelated pre-seeded coins that would
    otherwise inflate "invested" with noise this bot never traded)."""
    try:
        exchange = _require_binance_exchange(user_id)
    except Exception:
        return 0.0

    try:
        from data.asset_universe import ASSET_UNIVERSE
        tracked = {t.replace("-USD", "") for t in ASSET_UNIVERSE["CRYPTO"]["symbols"]}

        balance = exchange.fetch_balance()
        free_usdt = float(balance.get("USDT", {}).get("free", 0) or 0)

        crypto_value = 0.0
        for asset, total_qty in balance.get("total", {}).items():
            if asset not in tracked or not total_qty:
                continue
            try:
                price = float(exchange.fetch_ticker(f"{asset}/USDT").get("last") or 0)
            except Exception:
                continue  # one bad ticker lookup shouldn't zero out the whole calculation
            crypto_value += float(total_qty) * price

        portfolio_value = free_usdt + crypto_value
        if portfolio_value <= 0:
            return 0.0
        return (crypto_value / portfolio_value) * 100
    except Exception:
        return 0.0


def _get_etoro_exposure_percent(user_id):
    """Duplicates check_user_etoro_connection()'s portfolio fetch rather
    than reusing it, since that function returns cash/equity only, not
    the per-position "amount" (invested/margin, same field buy()/
    etoro_broker.py already treat as position size) this needs. Costs one
    extra eToro API call per run when FOREX/COMMODITIES are both
    enabled -- same already-accepted tradeoff get_user_account_balance()
    above has (it also calls check_user_etoro_connection() once per
    asset class even though both share one eToro account)."""
    creds = tenant.get_broker_credentials(user_id, "ETORO")
    if creds is None:
        return 0.0

    is_demo = creds["environment"] != "real"
    portfolio_path = "trading/info/demo/portfolio" if is_demo else "trading/info/portfolio"
    api_base = "https://public-api.etoro.com/api/v1"
    headers = {
        "x-api-key": creds["api_key"],
        "x-user-key": creds["api_secret"],
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

    try:
        response = requests.get(f"{api_base}/{portfolio_path}", headers=headers, timeout=15)
        response.raise_for_status()
        portfolio = response.json().get("clientPortfolio", {})

        credit = float(portfolio.get("credit", 0.0))
        positions = portfolio.get("positions", [])
        unrealized_pnl = sum(float(p.get("netProfit", 0) or 0) for p in positions)
        invested = sum(float(p.get("amount", 0) or 0) for p in positions)

        equity = credit + unrealized_pnl
        if equity <= 0:
            return 0.0
        return (invested / equity) * 100
    except Exception:
        return 0.0


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

    Returns Alpaca's raw order response object -- callers MUST check its
    .status before treating this as filled (see app.py's
    execute_alpaca_trades() comment on the 2026-08-08 rotation incident:
    Alpaca's response right after submit_order() usually still reads
    "accepted"/"pending_new" even when the real fill happens moments
    later, so only an explicit "filled" status should ever be trusted).
    engines/saas_decision_engine.py's execution branch does this check;
    do not add a second stock-buying call site that skips it.

    Raises on insufficient buying power or any Alpaca API error --
    callers are expected to catch and log/journal failures per user, the
    same way app.py's execute_alpaca_trades() already does for the
    single-owner bot.
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


def get_alpaca_order_status_for_user(user_id, broker_order_id):
    """
    Look up the CURRENT status of a previously-submitted Alpaca order by
    its broker_order_id, using this user's own credentials. Used by
    saas_reconcile_engine.py to follow up on orders that were submitted
    but not confirmed filled at the time (status "new"/"accepted" --
    see saas_decision_engine.py's 2026-08-26 fix). Raises on API error
    or missing credentials; callers should catch and skip that order
    for this reconciliation pass rather than let one bad lookup stop the
    rest.
    """
    client = _require_alpaca_client(user_id)
    return client.get_order_by_id(broker_order_id)


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


def get_user_crypto_held_qty(user_id, ticker):
    """
    Added 2026-08-27 after a live SELL failure: "SOL-USD: Broker sell
    failed (Take-profit hit ...): binance Account has insufficient
    balance for requested action." saas_exit_engine.py was sizing its
    SELL off the ORIGINAL BUY order's journaled filled_quantity, which
    can drift from the wallet's real current balance -- exactly the
    class of bug the single-owner bot already avoids: app.py's crypto
    risk-management SELL path sizes off a live binance_broker.
    get_positions() wallet query, not its own order history. This is
    the per-user equivalent of that live query, for saas_exit_engine.py
    to cap its journal-sourced quantity against before selling.

    Never raises -- returns 0.0 on any failure (no credentials, API
    error, ticker not held, etc.), so a lookup failure fails toward "sell
    nothing" rather than an unguarded exception reaching the caller.

    FIX 2026-08-27 (same day, found immediately after deploying the
    first version of this function): originally read balance["total"]
    (free + locked/used), which still produced "insufficient balance"
    live -- confirmed the sell WAS attempted (not skipped as zero-held),
    so real_qty was > 0 but still exceeded what Binance would actually
    let go. Only balance["free"] is genuinely sellable; "total" can
    overstate that if any of the asset is locked in another open order
    or otherwise reserved. Now reads "free". Note: binance_broker.py's
    own get_positions() (the single-owner bot's reference this function
    was modeled on) still reads "total" -- same latent gap there, not
    fixed here since it's out of scope for this SaaS-side bug, but worth
    a follow-up if the single-owner bot ever hits the same failure.
    """
    try:
        exchange = _require_binance_exchange(user_id)
    except Exception:
        return 0.0

    try:
        base_asset = ticker.replace("-USD", "")
        balance = exchange.fetch_balance()
        return float(balance.get("free", {}).get(base_asset, 0) or 0)
    except Exception:
        return 0.0


# ============================================================
# ORDER EXECUTION -- eToro (forex/commodities). See module docstring
# ("FOLLOW-UP 2026-08-26") for the per-user-catalog-cache design
# decision and the known gaps (no trailing-lock ratchet, no exit-engine
# coverage) versus etoro_broker.py's single-owner version.
# ============================================================

ETORO_API_BASE = "https://public-api.etoro.com/api/v1"
ETORO_EXECUTION_BASE_V2 = "https://public-api.etoro.com/api/v2"

# ticker -> instrumentId, one catalog per user_id (see module docstring
# for why this isn't a single shared cache like etoro_broker.py's).
_etoro_instrument_catalog_cache = {}


def _require_etoro_creds(user_id):
    creds = tenant.get_broker_credentials(user_id, "ETORO")
    if creds is None:
        raise ValueError("No eToro credentials saved for this user.")
    return creds


def _etoro_headers_for_user(creds):
    return {
        "x-api-key": creds["api_key"],
        "x-user-key": creds["api_secret"],  # stored as "api_secret" slot; eToro calls this the user key
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


def _etoro_execution_prefix(creds):
    is_demo = creds["environment"] != "real"
    return "trading/execution/demo" if is_demo else "trading/execution"


def _etoro_positions_prefix(creds):
    is_demo = creds["environment"] != "real"
    return "trading/demo" if is_demo else "trading/real"


def _etoro_portfolio_path(creds):
    is_demo = creds["environment"] != "real"
    return "trading/info/demo/portfolio" if is_demo else "trading/info/portfolio"


def _load_etoro_catalog_for_user(user_id, creds):
    """Per-user cached ticker -> instrumentId map. See module docstring
    for why this is per-user rather than reusing etoro_broker.py's
    single global catalog. Same client-side exact-match approach as
    that file's _load_instrument_catalog() -- eToro's own filter params
    were confirmed live not to actually filter anything (see that
    function's docstring)."""
    if user_id in _etoro_instrument_catalog_cache:
        return _etoro_instrument_catalog_cache[user_id]

    response = requests.get(
        f"{ETORO_API_BASE}/market-data/instruments",
        headers=_etoro_headers_for_user(creds),
        timeout=30,
    )
    response.raise_for_status()
    items = response.json().get("instrumentDisplayDatas", [])

    catalog = {}
    for item in items:
        symbol = str(item.get("symbolFull", "")).upper().strip()
        instrument_id = item.get("instrumentID")
        if symbol and instrument_id is not None and symbol not in catalog:
            catalog[symbol] = instrument_id

    _etoro_instrument_catalog_cache[user_id] = catalog
    return catalog


def _get_etoro_instrument_id_for_user(user_id, creds, ticker):
    resolved = resolve_project_ticker(ticker)
    catalog = _load_etoro_catalog_for_user(user_id, creds)
    instrument_id = catalog.get(resolved)
    if instrument_id is None:
        raise ValueError(
            f"eToro instrument catalog has no exact symbolFull match for "
            f"'{resolved}' (from project ticker '{ticker}')."
        )
    return instrument_id


def get_etoro_current_price_for_user(user_id, ticker):
    """Current ask price for a ticker, via this user's own eToro
    credentials. Mirrors etoro_broker.get_current_price() -- used below
    to compute stopLossRate/takeProfitRate for leveraged orders."""
    creds = _require_etoro_creds(user_id)
    instrument_id = _get_etoro_instrument_id_for_user(user_id, creds, ticker)

    response = requests.get(
        f"{ETORO_API_BASE}/market-data/instruments/rates",
        params={"instrumentIds": instrument_id},
        headers=_etoro_headers_for_user(creds),
        timeout=10,
    )
    response.raise_for_status()
    rates = response.json().get("rates", [])
    if not rates:
        raise ValueError(f"No rates in eToro response for {ticker}.")

    price = rates[0].get("ask")
    if price is None:
        raise ValueError(f"No ask price in eToro rates response for {ticker}.")
    return float(price)


def _set_etoro_trailing_stop_for_user(user_id, creds, position_id, stop_loss_rate, take_profit_rate=None):
    """Best-effort broker-side trailing-stop upgrade, called by
    buy_etoro_for_user() right after a leveraged CFD position confirms
    open. Mirrors etoro_broker.set_trailing_stop() -- see that
    function's docstring for the full reasoning and its NOT-yet-proven-
    reliable caveat (etoro_broker.py's 2026-08-24 comment: eToro's own
    "trailing" flag was confirmed live NOT to actually ratchet the stop
    up despite reporting isTslEnabled=True). Callers must wrap this in
    try/except -- a failure here must never undo the buy that already
    succeeded; worst case the position keeps its original fixed
    stopLossRate, already set by the order itself."""
    payload = {"stopLossType": "trailing", "stopLossRate": stop_loss_rate}
    if take_profit_rate is not None:
        payload["takeProfitRate"] = take_profit_rate

    response = requests.patch(
        f"{ETORO_EXECUTION_BASE_V2}/{_etoro_positions_prefix(creds)}/positions/{position_id}",
        headers=_etoro_headers_for_user(creds),
        json=payload,
        timeout=25,
    )
    response.raise_for_status()
    return response.json()


def buy_etoro_for_user(user_id, ticker, usd_amount):
    """
    Per-user eToro market BUY, sized by dollar amount. Mirrors
    etoro_broker.py's buy() -- same leverage/stopLossRate handling for
    FOREX/COMMODITIES tickers (ETORO_LEVERAGE, ETORO_STOP_LOSS_PCT/
    ETORO_TAKE_PROFIT_PCT, imported from that file -- see module
    docstring for why importing these specific constants is safe), same
    fill-confirmation poll (longer window for leveraged CFDs, which take
    longer to confirm than crypto/stocks -- see etoro_broker.buy()'s
    docstring for the full live-testing history behind that 15s number).

    Returns {"position_id", "executed_price", "trailing_stop_set", "raw"}
    -- position_id is None if the poll window elapsed before eToro
    confirmed the fill (NOT necessarily a failure -- see etoro_broker.
    buy()'s docstring on stocks queuing outside market hours; for FOREX/
    COMMODITIES specifically this would mean a genuinely slow fill, not
    a market-closed queue, since these trade nearly continuously).
    Callers (saas_decision_engine.py) must treat position_id is None the
    same as any other "not yet confirmed filled" case -- do not journal
    as bought/filled unless a position_id came back.
    """
    creds = _require_etoro_creds(user_id)
    instrument_id = _get_etoro_instrument_id_for_user(user_id, creds, ticker)
    is_leveraged_cfd = _is_forex_or_commodity_ticker(ticker)

    order_payload = {
        "action": "open",
        "transaction": "buy",
        "instrumentId": instrument_id,
        "orderType": "mkt",
        "amount": usd_amount,
        "orderCurrency": "usd",
        "leverage": ETORO_LEVERAGE if is_leveraged_cfd else 1,
    }

    if is_leveraged_cfd:
        current_price = get_etoro_current_price_for_user(user_id, ticker)
        order_payload["stopLossRate"] = round(current_price * (1 - ETORO_STOP_LOSS_PCT), 5)
        order_payload["takeProfitRate"] = round(current_price * (1 + ETORO_TAKE_PROFIT_PCT), 5)

    response = requests.post(
        f"{ETORO_EXECUTION_BASE_V2}/{_etoro_execution_prefix(creds)}/orders",
        headers=_etoro_headers_for_user(creds),
        json=order_payload,
        timeout=25,
    )
    response.raise_for_status()
    order = response.json()
    order_id = order.get("orderId")

    position_id = None
    executed_price = None

    poll_attempts = 15 if is_leveraged_cfd else 5
    for _ in range(poll_attempts):
        portfolio_response = requests.get(
            f"{ETORO_API_BASE}/{_etoro_portfolio_path(creds)}",
            headers=_etoro_headers_for_user(creds),
            timeout=25,
        )
        portfolio_response.raise_for_status()
        portfolio = portfolio_response.json().get("clientPortfolio", {})
        match = next(
            (p for p in portfolio.get("positions", []) if p.get("orderID") == order_id),
            None,
        )
        if match is not None:
            position_id = match.get("positionID")
            executed_price = match.get("openRate")
            break
        time.sleep(1)

    trailing_stop_set = False
    if is_leveraged_cfd and position_id is not None and ETORO_USE_TRAILING_STOP:
        try:
            _set_etoro_trailing_stop_for_user(
                user_id, creds, position_id,
                stop_loss_rate=order_payload["stopLossRate"],
                take_profit_rate=order_payload.get("takeProfitRate"),
            )
            trailing_stop_set = True
        except Exception as trailing_error:
            print(
                f"Could not set trailing stop for user {user_id} position "
                f"{position_id} ({ticker}): {trailing_error}"
            )

    return {
        "position_id": position_id,
        "executed_price": executed_price,
        "trailing_stop_set": trailing_stop_set,
        "raw": order,
    }


def sell_etoro_for_user(user_id, position_id):
    """
    FOLLOW-UP 2026-08-26: closes an existing eToro position by its
    position_id. Mirrors etoro_broker.close_position() exactly -- same
    v1 API base + EXECUTION_PREFIX endpoint, same "look the position up
    first to get its instrumentId, since the close endpoint requires it
    in the body even though position_id is already in the URL" workaround
    (see that function's docstring for the live-tested history behind
    this). Used by saas_exit_engine.py once eToro is added to its
    _ASSET_CLASS_BROKER map -- unlike sell_stock_for_user()/
    sell_crypto_for_user(), this takes a position_id, not a ticker +
    quantity, since that's what eToro's close endpoint actually needs;
    the exit engine passes entry_order["broker_order_id"] (the eToro
    positionID stored once the BUY confirmed filled, either at buy time
    or via reconcile_user_etoro_orders()).

    Confirmed synchronous by etoro_broker.close_position()'s own
    live-testing notes (position gone from portfolio, credit updated,
    immediately after this call returns) -- callers can treat a
    successful return as a confirmed fill, same as Binance testnet
    sells, no polling needed.
    """
    creds = _require_etoro_creds(user_id)

    portfolio_response = requests.get(
        f"{ETORO_API_BASE}/{_etoro_portfolio_path(creds)}",
        headers=_etoro_headers_for_user(creds),
        timeout=25,
    )
    portfolio_response.raise_for_status()
    portfolio = portfolio_response.json().get("clientPortfolio", {})
    position = next(
        (p for p in portfolio.get("positions", []) if str(p.get("positionID")) == str(position_id)),
        None,
    )
    if position is None:
        raise ValueError(f"No open eToro position found with position_id {position_id}.")

    response = requests.post(
        f"{ETORO_API_BASE}/{_etoro_execution_prefix(creds)}/market-close-orders/positions/{position_id}",
        headers=_etoro_headers_for_user(creds),
        json={"instrumentId": position["instrumentID"]},
        timeout=25,
    )
    response.raise_for_status()
    return response.json()


def find_etoro_position_by_ticker_for_user(user_id, ticker):
    """
    FOLLOW-UP 2026-08-26: find this user's open eToro position for a
    project ticker, if any -- used by saas_reconcile_engine.py's
    reconcile_user_etoro_orders() to catch up a SUBMITTED order whose
    buy_etoro_for_user() poll window elapsed before eToro confirmed the
    fill (see that function's docstring; this is the eToro equivalent of
    get_alpaca_order_status_for_user(), just matched by ticker rather
    than a broker order id -- eToro's initial order-POST response does
    include an orderId, but that id isn't persisted anywhere in the SaaS
    order journal today, same "match by symbol, not order id" approach
    etoro_broker.find_position_by_symbol() already established for the
    single-owner bot).

    Returns {"position_id", "open_price", "quantity"} for the first
    matching open position, or None if this user holds no open position
    for this ticker on eToro right now.
    """
    creds = _require_etoro_creds(user_id)
    try:
        instrument_id = _get_etoro_instrument_id_for_user(user_id, creds, ticker)
    except ValueError:
        return None

    response = requests.get(
        f"{ETORO_API_BASE}/{_etoro_portfolio_path(creds)}",
        headers=_etoro_headers_for_user(creds),
        timeout=25,
    )
    response.raise_for_status()
    portfolio = response.json().get("clientPortfolio", {})

    for p in portfolio.get("positions", []):
        if p.get("instrumentID") == instrument_id:
            return {
                "position_id": p.get("positionID"),
                "open_price": p.get("openRate"),
                "quantity": p.get("amount"),
            }

    return None


# ============================================================
# PER-USER OPEN POSITIONS (added 2026-08-27, item #121 follow-up)
# ============================================================
#
# saas_app.py had no way for a user to see their own open positions --
# this is the per-user, multi-broker equivalent of app.py's own
# "Current Positions" section (which reads broker.get_positions()
# directly for the single owner's Alpaca account). Returns a common
# shape across all three brokers so the dashboard can render one table:
#   ticker, quantity, entry_price, current_price,
#   unrealized_pnl, unrealized_pnl_pct, stop_loss, take_profit
# unrealized_pnl / unrealized_pnl_pct may be None where a broker doesn't
# give us enough to compute one honestly (see eToro note below) --
# callers should render None as "--", not 0.
#
# Never raises -- each per-broker helper fails toward an empty list on
# any error, same "never crash the caller" pattern as the rest of this
# file's per-user lookups.


def get_user_open_positions(user_id, broker):
    """Dispatch helper -- get_user_open_positions(user_id, "ALPACA")."""
    broker = broker.upper()
    if broker == "ALPACA":
        return _get_alpaca_open_positions(user_id)
    if broker == "BINANCE":
        return _get_binance_open_positions(user_id)
    if broker == "ETORO":
        return _get_etoro_open_positions(user_id)
    return []


def _get_alpaca_open_positions(user_id):
    """
    Reads straight from Alpaca's own get_all_positions() -- authoritative,
    live, and already computes entry/current/PnL correctly server-side
    (same call app.py's own Current Positions section makes for the
    single owner). No journal lookup needed for the numbers themselves;
    the journal is only consulted for stop_loss/take_profit, which
    Alpaca's position object doesn't carry.
    """
    try:
        client = _require_alpaca_client(user_id)
        positions = client.get_all_positions()
    except Exception:
        return []

    result = []
    for p in positions:
        try:
            entry_order = journal.get_most_recent_filled_buy_for_user(user_id, p.symbol, "ALPACA")
            result.append({
                "ticker": p.symbol,
                "quantity": float(p.qty),
                "entry_price": round(float(p.avg_entry_price), 2),
                "current_price": round(float(p.current_price), 2),
                "unrealized_pnl": round(float(p.unrealized_pl), 2),
                "unrealized_pnl_pct": round(float(p.unrealized_plpc) * 100, 2),
                "stop_loss": entry_order.get("stop_loss") if entry_order else None,
                "take_profit": entry_order.get("take_profit") if entry_order else None,
            })
        except Exception:
            continue
    return result


def _get_binance_open_positions(user_id):
    """
    Sized off the REAL wallet balance (get_user_crypto_held_qty), not the
    journal's filled_quantity -- same lesson as the 2026-08-27 SOL-USD
    exit-sizing fix. A ticker the journal thinks is open but the wallet
    actually holds zero of is silently skipped here rather than shown as
    a ghost position (that's the reconcile_closed_sol_position.py class
    of mismatch; this view should reflect reality, not the journal's
    possibly-stale belief).
    """
    try:
        tickers = journal.list_open_tickers_for_user(user_id, "BINANCE")
    except Exception:
        return []

    result = []
    for ticker in tickers:
        try:
            real_qty = get_user_crypto_held_qty(user_id, ticker)
            if real_qty <= 0:
                continue

            entry_order = journal.get_most_recent_filled_buy_for_user(user_id, ticker, "BINANCE")
            if entry_order is None:
                continue
            entry_price = float(entry_order.get("filled_price") or entry_order.get("price") or 0)

            exchange = _require_binance_exchange(user_id)
            symbol = _to_binance_symbol(ticker)
            current_price = float(exchange.fetch_ticker(symbol)["last"])

            pnl = None
            pnl_pct = None
            if entry_price > 0:
                pnl = round((current_price - entry_price) * real_qty, 2)
                pnl_pct = round((current_price - entry_price) / entry_price * 100, 2)

            result.append({
                "ticker": ticker,
                "quantity": real_qty,
                "entry_price": round(entry_price, 4),
                "current_price": round(current_price, 4),
                "unrealized_pnl": pnl,
                "unrealized_pnl_pct": pnl_pct,
                "stop_loss": entry_order.get("stop_loss"),
                "take_profit": entry_order.get("take_profit"),
            })
        except Exception:
            continue
    return result


def _get_etoro_open_positions(user_id):
    """
    unrealized_pnl is deliberately left as None for eToro: the journal's
    "quantity" for an eToro position is the margin/invested amount
    (etoro_broker.py's own convention -- see buy()), not a share count,
    so (current_price - entry_price) * quantity would be a leveraged CFD
    dollar figure this codebase doesn't have enough information to get
    right (leverage varies by instrument, plus eToro's own fees/overnight
    charges aren't visible here). unrealized_pnl_pct (simple price change)
    IS shown, since that's honest regardless of leverage. Exact $ P&L for
    eToro should be checked in the eToro app itself.
    """
    try:
        tickers = journal.list_open_tickers_for_user(user_id, "ETORO")
    except Exception:
        return []

    result = []
    for ticker in tickers:
        try:
            live = find_etoro_position_by_ticker_for_user(user_id, ticker)
            if live is None:
                # Journal says open, eToro shows no matching position --
                # same reconciliation gap class as the SOL-USD case.
                # Skip rather than show a ghost row.
                continue

            entry_order = journal.get_most_recent_filled_buy_for_user(user_id, ticker, "ETORO")
            entry_price = float(live.get("open_price") or 0)
            amount = float(live.get("quantity") or 0)
            current_price = get_etoro_current_price_for_user(user_id, ticker)

            pnl_pct = None
            if entry_price > 0 and current_price:
                pnl_pct = round((current_price - entry_price) / entry_price * 100, 2)

            result.append({
                "ticker": ticker,
                "quantity": amount,  # margin invested, NOT a share count
                "entry_price": entry_price,
                "current_price": round(current_price, 5) if current_price else None,
                "unrealized_pnl": None,
                "unrealized_pnl_pct": pnl_pct,
                "stop_loss": entry_order.get("stop_loss") if entry_order else None,
                "take_profit": entry_order.get("take_profit") if entry_order else None,
            })
        except Exception:
            continue
    return result
