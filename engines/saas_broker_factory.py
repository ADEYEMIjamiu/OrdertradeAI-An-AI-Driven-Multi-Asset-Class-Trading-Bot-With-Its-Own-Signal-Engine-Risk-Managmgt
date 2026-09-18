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
from etoro_broker.py -- resolve_project_ticker(), _is_leveraged_cfd_ticker()
(renamed from _is_forex_or_commodity_ticker() when INDICES joined as a 5th
CFD asset class -- see that function's docstring), and the
ETORO_LEVERAGE/ETORO_STOP_LOSS_PCT/ETORO_TAKE_PROFIT_PCT/
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

RESOLVED 2026-08-27 (was "KNOWN GAP" here): buy_etoro_for_user() places
the initial order (with a real broker-side fixed stop-loss/take-profit,
same as etoro_broker.py's own buy()) and best-effort upgrades it to a
broker-side TRAILING stop -- there is now also a per-user equivalent of
app.py's apply_etoro_trailing_lock() (the single-owner bot's own
workaround for eToro's trailing stop not actually working as documented
-- see etoro_broker.py's 2026-08-24 comment): see engines/saas_etoro_
trailing_engine.py's apply_etoro_trailing_lock_for_user(), which uses
get_user_etoro_positions()/set_etoro_fixed_stop_loss_for_user() below
and is wired into saas_decision_engine.run_decision_loop_for_user() to
run every scheduler tick. eToro positions in engines/saas_exit_engine.py
were ALSO already resolved (see that file's own 2026-08-26 FOLLOW-UP
note) -- this paragraph was stale, describing a state that no longer
matched the code below it.

SAFETY -- read this before changing paper=True / set_sandbox_mode(True)
below: as of 2026-09-08, Alpaca (task #302), Binance (task #303), and
eToro (task #304) are all wired to the same double-gate design -- see
_alpaca_is_live(), _binance_is_live(), and _etoro_is_live() below. eToro's
is_demo computation (used both directly and via the
_etoro_execution_prefix()/_etoro_positions_prefix()/_etoro_portfolio_path()
helpers) now routes through _etoro_is_live(), which requires BOTH
creds["environment"] == "real" AND allow_live_trading, exactly like
Alpaca/Binance -- the dormant gap flagged here previously (eToro checking
creds["environment"] alone, with no Lock 1 check) is closed.

MT4/5 (task #305, same day) is DIFFERENT from the three above and does
NOT go through this file's is_live()/environment double-gate pattern at
all -- see mt_broker.py's own module docstring LIVE TRADING GATE section
for the full reasoning. Short version: MetaApi has no code-level sandbox
to flip between (unlike paper=True/set_sandbox_mode(True)/a URL prefix
choice) -- a connected MT4/5 account IS whatever the broker says it is.
buy_mt_for_user()/sell_mt_for_user() below are thin pass-throughs with NO
gate of their own; the actual Lock 1 check lives inside mt_broker.py's
execute_buy_by_usd_amount()/execute_sell_close(), verified fresh from
MetaApi's own account_information.type on every single call, not from
any stored/user-chosen flag. Found while implementing #305: before this
fix, ANY user with a connected real-money MT4/5 account had it tradeable
through the AI decision loop with NO Lock 1 check at all (not dormant
like eToro's gap -- live and reachable, though a production DB check
before the fix confirmed zero users had one connected, so nothing was
actually exposed).

All three brokers above are a DOUBLE gate, computed by
_alpaca_is_live()/_binance_is_live()/_etoro_is_live() below, and consulted by every call
that builds a client for that broker in this file -- never duplicate
this check inline elsewhere, always call the one function, so there is
exactly one place this logic can drift. Real execution requires BOTH:
(1) user_settings.allow_live_trading is True (Lock 1, flipped only via
engines/tenant_engine.py's set_live_trading_status(), see that
function's docstring) AND (2) this specific credential's stored
environment is "live" (Lock 2, chosen per-broker at connect time in
saas_app.py's render_broker_connections(), only offered when Lock 1 is
already on). Either gate alone is not enough -- a user who saves live
credentials and then reverts Lock 1 back to demo must NOT keep trading
live; both is_live() functions re-check Lock 1 on every call rather than
trusting a stale UI state, so that revert takes effect immediately,
before the very next order. Any error while reading user_settings
(missing row, DB error) fails CLOSED to paper -- see
_user_has_live_trading_enabled()'s docstring. MT4/5's own Lock 1 check
(in mt_broker.py) follows the identical fail-closed contract even though
it isn't one of these three functions.

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
from requests.adapters import HTTPAdapter
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from engines import tenant_engine as tenant
from engines import saas_order_manager as journal
from engines.broker_error_messages import (
    friendly_broker_error_message,
    broker_error_status,
    LiveTradingNotEnabledError,
)
from etoro_broker import (
    resolve_project_ticker,
    _is_leveraged_cfd_ticker,
    ETORO_LEVERAGE,
    ETORO_STOP_LOSS_PCT,
    ETORO_TAKE_PROFIT_PCT,
    ETORO_USE_TRAILING_STOP,
)
import mt_broker

# FIX 2026-09-02 (post-launch-audit): alpaca-py's RESTClient never sets a
# requests timeout itself -- confirmed via direct read of the installed
# SDK (alpaca/common/rest.py): _request()/_one_request() build the
# **opts dict passed to self._session.request(...) with only headers/
# allow_redirects/params/json, no "timeout" key, and RESTClient.__init__
# has no timeout parameter to set one either. That means every Alpaca
# call in this file (Test Connection AND live order placement) inherits
# `requests`' own default of NO timeout at all -- a stalled connection
# can hang indefinitely, worse than the bounded-but-too-generous timeout
# gap just fixed in mt_broker.py (task #238), since this one has no
# bound whatsoever. eToro's checks already pass an explicit `timeout=`
# to requests.get(); Binance's ccxt client defaults to 10s. Since
# alpaca-py doesn't expose a constructor param for this, the fix is a
# custom HTTPAdapter mounted on the client's own `_session` (a plain
# requests.Session, confirmed a real, accessible attribute on
# RESTClient) that injects a default timeout on every request through
# that session unless the caller explicitly passes one of their own.
_ALPACA_REQUEST_TIMEOUT_SECONDS = 15


class _TimeoutHTTPAdapter(HTTPAdapter):
    def __init__(self, *args, timeout=None, **kwargs):
        self._default_timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self._default_timeout
        return super().send(request, **kwargs)


def _with_request_timeout(client, timeout_seconds=_ALPACA_REQUEST_TIMEOUT_SECONDS):
    """Mounts a default request timeout onto an alpaca-py client's
    underlying requests.Session -- see _ALPACA_REQUEST_TIMEOUT_SECONDS
    above for why this is necessary. Every TradingClient(...) built in
    this file should be wrapped with this."""
    adapter = _TimeoutHTTPAdapter(timeout=timeout_seconds)
    client._session.mount("https://", adapter)
    client._session.mount("http://", adapter)
    return client


# FIX 2026-09-03 (post-launch-audit #257): check_user_alpaca_connection()/
# check_user_binance_connection()/check_user_etoro_connection() (and
# mt_broker.py's check_user_mt_connection_sync(), same contract) used to
# put str(e) -- the raw alpaca-py/ccxt/requests exception text -- straight
# into the "error" field, which saas_app.py then interpolates directly
# into st.error(...) for the user to read verbatim. That leaks internal
# details (raw HTTP status text, account/API internals) and is confusing
# for non-technical users ("APIError: {"code":40110000,"message":"access
# key verification failed"}" instead of a plain "check your API key").
# friendly_broker_error_message() (engines/broker_error_messages.py --
# its own tiny module so mt_broker.py can use the same classifier
# without a circular import, since this file already imports mt_broker)
# classifies the raw exception text into one of a few common, broker-
# agnostic buckets and returns a clean message for the UI. The raw
# exception is still printed server-side (captured by journalctl) so
# nothing is lost for debugging -- it's just no longer shown to the end
# user.


def _user_has_live_trading_enabled(user_id):
    """
    Lock 1 of the two-lock live-trading design -- reads
    user_settings.allow_live_trading fresh on every call (never cached),
    so a revert-to-demo (engines/tenant_engine.py's
    set_live_trading_status()) takes effect on the very next broker call,
    not just the next time some other cache happens to refresh.

    Fails CLOSED (returns False) on any error -- a missing user_settings
    row, a DB hiccup, anything -- because False here means "trade this
    user's Alpaca connection as paper," which is the safe default; the
    alternative (letting an exception propagate) would either crash a
    legitimate paper-mode caller or, worse, risk some future refactor
    treating a caught exception as "assume True." Always fail toward
    paper, never toward live.
    """
    try:
        settings = tenant.get_user_settings(user_id)
        return bool(settings and settings.get("allow_live_trading"))
    except Exception:
        return False


def _alpaca_is_live(user_id, creds):
    """
    THE single source of truth for whether a given Alpaca call should hit
    this user's real account. See the module docstring's SAFETY section
    above for the full reasoning -- both gates below are required, and
    creds["environment"] must be exactly "live" (not "paper", not None,
    not any other value) for the second one to pass. Every Alpaca client
    built in this file MUST route through this function; never inline
    `creds.get("environment") == "live"` anywhere else.
    """
    return creds.get("environment") == "live" and _user_has_live_trading_enabled(user_id)


def check_user_alpaca_connection(user_id):
    """
    Builds a fresh Alpaca TradingClient from this user's OWN stored
    credentials and validates it with a real account call. Paper vs live
    is decided by _alpaca_is_live() (see module docstring SAFETY section)
    -- NOT hardcoded, as of task #302. Mirrors broker.check_broker_
    connection()'s return shape.
    """
    creds = tenant.get_broker_credentials(user_id, "ALPACA")
    if creds is None:
        return {
            "connected": False,
            "error": "No Alpaca credentials saved for this user.",
        }

    try:
        client = _with_request_timeout(TradingClient(
            creds["api_key"],
            creds["api_secret"],
            paper=not _alpaca_is_live(user_id, creds),
        ))
        account = client.get_account()

        return {
            "connected": True,
            "status": "connected",
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
            # FIX 2026-09-03 (#258): "unavailable" (network/timeout/rate-
            # limit -- probably transient, not a bad key) vs "failed"
            # (auth/balance/unknown -- a real problem). See
            # engines/broker_error_messages.broker_error_status()'s
            # docstring for why this distinction exists and matters at
            # saas_app.py's credential-save flow.
            "status": broker_error_status(e),
            "account_status": None,
            "trading_blocked": True,
            "buying_power": 0.0,
            "cash": 0.0,
            "equity": 0.0,
            "error": friendly_broker_error_message("Alpaca", e),
        }


def _binance_is_live(user_id, creds):
    """
    Binance's equivalent of _alpaca_is_live() above -- see that
    function's docstring and the module SAFETY section for the full
    two-lock reasoning, which applies identically here. Task #303
    (2026-09-08): Binance is now the second broker wired to this
    double-gate; set_sandbox_mode(True)/(False) is decided by this
    function's return value, never hardcoded, never inlined elsewhere.
    """
    return creds.get("environment") == "live" and _user_has_live_trading_enabled(user_id)


def check_user_binance_connection(user_id):
    """
    Builds a fresh ccxt Binance exchange instance from this user's OWN
    stored credentials. Testnet vs live (mainnet) is decided by
    _binance_is_live() (see module docstring SAFETY section) -- NOT
    hardcoded, as of task #303. Mirrors binance_broker.check_broker_
    connection()'s return shape.
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
        exchange.set_sandbox_mode(not _binance_is_live(user_id, creds))

        balance = exchange.fetch_balance()
        usdt = balance.get("USDT", {}).get("free", 0)

        return {
            "connected": True,
            "status": "connected",
            "cash": float(usdt),
            "error": None,
        }
    except Exception as e:
        return {
            "connected": False,
            "status": broker_error_status(e),  # see #258 note above check_user_alpaca_connection()
            "cash": 0.0,
            "error": friendly_broker_error_message("Binance", e),
        }


def _etoro_is_live(user_id, creds):
    """
    eToro's equivalent of _alpaca_is_live()/_binance_is_live() above --
    see the module SAFETY section for the full two-lock reasoning, which
    applies identically here. Task #304 (2026-09-08) CLOSES the gap that
    section flagged: every eToro function below used to compute
    is_demo = creds["environment"] != "real" directly, with NO check of
    user_settings.allow_live_trading at all -- meaning if anything ever
    set environment="real" for an eToro credential, orders would go to
    the real account regardless of Lock 1. Nothing in saas_app.py's UI
    has ever offered that choice, so this was dormant, not actually
    exploited -- but it was still a live landmine, not a safe-by-design
    gap. Every is_demo computation in this file's eToro section now
    routes through this function instead of checking creds directly.
    """
    return creds.get("environment") == "real" and _user_has_live_trading_enabled(user_id)


def check_user_etoro_connection(user_id):
    """
    Calls eToro's portfolio endpoint directly with this user's OWN
    stored API key / user key headers (mirrors etoro_broker.py's
    _headers()/_fetch_client_portfolio() pattern) -- does not touch
    etoro_broker.py's global module state at all. Mirrors
    etoro_broker.check_broker_connection()'s return shape. Real vs demo
    is decided by _etoro_is_live() (see module docstring SAFETY section)
    -- NOT by creds["environment"] alone, as of task #304.
    """
    creds = tenant.get_broker_credentials(user_id, "ETORO")
    if creds is None:
        return {
            "connected": False,
            "error": "No eToro credentials saved for this user.",
        }

    is_demo = not _etoro_is_live(user_id, creds)
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
            "status": "connected",
            "account_status": "DEMO" if is_demo else "REAL",
            "cash": credit,
            "equity": credit + unrealized_pnl,
            "error": None,
        }
    except Exception as e:
        return {
            "connected": False,
            "status": broker_error_status(e),  # see #258 note above check_user_alpaca_connection()
            "account_status": None,
            "cash": 0.0,
            "equity": 0.0,
            "error": friendly_broker_error_message("eToro", e),
        }


def check_user_mt_bridge_connection(user_id):
    """
    Thin wrapper around mt_broker.check_user_mt_connection_sync() --
    that function already returns this exact shape (connected/
    account_status/trading_blocked/buying_power/cash/equity/error), see
    mt_broker.py's check_user_mt_connection() docstring: it was built to
    mirror this file's checker return shape from the start (Phase 1,
    2026-09-02). Exists as a real function (not just a dict alias) so
    this file's own docstring/import list stays the single place other
    code looks to find every broker this platform supports.
    """
    return mt_broker.check_user_mt_connection_sync(user_id)


# ============================================================
# KRAKEN (second CRYPTO broker, task #365) -- added to close the gap
# Binance leaves in Canada (exited entirely, May 2023) and the UK (FCA
# blocked new retail sign-ups Oct 2023, no derivatives for existing
# ones) -- both currencies this platform already bills in (CAD/GBP).
#
# CRITICAL DIFFERENCE FROM BINANCE: Kraken has NO public spot sandbox/
# testnet for ordinary retail API keys (confirmed via research before
# building this -- Kraken's spot test environment is "offered for
# qualified clients" only, not a self-serve testnet like Binance's).
# A connected Kraken account is therefore ALWAYS real money -- there is
# no code-level sandbox to flip between the way _binance_is_live() does
# via set_sandbox_mode(). This is the SAME characteristic mt_broker.py
# (MT4/5) has -- see that file's LIVE TRADING GATE section, which this
# mirrors: the only gate is user_settings.allow_live_trading (Lock 1),
# checked fresh on every buy/sell call via _require_kraken_live_
# trading_enabled() below, raising LiveTradingNotEnabledError (already
# handled by saas_decision_engine.py's/saas_exit_engine.py's generic
# except-Exception blocks, same as MT4/5) if it's off. There is no
# Lock 2 (credential environment) for Kraken -- environment is always
# saved as "live" at connect time (see saas_app.py's render_kraken_
# connection()), purely a display label, never consulted for the
# execution gate (consistent with how MT_BRIDGE's stored environment
# is also just a label -- see tenant.update_broker_environment()'s
# docstring).
#
# check_user_kraken_connection() itself is NOT gated by Lock 1 -- same
# as check_user_mt_bridge_connection() -- reading balance/connection
# status is safe regardless of whether live trading is enabled; only
# actually placing/closing an order is gated.
#
# Symbol convention: Kraken's classic strength is direct fiat USD spot
# pairs (BTC/USD, ETH/USD, SOL/USD, etc. -- offered since Kraken's
# inception), unlike Binance which is USDT-denominated. _to_kraken_
# symbol() below converts this project's "TICKER-USD" tickers to
# "TICKER/USD", and balances are read from the "USD" free balance, not
# "USDT". NOT live-verified against a real Kraken account from this
# build session (no network access at build time) -- every tracked
# ticker's exact Kraken pair availability, minimum order size, and the
# clientOrderId param name used for reconciliation below should be
# confirmed via a real Test Connection + a small real order BEFORE
# this is trusted for any live user, same "live-test before trusting"
# discipline every other broker integration in this file got (see
# mt_broker.py's task #231, eToro's task #44).
# ============================================================


def _require_kraken_live_trading_enabled(user_id):
    """
    THE single gate for Kraken order execution -- see the KRAKEN section
    docstring above for why this is a SINGLE gate (Lock 1 only), unlike
    Alpaca/Binance/eToro's double-gate (_alpaca_is_live()/_binance_is_
    live()/_etoro_is_live()). Raises LiveTradingNotEnabledError (caught
    by the same generic except-Exception handling saas_decision_engine.py
    and saas_exit_engine.py already use for MT_BRIDGE's identical error)
    if user_settings.allow_live_trading is not on. Never silently routes
    to a "demo" execution path -- there isn't one for Kraken.
    """
    if not _user_has_live_trading_enabled(user_id):
        raise LiveTradingNotEnabledError(
            f"Kraken account for user {user_id} is real-money by design "
            f"(Kraken has no spot sandbox/testnet for retail API keys) "
            f"but allow_live_trading is not enabled."
        )


def check_user_kraken_connection(user_id):
    """
    Builds a fresh ccxt Kraken exchange instance from this user's OWN
    stored credentials and validates it with a real balance call. No
    sandbox mode (Kraken has none for spot -- see KRAKEN section
    docstring above) and NOT gated by Lock 1 -- a read-only connection
    check is safe regardless of whether live trading is enabled, same
    as check_user_mt_bridge_connection(). Mirrors check_user_binance_
    connection()'s return shape, reading the "USD" free balance instead
    of "USDT" (Kraken's native fiat pair, not Binance's stablecoin one).
    """
    creds = tenant.get_broker_credentials(user_id, "KRAKEN")
    if creds is None:
        return {
            "connected": False,
            "error": "No Kraken credentials saved for this user.",
        }

    try:
        exchange = ccxt.kraken({
            "apiKey": creds["api_key"],
            "secret": creds["api_secret"],
            "enableRateLimit": True,
        })
        balance = exchange.fetch_balance()
        usd = balance.get("USD", {}).get("free", 0)

        return {
            "connected": True,
            "status": "connected",
            "cash": float(usd),
            "error": None,
        }
    except Exception as e:
        return {
            "connected": False,
            "status": broker_error_status(e),  # see #258 note above check_user_alpaca_connection()
            "cash": 0.0,
            "error": friendly_broker_error_message("Kraken", e),
        }


def _require_kraken_exchange(user_id):
    """
    Used by every order-placing/order-status/balance-reading Kraken call
    below. No sandbox mode call -- see KRAKEN section docstring above.
    Real execution is gated separately by _require_kraken_live_trading_
    enabled(), called explicitly by buy_kraken_for_user()/sell_kraken_
    for_user() before this, never inlined here, so a future caller can't
    accidentally build a live-capable client without that check.
    """
    creds = tenant.get_broker_credentials(user_id, "KRAKEN")
    if creds is None:
        raise ValueError("No Kraken credentials saved for this user.")
    return ccxt.kraken({
        "apiKey": creds["api_key"],
        "secret": creds["api_secret"],
        "enableRateLimit": True,
    })


def _to_kraken_symbol(ticker):
    """See KRAKEN section docstring above -- Kraken's native pairs are
    fiat-USD, not USDT, unlike _to_binance_symbol()."""
    return f"{ticker.replace('-USD', '')}/USD"


def buy_kraken_for_user(user_id, ticker, usd_amount, client_order_id=None):
    """
    Per-user Kraken REAL market BUY, sized by dollar amount. Mirrors
    buy_crypto_for_user() (Binance) in shape, with two deliberate
    differences: (1) _require_kraken_live_trading_enabled() is checked
    FIRST, before anything else -- there is no sandbox to fall back to,
    so this must never be reached with live trading off; (2) the
    client_order_id is passed as ccxt's unified 'clientOrderId' params
    key rather than Binance's 'newClientOrderId' -- NOT yet live-
    verified against a real Kraken account (see KRAKEN section
    docstring's live-test caveat) to confirm this is the exact param
    ccxt's Kraken implementation expects and that fetch_order() can look
    it back up the same way get_binance_order_by_client_id_for_user()
    does. Returns (order, price, quantity) same shape as buy_crypto_
    for_user() so saas_decision_engine.py's CRYPTO branch can treat
    both brokers identically once `broker` is threaded through.
    """
    _require_kraken_live_trading_enabled(user_id)
    exchange = _require_kraken_exchange(user_id)
    symbol = _to_kraken_symbol(ticker)

    ticker_data = exchange.fetch_ticker(symbol)
    price = ticker_data["last"]
    quantity = usd_amount / price

    params = {"clientOrderId": client_order_id} if client_order_id else {}
    order = exchange.create_market_buy_order(symbol, quantity, params=params)
    return order, price, quantity


def get_kraken_order_by_client_id_for_user(user_id, ticker, client_order_id):
    """
    Kraken equivalent of get_binance_order_by_client_id_for_user() --
    used by reconcile_user_crypto_orders() (see saas_reconcile_engine.py,
    now broker-parametrized) to resolve a Kraken BUY whose original
    create_market_buy_order() response was lost to a network error. Not
    gated by _require_kraken_live_trading_enabled() -- a lookup is
    read-only and safe to run regardless (mirrors get_binance_order_by_
    client_id_for_user() having no such gate either).

    NOT yet live-verified (see KRAKEN section docstring) -- confirm
    ccxt's Kraken fetchOrder() actually accepts 'clientOrderId' in
    params the same way Binance's fetch_order() accepts
    'origClientOrderId' before relying on this for real duplicate-order
    protection.
    """
    exchange = _require_kraken_exchange(user_id)
    symbol = _to_kraken_symbol(ticker)
    return exchange.fetch_order(None, symbol, params={"clientOrderId": client_order_id})


def sell_kraken_for_user(user_id, ticker, quantity):
    """Per-user Kraken REAL market SELL. Mirrors sell_crypto_for_user()
    (Binance), gated first by _require_kraken_live_trading_enabled() --
    see buy_kraken_for_user()'s docstring for why this check comes
    before anything else for Kraken specifically."""
    _require_kraken_live_trading_enabled(user_id)
    exchange = _require_kraken_exchange(user_id)
    symbol = _to_kraken_symbol(ticker)
    return exchange.create_market_sell_order(symbol, quantity)


def get_user_kraken_held_qty(user_id, ticker):
    """
    Kraken equivalent of get_user_crypto_held_qty() (Binance) -- live
    wallet check used by saas_exit_engine.py to cap a SELL at what's
    actually held, same "trust the wallet over the journal" reasoning
    as that function's docstring. Never raises -- returns 0.0 on any
    failure (no credentials, API error, ticker not held), same
    never-raise contract. Not gated by live-trading-enabled -- a
    read-only wallet query, same reasoning as check_user_kraken_
    connection() above.
    """
    try:
        exchange = _require_kraken_exchange(user_id)
    except Exception:
        return 0.0

    try:
        base_asset = ticker.replace("-USD", "")
        balance = exchange.fetch_balance()
        return float(balance.get("free", {}).get(base_asset, 0) or 0)
    except Exception:
        return 0.0


def _get_kraken_exposure_percent(user_id):
    """Kraken equivalent of _get_binance_exposure_percent() -- values
    only TRACKED_ASSETS at a fresh per-coin Kraken ticker price, reading
    the "USD" free balance instead of "USDT". Never raises -- returns
    0.0 on any failure, same contract as the Binance version."""
    try:
        exchange = _require_kraken_exchange(user_id)
    except Exception:
        return 0.0

    try:
        from data.asset_universe import ASSET_UNIVERSE
        tracked = {t.replace("-USD", "") for t in ASSET_UNIVERSE["CRYPTO"]["symbols"]}

        balance = exchange.fetch_balance()
        free_usd = float(balance.get("USD", {}).get("free", 0) or 0)

        crypto_value = 0.0
        for asset, total_qty in balance.get("total", {}).items():
            if asset not in tracked or not total_qty:
                continue
            try:
                price = float(exchange.fetch_ticker(f"{asset}/USD").get("last") or 0)
            except Exception:
                continue
            crypto_value += float(total_qty) * price

        portfolio_value = free_usd + crypto_value
        if portfolio_value <= 0:
            return 0.0
        return (crypto_value / portfolio_value) * 100
    except Exception:
        return 0.0


# ============================================================
# LUNO (third CRYPTO broker, task #378) -- added to serve customers in
# Nigeria, Kenya, South Africa (and Malaysia/Indonesia) where Binance
# has no functional local-currency on/off-ramp: Binance halted all
# naira services in Nigeria in March 2024 amid an ongoing dispute with
# the CBN, on top of the same regulatory-exit pattern that motivated
# Kraken (task #365) for Canada/UK. Luno is ccxt-supported and already
# licensed/pursuing licensing across these specific markets, letting
# this platform close a real regional gap without a custom non-ccxt
# connector (the Nigeria-SEC-licensed alternatives, Quidax/Busha,
# would need one -- see task #378's research).
#
# CRITICAL DIFFERENCE FROM BOTH BINANCE AND KRAKEN: Luno's ccxt adapter
# has no confirmed sandbox/testnet support (unlike Binance's real
# testnet, and same absence as Kraken's spot API -- see KRAKEN section
# above) -- treated the same conservative way: NO code-level sandbox,
# single gate (Lock 1 only, via _require_luno_live_trading_enabled()
# below), same as Kraken and MT4/5. If ccxt's Luno sandbox support is
# later confirmed, this can be upgraded to a double gate like Binance's
# -- until then, assuming "always real money" is the safe default, not
# the risky one.
#
# CRITICAL DIFFERENCE FROM KRAKEN: Kraken quotes everything in USD, so
# _to_kraken_symbol()/its balance checks hardcode "USD" outright. Luno
# instead quotes in whatever LOCAL FIAT CURRENCY the user's own Luno
# account is denominated in (confirmed via research: XBTZAR (South
# Africa) and XBTMYR (Malaysia) exist as native pairs; XBTNGN, XBTIDR,
# XBTEUR, XBTGBP follow the same pattern but weren't individually
# confirmed live before this build -- verify via a real Test
# Connection + exchange.load_markets() before trusting a specific pair
# for a specific user). This platform's tracked asset universe,
# balance math, and position sizing are all USD-denominated throughout
# (see ASSET_UNIVERSE, calculate_trade_amount()) -- rather than thread
# a second currency through every caller, this section keeps the SAME
# usd_amount-in/USD-price-out contract as every other broker in this
# file end to end -- not just at the buy_luno_for_user() call boundary,
# but for every price this section ever hands back (check_user_luno_
# connection()'s cash, buy_luno_for_user()'s fill price, and
# _get_luno_open_positions()'s current_price are all converted to USD
# via a live FX rate, _get_usd_fx_rate() below, before being returned),
# so every downstream consumer (saas_position_lifecycle_engine.py's
# break-even/partial-profit % math, saas_performance_engine.py's $ P&L,
# saas_app.py's My Positions/Performance views) works unmodified,
# exactly as if this were another USD-quoted broker. The FX rate itself
# comes from a free, no-auth public API -- a genuinely new kind of
# external dependency for this file (every other broker/exchange here
# already prices in a currency this platform understands natively) --
# if it can't be fetched, every function below fails CLOSED (0.0 cash /
# 0.0 exposure / a blocked buy / a skipped position row), the same "a
# bad external dependency blocks trading, it never silently mis-sizes
# or mis-reports a real number" discipline as the rest of this file's
# never-raise/fail-closed contracts. NOT yet live-verified against a
# real Luno account (no network access at build time) -- confirm actual
# pair availability, minimum order size, and the clientOrderId-
# equivalent param name via a real Test Connection + a small real order
# before trusting this for any live user, same discipline Kraken's
# section above calls for.
#
# The user's own Luno account currency is stored in this credential's
# `extra` field (tenant.save_broker_credentials()'s existing generic
# third-secret slot, repurposed here to hold a plain currency code
# like "NGN"/"ZAR"/"KES"/"MYR" rather than a secret -- chosen at
# connect time in saas_app.py's render_luno_connection(), never
# guessed there). The "ZAR" fallback below (Luno's original, most
# liquid market) is a last resort only, for a credential saved before
# `extra` was ever set -- every current connect path always sets it.
# ============================================================


_LUNO_FX_CACHE = {}  # {currency_code: (rate, fetched_at_epoch_seconds)}
_LUNO_FX_CACHE_TTL_SECONDS = 300  # long enough that one decision-loop
# tick's balance + sizing + exposure calls for one user don't each hit
# the public FX API separately; short enough that a real FX move is
# reflected within minutes.


def _get_usd_fx_rate(currency_code):
    """
    Units of `currency_code` per 1 USD (e.g. ~1550 for NGN), used to
    convert Luno's local-currency balances/order sizes to/from this
    platform's USD-denominated sizing math. Uses the free, no-API-key
    open.er-api.com endpoint (mid-market rates, updated daily) -- this
    is an approximation, not the exact rate Luno itself would apply on
    a real trade. That is an accepted, disclosed limitation (see LUNO
    section docstring above), not a silent one.

    Cached per currency for _LUNO_FX_CACHE_TTL_SECONDS to avoid hitting
    the public API on every call. Returns None on ANY failure (network
    error, unexpected response shape, unknown currency code) -- every
    caller below MUST treat None as "cannot safely convert" and fail
    closed, never assume a 1:1 rate.
    """
    currency_code = (currency_code or "").upper()
    cached = _LUNO_FX_CACHE.get(currency_code)
    if cached and (time.time() - cached[1]) < _LUNO_FX_CACHE_TTL_SECONDS:
        return cached[0]

    try:
        resp = requests.get("https://open.er-api.com/v6/latest/USD", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        rate = data.get("rates", {}).get(currency_code)
        if not rate:
            return None
        rate = float(rate)
        _LUNO_FX_CACHE[currency_code] = (rate, time.time())
        return rate
    except Exception:
        return None


def _get_user_luno_quote_currency(creds):
    """This credential's local account currency (see LUNO section
    docstring) -- "ZAR" fallback is a last resort only, see above."""
    return (creds.get("extra") or "ZAR").upper()


def _require_luno_live_trading_enabled(user_id):
    """
    THE single gate for Luno order execution -- see the LUNO section
    docstring above for why this is a SINGLE gate (Lock 1 only), same
    reasoning as _require_kraken_live_trading_enabled(). Raises
    LiveTradingNotEnabledError (caught by the same generic except-
    Exception handling saas_decision_engine.py/saas_exit_engine.py
    already use for MT_BRIDGE/Kraken) if user_settings.allow_live_
    trading is not on.
    """
    if not _user_has_live_trading_enabled(user_id):
        raise LiveTradingNotEnabledError(
            f"Luno account for user {user_id} is real-money by design "
            f"(no confirmed sandbox/testnet for Luno's ccxt adapter) "
            f"but allow_live_trading is not enabled."
        )


def check_user_luno_connection(user_id):
    """
    Builds a fresh ccxt Luno exchange instance from this user's OWN
    stored credentials and validates it with a real balance call.
    Mirrors check_user_kraken_connection()'s shape, but reads the
    user's OWN local quote currency (not a hardcoded "USD") and
    converts that free balance to a USD-equivalent "cash" figure via
    _get_usd_fx_rate() so it composes with the rest of this platform's
    USD-denominated balance/sizing math (see LUNO section docstring).
    If the FX rate can't be fetched, fails CLOSED -- reports connected
    but cash=0.0 with an explanatory error, rather than showing a
    number in the wrong currency as if it were USD.
    """
    creds = tenant.get_broker_credentials(user_id, "LUNO")
    if creds is None:
        return {
            "connected": False,
            "error": "No Luno credentials saved for this user.",
        }

    quote_currency = _get_user_luno_quote_currency(creds)

    try:
        exchange = ccxt.luno({
            "apiKey": creds["api_key"],
            "secret": creds["api_secret"],
            "enableRateLimit": True,
        })
        balance = exchange.fetch_balance()
        local_cash = float(balance.get(quote_currency, {}).get("free", 0) or 0)

        fx_rate = _get_usd_fx_rate(quote_currency)
        if fx_rate is None:
            return {
                "connected": True,
                "status": "connected",
                "cash": 0.0,
                "error": (
                    f"Connected, but could not fetch a live {quote_currency}/USD "
                    f"rate to show your balance -- try again shortly."
                ),
            }

        return {
            "connected": True,
            "status": "connected",
            "cash": round(local_cash / fx_rate, 2),
            "error": None,
        }
    except Exception as e:
        return {
            "connected": False,
            "status": broker_error_status(e),  # see #258 note above check_user_alpaca_connection()
            "cash": 0.0,
            "error": friendly_broker_error_message("Luno", e),
        }


def _require_luno_exchange(user_id):
    """
    Used by every order-placing/order-status/balance-reading Luno call
    below. Returns (exchange, quote_currency) since -- unlike Kraken's
    fixed USD -- Luno's quote currency is per-user (see LUNO section
    docstring). Real execution is gated separately by _require_luno_
    live_trading_enabled(), called explicitly by buy_luno_for_user()/
    sell_luno_for_user() before this, never inlined here.
    """
    creds = tenant.get_broker_credentials(user_id, "LUNO")
    if creds is None:
        raise ValueError("No Luno credentials saved for this user.")
    exchange = ccxt.luno({
        "apiKey": creds["api_key"],
        "secret": creds["api_secret"],
        "enableRateLimit": True,
    })
    return exchange, _get_user_luno_quote_currency(creds)


def _to_luno_symbol(ticker, quote_currency):
    """See LUNO section docstring above -- Luno's native pairs are
    quoted in the user's own local fiat, not uniformly USD like
    _to_kraken_symbol()."""
    return f"{ticker.replace('-USD', '')}/{quote_currency}"


def buy_luno_for_user(user_id, ticker, usd_amount, client_order_id=None):
    """
    Per-user Luno REAL market BUY, sized by dollar amount -- SAME
    usd_amount-in/USD-price-out contract as buy_kraken_for_user()/
    buy_crypto_for_user() so saas_decision_engine.py's CRYPTO branch
    doesn't need Luno-specific sizing logic AND so every downstream
    consumer of the journaled filled_price (saas_position_lifecycle_
    engine.py's break-even/partial-profit % math, saas_performance_
    engine.py's $ P&L) keeps working unmodified. Converts usd_amount to
    the user's local quote currency via _get_usd_fx_rate() before
    placing the order (Luno's own API only understands its native fiat
    amount, not USD -- see LUNO section docstring), then converts the
    fill price BACK to USD before returning it -- the local price is
    used only internally, to size the order correctly against Luno's
    own order book. Raises if the FX rate can't be fetched -- refusing
    to guess a conversion for a REAL order is the only safe option here
    (unlike the read-only balance check above, which can fail closed to
    a merely-uninformative 0.0).
    """
    _require_luno_live_trading_enabled(user_id)
    exchange, quote_currency = _require_luno_exchange(user_id)

    fx_rate = _get_usd_fx_rate(quote_currency)
    if fx_rate is None:
        raise ValueError(
            f"Could not fetch a live {quote_currency}/USD rate -- "
            f"refusing to size a real Luno order without it."
        )
    local_amount = usd_amount * fx_rate

    symbol = _to_luno_symbol(ticker, quote_currency)
    ticker_data = exchange.fetch_ticker(symbol)
    local_price = ticker_data["last"]
    quantity = local_amount / local_price

    params = {"clientOrderId": client_order_id} if client_order_id else {}
    order = exchange.create_market_buy_order(symbol, quantity, params=params)
    usd_price = local_price / fx_rate
    return order, usd_price, quantity


def get_luno_order_by_client_id_for_user(user_id, ticker, client_order_id):
    """
    Luno equivalent of get_kraken_order_by_client_id_for_user() -- used
    by reconcile_user_crypto_orders() to resolve a Luno BUY whose
    original create_market_buy_order() response was lost to a network
    error. Not gated by _require_luno_live_trading_enabled() -- a
    lookup is read-only and safe regardless.

    Returns ccxt's order dict with "average"/"price" converted to USD
    (same currency-consistency reasoning as buy_luno_for_user() --
    reconcile_user_crypto_orders() journals whichever of those two
    fields ccxt populates directly as filled_price, with no broker-
    specific handling of its own, so the conversion has to happen here
    rather than there). Raises (caught by reconcile_user_crypto_orders()'s
    existing generic except-Exception handling, same as any other
    lookup failure) if the FX rate can't be fetched -- left for a later
    pass to retry rather than journaling an unconverted local-currency
    number as if it were USD.

    NOT yet live-verified -- confirm ccxt's Luno fetchOrder() actually
    accepts 'clientOrderId' in params the same way Kraken's is assumed
    to (itself also unverified, see KRAKEN section) before relying on
    this for real duplicate-order protection.
    """
    exchange, quote_currency = _require_luno_exchange(user_id)
    symbol = _to_luno_symbol(ticker, quote_currency)
    order = exchange.fetch_order(None, symbol, params={"clientOrderId": client_order_id})

    fx_rate = _get_usd_fx_rate(quote_currency)
    if fx_rate is None:
        raise ValueError(
            f"Could not fetch a live {quote_currency}/USD rate -- "
            f"refusing to reconcile a Luno order without it."
        )
    order = dict(order)
    for price_field in ("average", "price"):
        if order.get(price_field):
            order[price_field] = float(order[price_field]) / fx_rate
    return order


def sell_luno_for_user(user_id, ticker, quantity):
    """Per-user Luno REAL market SELL. Mirrors sell_kraken_for_user() --
    gated first by _require_luno_live_trading_enabled(). quantity is
    always in the base crypto asset (e.g. XBT), same as every other
    broker's sell_*_for_user() -- no currency conversion needed here,
    only buy-side sizing (usd_amount -> local_amount) needs the FX
    rate."""
    _require_luno_live_trading_enabled(user_id)
    exchange, quote_currency = _require_luno_exchange(user_id)
    symbol = _to_luno_symbol(ticker, quote_currency)
    return exchange.create_market_sell_order(symbol, quantity)


def get_user_luno_held_qty(user_id, ticker):
    """
    Luno equivalent of get_user_kraken_held_qty() -- live wallet check
    used by saas_exit_engine.py to cap a SELL at what's actually held.
    Never raises -- returns 0.0 on any failure, same never-raise
    contract. Not gated by live-trading-enabled -- read-only.
    """
    try:
        exchange, _quote_currency = _require_luno_exchange(user_id)
    except Exception:
        return 0.0

    try:
        base_asset = ticker.replace("-USD", "")
        balance = exchange.fetch_balance()
        return float(balance.get("free", {}).get(base_asset, 0) or 0)
    except Exception:
        return 0.0


def _get_luno_exposure_percent(user_id):
    """
    Luno equivalent of _get_kraken_exposure_percent() -- values only
    TRACKED_ASSETS at a fresh per-coin Luno ticker price, entirely in
    the user's own local currency. The invested/portfolio ratio is
    currency-independent as long as both sides use the same currency
    (which they do here), so -- unlike the balance/buy paths above --
    no FX conversion is needed for this one number. Never raises --
    returns 0.0 on any failure, same contract as the Kraken/Binance
    versions.
    """
    try:
        exchange, quote_currency = _require_luno_exchange(user_id)
    except Exception:
        return 0.0

    try:
        from data.asset_universe import ASSET_UNIVERSE
        tracked = {t.replace("-USD", "") for t in ASSET_UNIVERSE["CRYPTO"]["symbols"]}

        balance = exchange.fetch_balance()
        free_local = float(balance.get(quote_currency, {}).get("free", 0) or 0)

        crypto_value_local = 0.0
        for asset, total_qty in balance.get("total", {}).items():
            if asset not in tracked or not total_qty:
                continue
            try:
                price = float(exchange.fetch_ticker(f"{asset}/{quote_currency}").get("last") or 0)
            except Exception:
                continue
            crypto_value_local += float(total_qty) * price

        portfolio_value_local = free_local + crypto_value_local
        if portfolio_value_local <= 0:
            return 0.0
        return (crypto_value_local / portfolio_value_local) * 100
    except Exception:
        return 0.0


_CHECKERS = {
    "ALPACA": check_user_alpaca_connection,
    "BINANCE": check_user_binance_connection,
    "KRAKEN": check_user_kraken_connection,
    "LUNO": check_user_luno_connection,
    "ETORO": check_user_etoro_connection,
    "MT_BRIDGE": check_user_mt_bridge_connection,
}


def check_user_broker_connection(user_id, broker):
    """Dispatch helper -- check_user_broker_connection(user_id, "ALPACA")."""
    checker = _CHECKERS.get(broker.upper())
    if checker is None:
        return {"connected": False, "error": f"Unknown broker: {broker}"}
    return checker(user_id)


def get_user_account_balance(user_id, asset_class, broker=None):
    """
    Real, current spendable balance for this user's own broker account,
    for whichever broker owns this asset class. Mirrors risk_engine.
    get_account_balance()'s per-asset-class dispatch and never-raise
    contract, but reads from THIS user's own credentials via the
    checkers above instead of the single owner's global broker.py/
    binance_broker.py. Used by the per-user decision loop
    (saas_decision_engine.py) to size trades with calculate_trade_amount().

    US_STOCKS (Alpaca), CRYPTO (Binance or, as of task #365, Kraken), and
    -- as of the 2026-08-26 eToro follow-up -- FOREX/COMMODITIES (eToro)
    are all wired here.

    FOLLOW-UP 2026-09-02: FOREX/COMMODITIES can now also be served by
    MT_BRIDGE (MT4/5 via MetaApi) instead of ETORO -- since a given user
    only ever has ONE of the two connected for a given asset class (see
    saas_decision_engine.py's per-user broker preference), this needs an
    explicit `broker` argument rather than re-deriving it, to avoid
    silently checking the wrong one. `broker=None` preserves the exact
    old behavior (always ETORO for FOREX/COMMODITIES) for any caller that
    hasn't been updated to pass it explicitly.

    FOLLOW-UP 2026-09-15 (task #365): CRYPTO gained the exact same
    two-broker shape as FOREX/COMMODITIES once Kraken was added --
    `broker=None` preserves old behavior (always BINANCE) for any
    caller not yet updated; saas_decision_engine.py's _resolve_broker_
    for_asset_class() always passes it explicitly now.
    """
    if asset_class == "CRYPTO":
        if broker == "KRAKEN":
            result = check_user_kraken_connection(user_id)
            if not result.get("connected"):
                return 0.0
            return float(result.get("cash", 0) or 0)
        if broker == "LUNO":
            result = check_user_luno_connection(user_id)
            if not result.get("connected"):
                return 0.0
            # Already USD-converted by check_user_luno_connection() --
            # see LUNO section docstring.
            return float(result.get("cash", 0) or 0)
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

    if asset_class in ("FOREX", "COMMODITIES", "INDICES"):
        # INDICES (task #394) joined this branch on 2026-09-18 -- same
        # eToro/MT_BRIDGE two-broker shape as FOREX/COMMODITIES, no new
        # code needed beyond adding it to this tuple.
        if broker == "MT_BRIDGE":
            result = check_user_mt_bridge_connection(user_id)
            if not result.get("connected"):
                return 0.0
            # buying_power here is MetaApi's freeMargin -- how much of
            # this account's own capital is actually available to open a
            # NEW position (same reasoning as Alpaca's buying_power
            # above), not the raw balance, which can be fully tied up in
            # existing positions' margin.
            return float(result.get("buying_power", 0) or 0)

        result = check_user_etoro_connection(user_id)
        if not result.get("connected"):
            return 0.0
        # "cash" here is eToro's "credit" field (see check_user_etoro_
        # connection()'s docstring) -- the same balance FOREX,
        # COMMODITIES, and now INDICES all draw from, since they share
        # one eToro account per user rather than separate sub-balances.
        return float(result.get("cash", 0) or 0)

    return 0.0


def get_user_exposure_percent(user_id, asset_class, broker=None):
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

    FOLLOW-UP 2026-09-02: same `broker` argument as
    get_user_account_balance() above, for the same reason -- FOREX/
    COMMODITIES can now be served by MT_BRIDGE instead of ETORO, and
    which one applies must come from the caller (saas_decision_engine.py
    already knows which broker this user has connected for this asset
    class), not be re-derived here. `broker=None` preserves old behavior.
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
        if broker == "KRAKEN":
            return _get_kraken_exposure_percent(user_id)
        if broker == "LUNO":
            return _get_luno_exposure_percent(user_id)
        return _get_binance_exposure_percent(user_id)

    if asset_class in ("FOREX", "COMMODITIES", "INDICES"):
        # INDICES joined this branch 2026-09-18 -- see the matching
        # get_user_account_balance() branch above for the reasoning.
        if broker == "MT_BRIDGE":
            return _get_mt_bridge_exposure_percent(user_id)
        return _get_etoro_exposure_percent(user_id)

    return 0.0


def _get_mt_bridge_exposure_percent(user_id):
    """
    Same invested=equity-cash approach as the US_STOCKS branch above
    (mirrors risk_engine.get_exposure_percent()'s LIVE_TRADING math) --
    MetaApi's own account info already gives us both equity and
    freeMargin directly (see check_user_mt_bridge_connection()), no need
    to sum individual positions' margin the way _get_etoro_exposure_
    percent() has to (eToro's connection check doesn't return per-
    position margin, MetaApi's does via freeMargin at the account level).
    """
    result = check_user_mt_bridge_connection(user_id)
    if not result.get("connected"):
        return 0.0
    equity = float(result.get("equity", 0) or 0)
    free_margin = float(result.get("buying_power", 0) or 0)
    if equity <= 0:
        return 0.0
    invested = max(equity - free_margin, 0.0)
    return (invested / equity) * 100


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

    is_demo = not _etoro_is_live(user_id, creds)
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
    """
    Used by every order-placing/order-status/position-reading Alpaca call
    in this file (buy_stock_for_user(), sell_stock_for_user(),
    get_alpaca_order_status_for_user(), _get_alpaca_open_positions()).
    paper is decided by _alpaca_is_live() -- see module docstring SAFETY
    section -- so a live BUY/SELL only ever reaches Alpaca's real
    endpoint when both Lock 1 (account-level allow_live_trading) and
    Lock 2 (this credential's stored environment) agree, re-checked fresh
    on every single call, not just at connect time.
    """
    creds = tenant.get_broker_credentials(user_id, "ALPACA")
    if creds is None:
        raise ValueError("No Alpaca credentials saved for this user.")
    return _with_request_timeout(TradingClient(
        creds["api_key"], creds["api_secret"], paper=not _alpaca_is_live(user_id, creds)
    ))


def _require_binance_exchange(user_id):
    """
    Used by every order-placing/order-status/balance-reading Binance call
    in this file (buy_crypto_for_user(), sell_crypto_for_user(),
    get_binance_order_by_client_id_for_user(), get_user_crypto_held_qty(),
    _get_binance_open_positions(), _get_binance_exposure_percent()).
    Sandbox mode is decided by _binance_is_live() -- see module docstring
    SAFETY section -- so a live BUY/SELL only ever reaches Binance's real
    mainnet endpoint when both Lock 1 and Lock 2 agree, re-checked fresh
    on every single call.
    """
    creds = tenant.get_broker_credentials(user_id, "BINANCE")
    if creds is None:
        raise ValueError("No Binance credentials saved for this user.")
    exchange = ccxt.binance({
        "apiKey": creds["api_key"],
        "secret": creds["api_secret"],
        "enableRateLimit": True,
    })
    exchange.set_sandbox_mode(not _binance_is_live(user_id, creds))
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


def buy_crypto_for_user(user_id, ticker, usd_amount, client_order_id=None):
    """
    Per-user Binance testnet market BUY, sized by dollar amount. Mirrors
    binance_broker.py's buy_crypto(), against THIS user's own testnet
    account. Returns (order, price, quantity) same as the original.

    FIX 2026-09-02 (post-launch-audit CRITICAL finding): accepts an
    optional client_order_id now, forwarded to Binance as
    newClientOrderId (confirmed live via the installed ccxt SDK's
    binance.py: create_order() honors 'newClientOrderId' in params, same
    idiom Binance's own API supports for exactly this purpose). If this
    call raises -- e.g. a network timeout or dropped connection -- AFTER
    Binance actually executed the order, the caller has NO order id from
    the lost response to check later... unless it generated one itself
    BEFORE calling this, which is exactly what saas_decision_engine.py's
    CRYPTO branch now does. See get_binance_order_by_client_id_for_user()
    below and saas_reconcile_engine.py's reconcile_user_crypto_orders()
    for how that id is used afterward to find out definitively whether
    the order happened. Without a caller-supplied id, a lost response
    would leave literally no way to correlate back to a real order at
    all -- this was the actual root cause of "no crypto reconciliation"
    (a SUBMITTED-but-response-lost order previously had nothing to key
    a follow-up lookup on).
    """
    exchange = _require_binance_exchange(user_id)
    symbol = _to_binance_symbol(ticker)

    ticker_data = exchange.fetch_ticker(symbol)
    price = ticker_data["last"]
    quantity = usd_amount / price

    params = {"newClientOrderId": client_order_id} if client_order_id else {}
    order = exchange.create_market_buy_order(symbol, quantity, params=params)
    return order, price, quantity


def get_binance_order_by_client_id_for_user(user_id, ticker, client_order_id):
    """
    Looks up a previously-placed Binance order by the CLIENT-generated
    id passed to buy_crypto_for_user() above -- confirmed live via the
    installed ccxt SDK's binance.py: fetch_order() looks up by
    'origClientOrderId' in params when present, ignoring the `id`
    positional argument entirely in that case (so passing id=None here
    is safe and intentional, not an oversight).

    This is the ONLY reliable way to find out what actually happened to
    an order whose original create_market_buy_order() response was lost
    to a network error -- see reconcile_user_crypto_orders() in
    saas_reconcile_engine.py, the sole caller.

    Raises ccxt.OrderNotFound specifically (not caught here -- callers
    should catch it themselves) when Binance has never seen this client
    order id at all -- i.e. the ORIGINAL buy_crypto_for_user() call
    failed before Binance ever received/processed it, a DEFINITIVE
    "this never happened" signal the caller can act on immediately
    rather than waiting/guessing. Any OTHER exception (network/auth/
    etc.) means the lookup itself failed -- genuinely unknown, not a
    negative result -- and should be retried on a future pass instead.
    """
    exchange = _require_binance_exchange(user_id)
    symbol = _to_binance_symbol(ticker)
    return exchange.fetch_order(None, symbol, params={"origClientOrderId": client_order_id})


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


def _etoro_execution_prefix(user_id, creds):
    """Real vs demo decided by _etoro_is_live() -- see module docstring
    SAFETY section -- NOT by creds["environment"] alone, as of task #304."""
    is_demo = not _etoro_is_live(user_id, creds)
    return "trading/execution/demo" if is_demo else "trading/execution"


def _etoro_positions_prefix(user_id, creds):
    """Real vs demo decided by _etoro_is_live() -- see module docstring
    SAFETY section -- NOT by creds["environment"] alone, as of task #304."""
    is_demo = not _etoro_is_live(user_id, creds)
    return "trading/demo" if is_demo else "trading/real"


def _etoro_portfolio_path(user_id, creds):
    """Real vs demo decided by _etoro_is_live() -- see module docstring
    SAFETY section -- NOT by creds["environment"] alone, as of task #304."""
    is_demo = not _etoro_is_live(user_id, creds)
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
    to compute stopLossRate/takeProfitRate for leveraged orders.

    FIX 2026-09-10: was timeout=10 -- every other eToro API call in this
    file (and in etoro_broker.py) was bumped from 10s to 25s back when
    #51 diagnosed eToro's rates endpoint occasionally taking longer than
    10s to respond, but this one call was missed. It's what
    saas_etoro_trailing_engine.py calls to price a new trailing-stop
    level, so a slow response here was silently skipping that day's
    ratchet-up instead of erroring loudly -- caught live 2026-09-10 (a
    single SILVER "Read timed out (read timeout=10)" in the scheduler
    log). Matched to the same 25s used everywhere else.
    """
    creds = _require_etoro_creds(user_id)
    instrument_id = _get_etoro_instrument_id_for_user(user_id, creds, ticker)

    response = requests.get(
        f"{ETORO_API_BASE}/market-data/instruments/rates",
        params={"instrumentIds": instrument_id},
        headers=_etoro_headers_for_user(creds),
        timeout=25,
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
        f"{ETORO_EXECUTION_BASE_V2}/{_etoro_positions_prefix(user_id, creds)}/positions/{position_id}",
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
    is_leveraged_cfd = _is_leveraged_cfd_ticker(ticker)

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
        f"{ETORO_EXECUTION_BASE_V2}/{_etoro_execution_prefix(user_id, creds)}/orders",
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
            f"{ETORO_API_BASE}/{_etoro_portfolio_path(user_id, creds)}",
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
        f"{ETORO_API_BASE}/{_etoro_portfolio_path(user_id, creds)}",
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
        f"{ETORO_API_BASE}/{_etoro_execution_prefix(user_id, creds)}/market-close-orders/positions/{position_id}",
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
        f"{ETORO_API_BASE}/{_etoro_portfolio_path(user_id, creds)}",
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


def get_user_etoro_positions(user_id):
    """
    Per-user equivalent of etoro_broker.get_positions() -- full list of
    this user's open eToro positions, with enough detail (position_id/
    direction/open_price/stop_loss_rate/take_profit_rate) for
    engines/saas_etoro_trailing_engine.py's apply_etoro_trailing_lock_
    for_user() to ratchet stops the same way the single-owner bot's own
    apply_etoro_trailing_lock() (app.py) already does. Field names/shape
    deliberately mirror that function exactly -- see its docstring for
    why the raw eToro API fields differ from eToro's own documented
    field names (instrumentID not instrumentName, amount not
    investedAmount, positionID not positionId, etc).
    """
    creds = _require_etoro_creds(user_id)

    response = requests.get(
        f"{ETORO_API_BASE}/{_etoro_portfolio_path(user_id, creds)}",
        headers=_etoro_headers_for_user(creds),
        timeout=25,
    )
    response.raise_for_status()
    portfolio = response.json().get("clientPortfolio", {})
    raw_positions = portfolio.get("positions", [])

    catalog = _load_etoro_catalog_for_user(user_id, creds)
    id_to_symbol = {v: k for k, v in catalog.items()}

    positions = []
    for p in raw_positions:
        positions.append({
            "symbol": id_to_symbol.get(p.get("instrumentID")),
            "qty": float(p.get("amount") or 0),
            "position_id": p.get("positionID"),
            "direction": "LONG" if p.get("isBuy") else "SHORT",
            "open_price": p.get("openRate"),
            "stop_loss_rate": p.get("stopLossRate"),
            "take_profit_rate": p.get("takeProfitRate"),
        })
    return positions


def set_etoro_fixed_stop_loss_for_user(user_id, position_id, stop_loss_rate, take_profit_rate=None):
    """
    Per-user equivalent of etoro_broker.set_fixed_stop_loss() -- PATCH an
    updated FIXED (never "trailing" -- see that function's docstring for
    why eToro's own trailing flag isn't trusted) stop-loss level to an
    already-open position. Used by apply_etoro_trailing_lock_for_user()
    to push a ratcheted stop as price makes new highs. take_profit_rate
    is passed through unchanged, same reasoning as the single-owner
    version: the take-profit stays in place as a hard ceiling regardless
    of how far the stop-loss has been ratcheted.

    Callers must wrap this in try/except -- a failed PATCH here must
    never be allowed to look like a successful update.
    """
    creds = _require_etoro_creds(user_id)
    payload = {"stopLossType": "fixed", "stopLossRate": stop_loss_rate}
    if take_profit_rate is not None:
        payload["takeProfitRate"] = take_profit_rate

    response = requests.patch(
        f"{ETORO_EXECUTION_BASE_V2}/{_etoro_positions_prefix(user_id, creds)}/positions/{position_id}",
        headers=_etoro_headers_for_user(creds),
        json=payload,
        timeout=25,
    )
    response.raise_for_status()
    return response.json()


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
    if broker == "KRAKEN":
        return _get_kraken_open_positions(user_id)
    if broker == "LUNO":
        return _get_luno_open_positions(user_id)
    if broker == "ETORO":
        return _get_etoro_open_positions(user_id)
    if broker == "MT_BRIDGE":
        return _get_mt_bridge_open_positions(user_id)
    return []


def get_user_open_positions_or_error(user_id, broker):
    """
    FIX 2026-09-03 (post-launch-audit #263: "broker fetch failures fail
    silently in My Positions"): get_user_open_positions() above (and
    every _get_*_open_positions() helper it dispatches to) deliberately
    never raises -- on a genuine fetch failure (expired/revoked
    credentials, the broker's API being down, a network error) it just
    returns [], the EXACT same shape as "you really do have zero open
    positions right now". That never-raise contract is correct and
    load-bearing for this function's other callers -- engines/saas_exit_
    engine.py's exit-protection sweep (line ~275) and engines/saas_admin_
    engine.py's aggregate-exposure view both need "one broker's fetch
    hiccuped" to fail toward an empty list, not crash or skip a whole
    user -- so get_user_open_positions() itself is deliberately left
    untouched by this fix.

    But saas_app.py's "My Positions" display is exactly the case where
    that ambiguity is a real problem: a user whose Alpaca API key just
    expired would see a blank "No open positions right now" card --
    indistinguishable from genuinely being flat -- with nothing telling
    them their broker connection actually needs attention. If they've
    forgotten they're holding something, that silence is actively
    misleading, not just unhelpful.

    Returns (positions, error). error is None whenever the result can be
    trusted: either real positions came back (which only happens if the
    broker call actually succeeded), or -- for the empty-list case --  an
    explicit check_user_broker_connection() call confirms the broker
    really is reachable and genuinely has nothing open. error is a
    clean, user-safe string (built by check_user_broker_connection() via
    engines/broker_error_messages.py, same classifier #257/#258 already
    wired through every checker) only in the one ambiguous case: an
    empty list AND the broker is not actually reachable right now.

    The connectivity check only runs when the position list came back
    empty -- not on every call -- so a user who already has positions
    (the common case) doesn't pay for a second round-trip to the broker
    on every My Positions render.
    """
    positions = get_user_open_positions(user_id, broker)
    if positions:
        return positions, None

    check = check_user_broker_connection(user_id, broker)
    if check.get("connected"):
        return positions, None  # genuinely zero positions
    if check.get("status") == "deploying":
        # MT4/5 first-connect can legitimately take minutes (task #238)
        # -- not a failure, just not ready yet. Don't alarm the user
        # over something already expected and already surfaced by the
        # Broker Connections section above this one.
        return positions, None
    return positions, check.get("error") or f"Could not verify your {broker.title()} connection."


def _get_mt_bridge_open_positions(user_id):
    """
    Reads straight from MetaApi's own get_positions() -- authoritative
    and live, same reasoning as _get_alpaca_open_positions() above (no
    journal lookup needed for the position numbers themselves, only for
    stop_loss/take_profit which this project's own trade plan set, not
    necessarily what MetaApi's position object reports back). unrealized_
    pnl comes straight from MetaApi's own "profit" field -- unlike eToro,
    MetaApi already computes this correctly account-currency-converted,
    no leverage-ambiguity caveat needed here.

    FIX 2026-09-14 (My Positions "Total Invested" showing $272K instead
    of real committed capital): "quantity" here is lots (see comment
    below) -- open_price * lots is not a dollar amount at all, but
    saas_app.py's render_open_positions() was multiplying them together
    anyway for its Total Invested summary metric, producing a wildly
    inflated notional-looking number. The only real USD figure for an
    MT4/5 position is what mt_broker._compute_lot_size() sized it from
    at entry time, which saas_decision_engine.py already journals as
    this order's trade_amount (see that file's buy_mt_for_user() call
    site) -- pulled here via the same entry_order lookup already needed
    for stop_loss/take_profit, so no extra query.
    """
    try:
        positions = mt_broker.get_user_mt_positions_sync(user_id)
    except Exception:
        return []

    result = []
    for p in positions:
        try:
            ticker = p.get("symbol")
            entry_order = journal.get_most_recent_filled_buy_for_user(user_id, ticker, "MT_BRIDGE")
            open_price = float(p.get("openPrice") or 0)
            current_price = float(p.get("currentPrice") or 0)
            pnl_pct = None
            if open_price > 0 and current_price:
                pnl_pct = round((current_price - open_price) / open_price * 100, 2)

            invested_amount = None
            if entry_order and entry_order.get("trade_amount") is not None:
                invested_amount = round(float(entry_order["trade_amount"]), 2)

            result.append({
                "ticker": ticker,
                "quantity": float(p.get("volume") or 0),  # lots, not shares/margin
                "entry_price": open_price,
                "current_price": current_price,
                "unrealized_pnl": round(float(p.get("profit") or 0), 2),
                "unrealized_pnl_pct": pnl_pct,
                "invested_amount": invested_amount,  # real USD committed at entry, see FIX above
                "stop_loss": entry_order.get("stop_loss") if entry_order else None,
                "take_profit": entry_order.get("take_profit") if entry_order else None,
            })
        except Exception:
            continue
    return result


# ============================================================
# ORDER EXECUTION -- MT4/5 via MetaApi (FOREX/COMMODITIES, alternative
# to eToro). Added 2026-09-02 (Phase 2). Unlike Alpaca/Binance/eToro
# above, the actual broker logic (deploy/connect, symbol resolution,
# leverage-aware lot sizing) lives entirely in mt_broker.py, not here --
# this is a thin pass-through so saas_decision_engine.py can call MT4/5
# the same way it calls every other broker, without needing to know
# mt_broker.py's functions are async under the hood (see that file's
# SYNC WRAPPERS section for why).
# ============================================================

def buy_mt_for_user(user_id, ticker, usd_amount, stop_loss_price=None, take_profit_price=None):
    """
    Per-user MT4/5 market BUY, sized by dollar amount (converted to a
    real lot size inside mt_broker.execute_buy_by_usd_amount() -- see
    that function's docstring for the leverage/contract-size math).

    Returns None if usd_amount was too small to reach this symbol's
    minimum lot size -- callers must treat that the same as any other
    "skip this trade" gate, not as a failure to report as an error.
    Otherwise returns {"position_id", "executed_price", "lot_size",
    "raw"} -- position_id confirms the order genuinely filled (MetaApi
    market orders on this symbol's fillingMode confirm synchronously,
    no polling needed, unlike eToro's buy_etoro_for_user()); a None
    position_id here (with a non-None dict) would mean the order was
    REJECTED, not just slow to confirm -- see that function's docstring
    for the live-tested response shape this was built from.
    """
    return mt_broker.execute_buy_by_usd_amount_sync(
        user_id, ticker, usd_amount, stop_loss_price, take_profit_price
    )


def sell_mt_for_user(user_id, position_id):
    """
    FOLLOW-UP 2026-09-02 (Phase 3): closes an existing MT4/5 position by
    its MetaApi position_id. Thin wrapper around mt_broker.execute_sell_
    close_sync() -- mirrors sell_etoro_for_user() above (position_id,
    not ticker+quantity, since that's what MetaApi's close_position()
    actually needs -- entry_order["broker_order_id"] is the position_id
    stored once the BUY confirmed filled).

    Used by engines/saas_exit_engine.py, but ONLY for the hard time-based
    exit -- MT4/5's own broker-side stop-loss/take-profit (set at order
    time, see mt_broker.execute_buy_by_usd_amount()) already covers
    price-based exits, so this is never reached for those; see that
    file's docstring for the full reasoning. A position closed by its
    own broker-side stop before this is ever called is instead caught by
    reconcile_user_mt_orders() (engines/saas_reconcile_engine.py).
    """
    return mt_broker.execute_sell_close_sync(user_id, position_id)


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
            qty = float(p.qty)
            avg_entry = round(float(p.avg_entry_price), 2)
            result.append({
                "ticker": p.symbol,
                "quantity": qty,
                "entry_price": avg_entry,
                "current_price": round(float(p.current_price), 2),
                "unrealized_pnl": round(float(p.unrealized_pl), 2),
                "unrealized_pnl_pct": round(float(p.unrealized_plpc) * 100, 2),
                # Real share count * real entry price -- unlike eToro/MT4-5,
                # "quantity" here genuinely is shares, so this is a true
                # cost basis. See _get_etoro_open_positions()'s FIX 2026-09-14
                # comment for why this field has to be computed differently
                # per broker rather than uniformly in saas_app.py.
                "invested_amount": round(qty * avg_entry, 2),
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
                # Real unit count * real entry price -- same reasoning as
                # _get_alpaca_open_positions()'s invested_amount above.
                "invested_amount": round(real_qty * entry_price, 2) if entry_price > 0 else None,
                "stop_loss": entry_order.get("stop_loss"),
                "take_profit": entry_order.get("take_profit"),
            })
        except Exception:
            continue
    return result


def _get_luno_open_positions(user_id):
    """
    Luno equivalent of _get_kraken_open_positions() -- same real-
    wallet-balance sizing, same "silently skip a ghost journal entry
    the wallet doesn't back" logic. entry_price on the journal is
    already USD (buy_luno_for_user() converts before returning -- see
    its docstring), so current_price here is converted to USD too via
    the same live FX rate before computing P&L, keeping this
    apples-to-apples with every other broker's open-positions view. If
    the FX rate can't be fetched, this ticker is skipped for this pass
    (fails toward "not shown" rather than showing a wrong number) --
    same fail-closed contract as check_user_luno_connection().
    """
    try:
        tickers = journal.list_open_tickers_for_user(user_id, "LUNO")
    except Exception:
        return []

    result = []
    for ticker in tickers:
        try:
            real_qty = get_user_luno_held_qty(user_id, ticker)
            if real_qty <= 0:
                continue

            entry_order = journal.get_most_recent_filled_buy_for_user(user_id, ticker, "LUNO")
            if entry_order is None:
                continue
            entry_price = float(entry_order.get("filled_price") or entry_order.get("price") or 0)

            exchange, quote_currency = _require_luno_exchange(user_id)
            fx_rate = _get_usd_fx_rate(quote_currency)
            if fx_rate is None:
                continue
            symbol = _to_luno_symbol(ticker, quote_currency)
            current_price = float(exchange.fetch_ticker(symbol)["last"]) / fx_rate

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
                "invested_amount": round(real_qty * entry_price, 2) if entry_price > 0 else None,
                "stop_loss": entry_order.get("stop_loss"),
                "take_profit": entry_order.get("take_profit"),
            })
        except Exception:
            continue
    return result


def _get_kraken_open_positions(user_id):
    """Kraken equivalent of _get_binance_open_positions() (task #365) --
    same real-wallet-balance sizing (get_user_kraken_held_qty), same
    "silently skip a ghost journal entry the wallet doesn't back" logic,
    just against Kraken's USD pairs instead of Binance's USDT ones."""
    try:
        tickers = journal.list_open_tickers_for_user(user_id, "KRAKEN")
    except Exception:
        return []

    result = []
    for ticker in tickers:
        try:
            real_qty = get_user_kraken_held_qty(user_id, ticker)
            if real_qty <= 0:
                continue

            entry_order = journal.get_most_recent_filled_buy_for_user(user_id, ticker, "KRAKEN")
            if entry_order is None:
                continue
            entry_price = float(entry_order.get("filled_price") or entry_order.get("price") or 0)

            exchange = _require_kraken_exchange(user_id)
            symbol = _to_kraken_symbol(ticker)
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
                "invested_amount": round(real_qty * entry_price, 2) if entry_price > 0 else None,
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

    FIX 2026-09-14 (My Positions "Total Invested" showing $272K instead
    of real committed capital): saas_app.py's render_open_positions()
    was computing its Total Invested summary metric as entry_price *
    quantity for every broker uniformly. That's correct for Alpaca/
    Binance (real share/unit counts) but wrong here, since -- per the
    paragraph above -- "quantity" for eToro already IS the dollar
    amount invested (find_etoro_position_by_ticker_for_user() maps it
    straight from eToro's own "amount" field). Multiplying it by
    entry_price again turned a real ~$1-2K margin position into a
    leveraged-notional-looking six-figure number. Exposing it here as
    its own invested_amount field (equal to quantity, not re-derived)
    lets saas_app.py sum the right thing without needing to know this
    broker-specific quirk itself.
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
                "invested_amount": round(amount, 2) if amount else None,  # see FIX above -- amount IS the dollar figure
                "stop_loss": entry_order.get("stop_loss") if entry_order else None,
                "take_profit": entry_order.get("take_profit") if entry_order else None,
            })
        except Exception:
            continue
    return result
