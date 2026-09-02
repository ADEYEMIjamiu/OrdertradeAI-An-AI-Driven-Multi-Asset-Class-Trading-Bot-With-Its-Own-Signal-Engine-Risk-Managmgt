"""
mt_broker.py -- Per-user MetaTrader 4/5 broker bridge via MetaApi.cloud.

WHY THIS FILE EXISTS: Alpaca, Binance, and eToro cover US/EU-style
brokers, but retail traders in Nigeria, Malaysia, the UAE, and most of
the rest of the world overwhelmingly use MetaTrader 4/5 brokers instead
(Exness, XM, Pepperstone, AvaTrade, FXTM, IC Markets, HFM, JustMarkets,
Capital.com, FP Markets, and dozens more all run on MT4/MT5). Rather than
building one connector per broker, this integrates against MetaApi.cloud
-- a third-party bridge that speaks to ANY MT4/MT5 broker through one
stable API -- so a single integration unlocks all of them at once.
Decided 2026-09-02; see chat discussion for the broker research behind
this.

PHASE 1 (this file, as of 2026-09-02): STANDALONE. Not imported by
saas_decision_engine.py, saas_broker_factory.py, saas_exit_engine.py, or
any other engine yet. Nothing in this file runs automatically -- it only
runs when explicitly called (manual/local testing), exactly like
etoro_broker.py's original standalone build (tasks #43-48) before it was
wired into saas_broker_factory.py in a later, separate step.

COST MODEL -- CORRECTED 2026-09-02, DO NOT RE-INTRODUCE PER-CALL
UNDEPLOY: the original version of this file deployed the account right
before every single call and undeployed right after, based on an
assumption that MetaApi bills purely by the hour. That assumption was
WRONG -- MetaApi's own FAQ (metaapi.cloud/docs/client/faq/) states
plainly: "you are billed for 6 hours each time you start your server."
There is a 6-hour MINIMUM billing block per deploy, no matter how
briefly the account is actually used. Given engines/saas_scheduler.py
runs the decision loop every few minutes, deploying and undeploying on
every cycle would trigger a fresh 6-hour bill almost every time --
costing dramatically MORE than just staying deployed continuously (real
math: g2 tier, $0.012/account/hour x 720 hours/month = ~$8.64/user/month
always-on, vs. potentially $50-600+/user/month if redeployed every few
minutes). The correct, cheaper model is the same one Alpaca/Binance/
eToro already use: connect once when a user links their MT4/MT5 account,
stay deployed continuously while connected, and only undeploy when the
user disconnects that broker entirely (see disconnect_user_mt_account()
below). None of the functions below undeploy after use -- only
disconnect_user_mt_account() does.

DEPLOY/CONNECT SEQUENCING -- FIXED 2026-09-02 after a live test against
a real Pepperstone MT5 demo account failed with repeated "account ...
is not connected to broker yet" / "no accounts deployed yet" errors for
~105 seconds before timing out. Root cause: account.deploy() only
signals MetaApi to START deploying a cloud terminal -- it does not wait
for that terminal to actually finish connecting to the broker's own MT5
server, which is a separate, slower step. The first version of this
file called account.deploy() and then immediately tried to open an RPC
connection, racing ahead of the real state. Confirmed via direct
introspection of the installed SDK (metaapi-cloud-sdk==29.1.1) that
MetatraderAccount has documented wait_deployed() and wait_connected()
methods for exactly this -- _deploy_and_connect() below now calls both,
in order, before ever attempting connection.connect().

CREDENTIAL STORAGE: reuses the EXISTING engines/tenant_engine.py
save_broker_credentials()/get_broker_credentials() functions with
broker="MT_BRIDGE" -- no schema changes, no migration, nothing else in
the codebase touched. The three existing encrypted slots are used as:
    api_key    = MT account login (the account number, e.g. "1234567")
    api_secret = MT account password
    extra      = JSON string: {"server": "<broker server name>",
                 "platform": "mt4" or "mt5",
                 "metaapi_account_id": "<filled in after first connect>"}

MASTER VS INVESTOR PASSWORD: MetaTrader accounts have two passwords --
"investor" (read-only: balance/positions/history, cannot place orders)
and "master" (full trading rights). check_user_mt_connection() below
works with either. execute_buy()/execute_sell_close() need the MASTER
password and will fail with an authorization error if only the investor
password was supplied -- this is correct broker-side behavior, not a
bug here.

PLATFORM-LEVEL TOKEN: unlike api_key/api_secret above (which are each
USER's own MT login), METAAPI_TOKEN (read from the environment, see
_get_api() below) is OrderTrade AI's OWN MetaApi platform token -- one
token for the whole platform, used to provision every user's MT account
on MetaApi's side. It must be added to .env once, and is never taken
from user input. Also worth noting: this token's validity was set to
3 months when generated on 2026-09-02 for testing -- pick the longest
available validity (or set a renewal reminder) before this is relied on
for live production users, since expiry would silently break every
connected user's MT4/5 access at once.
"""

