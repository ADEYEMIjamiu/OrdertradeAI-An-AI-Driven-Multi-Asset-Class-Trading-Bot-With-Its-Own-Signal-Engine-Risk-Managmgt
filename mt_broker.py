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
wired into saas_broker_factory.py in a later, separate step. This is
deliberate: it means Phase 1 cannot affect any existing engine's
behavior, no matter what it does or doesn't do correctly yet.

NOT YET LIVE-TESTED: everything below is built directly from MetaApi's
official Python SDK documentation (github.com/metaapi/metaapi-python-sdk,
docs/metaApi/managingAccounts.rst and docs/metaApi/rpcApi.rst), but has
not been run against a real MetaApi account or a real MT4/MT5 demo
account yet -- we don't have a MetaApi platform token or a demo MT
account to test against as of this writing. Treat this the same way
etoro_broker.py was treated after task #43 and before task #44: written
to match documented behavior, unverified until a real connectivity test
runs. Do not wire this into any live engine before that test passes.

COST MODEL -- ON-DEMAND DEPLOY (see chat discussion 2026-09-02):
MetaApi bills per hour a trading account's cloud terminal is "deployed"
(running). Unlike Alpaca/Binance (free API) and eToro (free API),
MetaApi is a paid, metered third party sitting between us and the
broker. Leaving every connected user's account deployed 24/7 would cost
roughly $8.64/account/month (g2 tier: $0.012/hour x 720 hours). Every
function below instead deploys right before it needs the account and
undeploys immediately after, dropping that to roughly $0.76/account/
month (undeployed hosting is ~$0.00105/hour) -- about a 90% reduction --
at the cost of a ~30-90 second reconnect delay each call. This is
acceptable because engines/saas_scheduler.py already runs the decision
loop on a fixed interval, not continuous streaming, so per-cycle
checks for eToro/Alpaca/Binance are not instant either; this doesn't
change that cadence, it just adds a short delay specifically for MT4/5
accounts at the start of each cycle they're checked in.

CREDENTIAL STORAGE: reuses the EXISTING engines/tenant_engine.py
save_broker_credentials()/get_broker_credentials() functions with
broker="MT_BRIDGE" -- no schema changes, no migration, nothing else in
the codebase touched (per instruction to keep Phase 1 fully additive).
The three existing encrypted slots are used as:
    api_key    = MT account login (the account number, e.g. "1234567")
    api_secret = MT account password
    extra      = JSON string: {"server": "<broker server name>",
                 "platform": "mt4" or "mt5",
                 "metaapi_account_id": "<filled in after first connect>"}
Packing server/platform/metaapi_account_id as JSON into the single
`extra` slot (rather than adding new columns) is what keeps this
integration fully additive.

MASTER VS INVESTOR PASSWORD: MetaTrader accounts have two passwords --
"investor" (read-only: balance/positions/history, cannot place orders)
and "master" (full trading rights). check_user_mt_connection() below
works with either. execute_buy()/execute_sell_close() need the MASTER
password and will fail with an authorization error from MetaApi if only
the investor password was supplied -- this is correct broker-side
behavior, not a bug here. Phase 2's connect-flow UI needs to explain
this tradeoff to users explicitly (same "read-only vs trading scope"
choice they already make when generating an Alpaca/Binance API key).

PLATFORM-LEVEL TOKEN: unlike api_key/api_secret above (which are each
USER's own MT login), METAAPI_TOKEN (read from the environment, see
_get_api() below) is OrderTrade AI's OWN MetaApi platform token -- one
token for the whole platform, used to provision every user's MT account
on MetaApi's side. It must be added to .env once (same tier as e.g. a
Resend or Stripe key), and is never taken from user input.
"""

import json
import os

from metaapi_cloud_sdk import MetaApi

from engines import tenant_engine as tenant

BROKER_CODE = "MT_BRIDGE"

_METAAPI_TOKEN_ENV_VAR = "METAAPI_TOKEN"
_DEPLOY_TIMEOUT_SECONDS = 120


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
    """
    extra = json.dumps({"server": server, "platform": platform})
    tenant.save_broker_credentials(
        user_id, BROKER_CODE, environment,
        api_key=login, api_secret=password, extra=extra,
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
        # "regular" (not "high") reliability -- the cheaper g2 cloud
        # tier this whole cost model is based on; see module docstring.
        "reliability": "regular",
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
    On-demand deploy: starts the account's cloud terminal if it isn't
    already running, connects, and waits for sync. Callers MUST
    undeploy when done (see _undeploy() below) -- leaving accounts
    deployed is exactly the cost this design exists to avoid.

    Only calls methods confirmed in MetaApi's documented SDK reference
    (account.deploy(), connection.connect(), connection.
    wait_synchronized()) -- deliberately does NOT call an unconfirmed
    "wait_connected"-style helper that isn't in the documented API
    surface, to avoid shipping a call that might not exist.
    """
    already_running = getattr(account, "state", None) in ("DEPLOYING", "DEPLOYED")
    if not already_running:
        await account.deploy()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized(timeout_in_seconds=_DEPLOY_TIMEOUT_SECONDS)
    return connection


async def _undeploy(account):
    """Best-effort -- a failed undeploy costs a few more cents of hosting
    until the next call succeeds or MetaApi's own idle handling kicks
    in, never a correctness problem, so this never raises."""
    try:
        await account.undeploy()
    except Exception:
        pass


async def check_user_mt_connection(user_id):
    """
    Connects just long enough to confirm the credentials work and read
    account info, then undeploys again. Mirrors saas_broker_factory.py's
    check_user_etoro_connection()/check_user_alpaca_connection() return
    shape so this can be plugged into the same "Test Connection" UI
    pattern in Phase 2 without changing that shape.
    """
    account = None
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
    finally:
        if account is not None:
            await _undeploy(account)


async def get_user_mt_positions(user_id):
    """Returns MetaApi's raw position list. Deploy -> read -> undeploy,
    same on-demand pattern as every function in this file."""
    account = await _get_or_create_metaapi_account(user_id)
    try:
        connection = await _deploy_and_connect(account)
        return await connection.get_positions()
    finally:
        await _undeploy(account)


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
    try:
        connection = await _deploy_and_connect(account)
        return await connection.create_market_buy_order(
            symbol=symbol,
            volume=volume,
            stop_loss=stop_loss,
            take_profit=take_profit,
            options={"comment": "OrderTradeAI"},
        )
    finally:
        await _undeploy(account)


async def execute_sell_close(user_id, position_id):
    """Fully closes an existing position by MetaApi position id.
    Same master-password requirement and Phase-1-standalone status as
    execute_buy() above."""
    account = await _get_or_create_metaapi_account(user_id)
    try:
        connection = await _deploy_and_connect(account)
        return await connection.close_position(position_id=position_id)
    finally:
        await _undeploy(account)