import asyncio
import json
import os

from metaapi_cloud_sdk import MetaApi

from engines import tenant_engine as tenant

BROKER_CODE = "MT_BRIDGE"

_METAAPI_TOKEN_ENV_VAR = "METAAPI_TOKEN"
_DEPLOY_TIMEOUT_SECONDS = 300  # first-ever deploy of a fresh account can be slow


def _get_api():
    """Builds a MetaApi client from OrderTrade AI's own platform token
    (NOT a per-user credential -- see module docstring)."""
    token = os.environ.get(_METAAPI_TOKEN_ENV_VAR)
    if not token:
        raise RuntimeError(
            f"{_METAAPI_TOKEN_ENV_VAR} is not set. This is OrderTrade AI's "
            "own MetaApi platform token (one token covers every user's "
            "account), not a per-user credential -- add it to .env."
        )
    return MetaApi(token=token)


def _parse_extra(extra_raw):
    """`extra` is stored as a JSON string (see module docstring). Returns
    {} on anything unparsable so a first-time connect (no
    metaapi_account_id cached yet) is a normal codepath, not an error."""
    if not extra_raw:
        return {}
    try:
        return json.loads(extra_raw)
    except (TypeError, ValueError):
        return {}


async def save_mt_credentials(user_id, login, password, server, platform="mt5", environment="demo"):
    """
    Saves this user's MT4/MT5 login. Call this from the Phase 2 connect
    UI. Does NOT contact MetaApi or validate the credentials -- pair this
    with check_user_mt_connection() (which does) the same way the
    existing "Test Connection" buttons for Alpaca/Binance/eToro work.

    IMPORTANT: if this user already has a MetaApi account provisioned
    for this exact login/server/platform, its metaapi_account_id is
    preserved rather than discarded -- overwriting it unconditionally
    on every re-save (e.g. a test script run twice, or a user re-opening
    the connect form without changing anything) used to force a brand
    new MetaApi account to be created each time, silently accumulating
    duplicate accounts on MetaApi's side and burning through their
    free-tier account allowance. If login/server/platform actually
    changed, the old id is correctly dropped so a fresh account gets
    provisioned for the new broker connection.
    """
    existing = tenant.get_broker_credentials(user_id, BROKER_CODE)
    metaapi_account_id = None
    if existing:
        old_extra = _parse_extra(existing.get("extra"))
        if (
            existing.get("api_key") == login
            and old_extra.get("server") == server
            and old_extra.get("platform") == platform
        ):
            metaapi_account_id = old_extra.get("metaapi_account_id")

    extra = {"server": server, "platform": platform}
    if metaapi_account_id:
        extra["metaapi_account_id"] = metaapi_account_id

    tenant.save_broker_credentials(
        user_id, BROKER_CODE, environment,
        api_key=login, api_secret=password, extra=json.dumps(extra),
    )


async def _get_or_create_metaapi_account(user_id):
    """
    Returns the MetaApi MetatraderAccount object for this user, creating
    it on MetaApi's side (and caching the returned id back into this
    user's own encrypted credential row) the first time this is called.
    Every later call reuses the cached metaapi_account_id -- this is
    what avoids MetaApi's one-time "adding a trading account" fee being
    charged again on every check, and avoids silently creating duplicate
    MetaApi accounts for the same MT login.
    """
    creds = tenant.get_broker_credentials(user_id, BROKER_CODE)
    if creds is None:
        raise ValueError(f"No {BROKER_CODE} credentials saved for this user.")

    extra = _parse_extra(creds["extra"])
    api = _get_api()

    metaapi_account_id = extra.get("metaapi_account_id")
    if metaapi_account_id:
        try:
            return await api.metatrader_account_api.get_account(metaapi_account_id)
        except Exception:
            # Cached id is stale (e.g. removed on MetaApi's side out of
            # band) -- fall through and re-create rather than
            # permanently failing this user's connection.
            pass

    server = extra.get("server")
    platform = extra.get("platform", "mt5")
    if not server:
        raise ValueError(
            "No broker server name saved for this MT4/MT5 connection. "
            "Reconnect with your broker's server name (e.g. 'Exness-Real3')."
        )

    account = await api.metatrader_account_api.create_account(account={
        "name": f"OrderTradeAI-{user_id}",
        "type": "cloud",
        "login": creds["api_key"],
        "password": creds["api_secret"],
        "server": server,
        "platform": platform,
        "magic": 0,
        "quoteStreamingIntervalInSeconds": 2.5,
        "reliability": "regular",  # cheaper g2 tier -- see module docstring
    })

    extra["metaapi_account_id"] = account.id
    tenant.save_broker_credentials(
        user_id, BROKER_CODE, creds["environment"],
        api_key=creds["api_key"], api_secret=creds["api_secret"],
        extra=json.dumps(extra),
    )
    return account


async def _deploy_and_connect(account):
    """
    Deploys the account if it isn't already, THEN explicitly waits for
    both (a) the cloud terminal to finish deploying and (b) that
    terminal to actually establish its own connection to the broker's
    MT server -- these are two separate steps, and skipping straight to
    connection.connect() after only calling deploy() is what caused the
    live test failure on 2026-09-02 (see module docstring). Does NOT
    undeploy when done -- see module docstring's COST MODEL section for
    why staying deployed is now the deliberate, cheaper default.
    """
    if getattr(account, "state", None) not in ("DEPLOYING", "DEPLOYED"):
        await account.deploy()

    await account.wait_deployed(timeout_in_seconds=_DEPLOY_TIMEOUT_SECONDS)
    await account.wait_connected(timeout_in_seconds=_DEPLOY_TIMEOUT_SECONDS)

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized(timeout_in_seconds=_DEPLOY_TIMEOUT_SECONDS)
    return connection


async def check_user_mt_connection(user_id):
    """
    Connects (deploying the account if needed -- see module docstring
    for why this deliberately does NOT undeploy afterward) and reads
    account info. Mirrors saas_broker_factory.py's
    check_user_etoro_connection()/check_user_alpaca_connection() return
    shape so this can be plugged into the same "Test Connection" UI
    pattern in Phase 2 without changing that shape.
    """
    try:
        account = await _get_or_create_metaapi_account(user_id)
        connection = await _deploy_and_connect(account)
        info = await connection.get_account_information()
        return {
            "connected": True,
            "account_status": "CONNECTED",
            "trading_blocked": False,
            "buying_power": float(info.get("freeMargin", 0) or 0),
            "cash": float(info.get("balance", 0) or 0),
            "equity": float(info.get("equity", 0) or 0),
            "broker_name": info.get("broker", ""),
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
            "broker_name": "",
            "error": str(e),
        }


async def get_user_mt_positions(user_id):
    """Returns MetaApi's raw position list. Does not undeploy -- see
    module docstring's COST MODEL section."""
    account = await _get_or_create_metaapi_account(user_id)
    connection = await _deploy_and_connect(account)
    return await connection.get_positions()


async def execute_buy(user_id, symbol, volume, stop_loss=None, take_profit=None):
    """
    Places a real market BUY order for this user's connected MT4/MT5
    account. Requires the MASTER password (see module docstring) --
    fails with an authorization error if only the investor password was
    saved. NOT wired into saas_decision_engine.py yet (Phase 1 -- see
    module docstring); call this directly for manual/local testing only
    until Phase 2 lands.
    """
    account = await _get_or_create_metaapi_account(user_id)
    connection = await _deploy_and_connect(account)
    return await connection.create_market_buy_order(
        symbol=symbol,
        volume=volume,
        stop_loss=stop_loss,
        take_profit=take_profit,
        options={"comment": "OrderTradeAI"},
    )


async def execute_sell_close(user_id, position_id):
    """Fully closes an existing position by MetaApi position id.
    Same master-password requirement and Phase-1-standalone status as
    execute_buy() above."""
    account = await _get_or_create_metaapi_account(user_id)
    connection = await _deploy_and_connect(account)
    return await connection.close_position(position_id=position_id)


async def disconnect_user_mt_account(user_id):
    """
    THE ONLY function in this file that undeploys. Call this when a user
    explicitly disconnects/removes their MT4/MT5 broker connection from
    account settings -- not after every trade or check (see module
    docstring's COST MODEL section for why). Best-effort: swallows
    errors from undeploy() itself so a flaky MetaApi call can't block a
    user from disconnecting in our own UI.
    """
    account = await _get_or_create_metaapi_account(user_id)
    try:
        await account.undeploy()
    except Exception:
        pass


# ============================================================
# PHASE 2 (2026-09-02): TICKER RESOLUTION + DOLLAR-BASED SIZING
# ============================================================
#
# Everything above this point is Phase 1 (standalone, symbol/volume are
# raw MT4/5 inputs the caller must already know). This section is what
# makes mt_broker.py usable as a real FOREX/COMMODITIES option inside
# saas_decision_engine.py's per-user execution branch, alongside eToro:
# translating this project's own yfinance-style tickers ("EURUSD=X",
# "GC=F") into real MT symbol names, and converting the AI's
# dollar-sized trade recommendation into a real MT lot size.
#
# All symbol names and the leverage/contract-size math below were
# confirmed LIVE against the real connected Pepperstone demo account
# (2026-09-02) via get_symbol_specification()/get_symbols(), not
# guessed -- see that day's diagnostic scripts. Two things worth
# flagging for whoever touches this next:
#
# 1. MT symbol names are NOT standardized across brokers. XAUUSD/XAGUSD
#    (gold/silver) are near-universal MT conventions and very likely to
#    exist unchanged on other brokers, but oil has no standard name at
#    all -- this broker exposes "SpotCrude" (a plain WTI cash/spot CFD,
#    no expiration) alongside "WTOIL-PERP" (a perpetual swap, priced in
#    US cents not dollars, different margin/funding mechanics) and
#    "Crude-F" (a dated forward that rolls to a new contract every few
#    weeks). SpotCrude was chosen deliberately as the one that behaves
#    like a normal instrument (USD-denominated, no expiration/roll,
#    plain market fills) -- if a future broker doesn't have a
#    "SpotCrude"-equivalent, this mapping needs a broker-specific
#    override, not a blind guess at a similarly-named symbol.
# 2. Some MT brokers append suffixes to symbol names (e.g. "EURUSDm" on
#    ECN-style accounts) -- Pepperstone's demo does not, so this isn't
#    handled here yet. If a future broker connection's symbol lookups
#    start failing with "not found" for tickers that clearly should
#    exist, a broker-specific suffix is the first thing to check.

_MT_TICKER_OVERRIDES = {
    "GC=F": "XAUUSD",   # Gold
    "SI=F": "XAGUSD",   # Silver
    "CL=F": "SpotCrude",  # WTI Crude -- see note above on why this one
                          # specifically, not WTOIL-PERP or Crude-F.
}


def resolve_mt_symbol(project_ticker):
    """
    Translate one of this project's own tickers (data/asset_universe.py
    -- yfinance-style, e.g. "EURUSD=X", "GC=F") into the real MT symbol
    name this broker uses (e.g. "EURUSD", "XAUUSD"). Mirrors
    etoro_broker.resolve_project_ticker()'s exact same job for eToro.
    """
    ticker = project_ticker.upper().strip()

    if ticker in _MT_TICKER_OVERRIDES:
        return _MT_TICKER_OVERRIDES[ticker]

    if ticker.endswith("=X") or ticker.endswith("=F"):
        return ticker.split("=")[0]

    return ticker


async def _compute_lot_size(connection, account_info, symbol, usd_amount):
    """
    Converts a dollar trade_amount (same semantics as eToro's
    margin-based sizing -- see saas_broker_factory.buy_etoro_for_user()'s
    ETORO_LEVERAGE handling: usd_amount is the user's own capital being
    put up as margin, not the full position value) into a real MT lot
    size, using THIS account's own real leverage (read from account
    info, NOT a hardcoded constant -- MT accounts vary in leverage by
    broker/region/regulator, unlike eToro's single ETORO_LEVERAGE
    constant -- an FCA-regulated UK demo account like this one is capped
    at 30:1, while offshore-regulated accounts commonly used in Nigeria/
    Malaysia run far higher) and this symbol's real contract size
    (100,000 base-currency units for a standard forex lot, but a
    completely different number for commodities -- 100 oz/lot for
    XAUUSD on this broker, confirmed live rather than assumed).

        notional_value = usd_amount * account_leverage
        lot_size = notional_value / (contract_size * current_price)

    Rounded DOWN to the symbol's own volumeStep (never up past what the
    requested dollar amount actually supports) and capped at maxVolume.
    Returns None if the resulting lot size would round down to less than
    minVolume -- caller should treat this as "skip, don't attempt the
    order" rather than let MetaApi's own rejection surface as a raw
    broker-error string.
    """
    leverage = float(account_info.get("leverage") or 1)

    spec = await connection.get_symbol_specification(symbol)
    contract_size = float(spec.get("contractSize") or 0)
    volume_step = float(spec.get("volumeStep") or 0.01)
    min_volume = float(spec.get("minVolume") or 0.01)
    max_volume = float(spec.get("maxVolume") or 100)

    if contract_size <= 0:
        raise ValueError(f"No contract size available for {symbol!r} -- cannot size a position.")

    price_data = await connection.get_symbol_price(symbol)
    price = float(price_data.get("ask") or price_data.get("bid") or 0)
    if price <= 0:
        raise ValueError(f"No usable price available for {symbol!r} -- cannot size a position.")

    notional_value = usd_amount * leverage
    raw_lots = notional_value / (contract_size * price)

    steps = int(raw_lots / volume_step)
    lot_size = round(steps * volume_step, 2)

    if lot_size < min_volume:
        return None
    return min(lot_size, max_volume)


async def execute_buy_by_usd_amount(user_id, ticker, usd_amount, stop_loss_price=None, take_profit_price=None):
    """
    Higher-level entry point matching the dollar-based sizing semantics
    every other broker in this codebase uses (buy_stock_for_user(dollars)
    in saas_broker_factory.py, buy_etoro_for_user(usd_amount)) -- MT4/5
    itself only understands lot sizes, so this resolves the project
    ticker to a real MT symbol (resolve_mt_symbol()), converts usd_amount
    into a real lot size (_compute_lot_size() -- see that function's
    docstring for the leverage/contract-size math), then places the
    order.

    stop_loss_price/take_profit_price are ABSOLUTE prices (not
    percentages or rates) -- pass whatever create_trade_plan() already
    computed, same convention the other three brokers' execution
    functions use.

    Returns None (not an order response) if usd_amount is too small to
    reach this symbol's minimum lot size at this account's leverage --
    saas_decision_engine.py should treat that the same as any other
    "skip this trade" gate, not attempt the order.
    """
    symbol = resolve_mt_symbol(ticker)

    account = await _get_or_create_metaapi_account(user_id)
    connection = await _deploy_and_connect(account)
    account_info = await connection.get_account_information()

    lot_size = await _compute_lot_size(connection, account_info, symbol, usd_amount)
    if lot_size is None:
        return None

    return await connection.create_market_buy_order(
        symbol=symbol,
        volume=lot_size,
        stop_loss=stop_loss_price,
        take_profit=take_profit_price,
        options={"comment": "OrderTradeAI"},
    )


# ============================================================
# SYNC WRAPPERS -- saas_decision_engine.py and saas_broker_factory.py
# are entirely synchronous (requests, sync ccxt, sync alpaca-py), but
# the MetaApi SDK is async-only (websocket-based). Rather than making
# the whole decision loop async -- a much larger, riskier change to
# code every other broker integration also depends on -- each of these
# just runs its async counterpart to completion via asyncio.run() and
# returns a plain value. This does mean each call spins up and tears
# down its own event loop rather than reusing one; acceptable given the
# call frequency here (once per user per decision-loop tick, not a hot
# path), and it keeps the async/MetaApi-specific complexity fully
# contained to this one file.
# ============================================================

def save_mt_credentials_sync(user_id, login, password, server, platform="mt5", environment="demo"):
    return asyncio.run(save_mt_credentials(user_id, login, password, server, platform, environment))


def check_user_mt_connection_sync(user_id):
    return asyncio.run(check_user_mt_connection(user_id))


def get_user_mt_positions_sync(user_id):
    return asyncio.run(get_user_mt_positions(user_id))


def execute_buy_by_usd_amount_sync(user_id, ticker, usd_amount, stop_loss_price=None, take_profit_price=None):
    return asyncio.run(execute_buy_by_usd_amount(user_id, ticker, usd_amount, stop_loss_price, take_profit_price))


def execute_sell_close_sync(user_id, position_id):
    return asyncio.run(execute_sell_close(user_id, position_id))


def disconnect_user_mt_account_sync(user_id):
    return asyncio.run(disconnect_user_mt_account(user_id))
