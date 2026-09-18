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
from user input. This token was regenerated 2026-09-02 (the original had
been pasted in a chat transcript during development -- treat as
compromised the moment that happens, regardless of how low the actual
risk seems) -- pick the longest available validity when it's next
rotated (or set a renewal reminder), since expiry would silently break
every connected user's MT4/5 access at once.

CONNECTION CLEANUP -- FIXED 2026-09-02 (task #238, found via
test_mt_phase3.py's rapid-fire live test run: six calls in under 3
minutes left "Unclosed client session" warnings and a background
SubscriptionManager task crashing with a KeyError at process exit).
Every function below that talks to MetaApi now goes through
_mt_connection(), an async context manager that explicitly closes both
the RPC connection and the MetaApi client it was opened from before
returning -- confirmed-live methods (connection.close(), api.close()),
not guessed (see inspect_close_methods.py). Without this, each of the
SYNC WRAPPERS below (each spinning up and tearing down its OWN event
loop -- see that section) would leave that call's aiohttp session(s) and
background tasks dangling once the loop was gone, with nothing left to
ever clean them up. This does NOT change the account-level deploy/
undeploy behavior described in COST MODEL above -- only the RPC
connection and client are closed per call, the account itself stays
deployed exactly as before.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager

from metaapi_cloud_sdk import MetaApi

from engines import tenant_engine as tenant
from engines.broker_error_messages import friendly_broker_error_message, LiveTradingNotEnabledError

BROKER_CODE = "MT_BRIDGE"

_METAAPI_TOKEN_ENV_VAR = "METAAPI_TOKEN"
_DEPLOY_TIMEOUT_SECONDS = 300  # first-ever deploy of a fresh account can be slow

# LIVE TRADING GATE -- added 2026-09-08 (task #305). Unlike Alpaca
# (paper=True/False), Binance (sandbox_mode), and eToro (URL path
# prefix chosen from creds["environment"]), MetaApi/MT4/5 has NO
# code-level sandbox: the login+server a user supplies IS a specific
# broker account that is already, on the broker's own side, either a
# demo account (fake money, provisioned by the broker for practice) or
# a real account (real money) -- there is nothing for this platform to
# flip between the two. The user_broker_credentials.environment field
# saved at connect time (see save_mt_credentials() below) is therefore
# just a placeholder ("demo") until the account has actually been
# connected once -- it is NEVER consulted for the live/demo execution
# decision, unlike the other three brokers' _is_live() checks in
# saas_broker_factory.py, and must not be treated as equivalent to
# their creds["environment"].
#
# The real, trustworthy signal is MetaApi's own account_information.type
# field (enum ACCOUNT_TRADE_MODE_DEMO / ACCOUNT_TRADE_MODE_CONTEST /
# ACCOUNT_TRADE_MODE_REAL -- confirmed via MetaApi's own API docs,
# metaapi.cloud/docs/client/models/metatraderAccountInformation/),
# fetched fresh from the broker on every single connection, not
# self-reported by the user. _mt_is_live_account_type() below classifies
# it; execute_buy_by_usd_amount() and execute_sell_close() both call
# get_account_information() already (needed for lot sizing/position
# lookups) and now use that same fetch to enforce Lock 1
# (user_settings.allow_live_trading) before ever placing/closing a real
# order -- raising LiveTradingNotEnabledError (caught by
# saas_decision_engine.py's existing generic except-Exception block for
# MT_BRIDGE, same fail-safe path as any other broker rejection: nothing
# is journaled, no order is placed) if the account is REAL but Lock 1 is
# off. Demo/contest accounts are never gated by Lock 1, exactly like a
# paper/testnet/demo credential on the other three brokers -- no real
# money is ever at risk on those regardless of this platform's own
# live-trading switch.
#
# Found while implementing this task: prior to this fix, ANY user who
# connected a real MT4/5 account had that account tradeable through the
# AI decision loop with NO Lock 1 check at all -- a live, reachable gap,
# not a dormant one like eToro's (task #304). Confirmed via direct
# production DB query before starting this fix that zero users currently
# have an MT_BRIDGE credential saved, so nothing was actually exposed --
# but this needed to close before the feature could be considered safe
# to leave live.


def _mt_is_live_account_type(account_type):
    """True only for MetaApi's ACCOUNT_TRADE_MODE_REAL -- see LIVE
    TRADING GATE note above."""
    return account_type == "ACCOUNT_TRADE_MODE_REAL"


def _user_has_live_trading_enabled(user_id):
    """
    Mirrors saas_broker_factory.py's private helper of the exact same
    name and contract (fails CLOSED / False on any exception) --
    duplicated rather than imported because saas_broker_factory.py
    already imports this module (mt_broker.py), so importing back would
    be circular.
    """
    try:
        settings = tenant.get_user_settings(user_id)
        return bool(settings and settings.get("allow_live_trading"))
    except Exception:
        return False

# FIX 2026-09-02 (task #238 follow-up -- first-connect UX): live-tested the
# same day, a brand-new MetaApi account took ~16 minutes end-to-end for its
# first real broker connection (multiple internal retries inside the SDK's
# own wait_deployed/wait_connected/wait_synchronized calls, each budgeted up
# to _DEPLOY_TIMEOUT_SECONDS=300s). check_user_mt_connection() backs a
# synchronous "Test Connection" button/HTTP request (see saas_app.py) that
# cannot block for minutes -- a Streamlit request or nginx proxy will hit
# its own read timeout long before that. It now uses this much shorter
# budget instead and returns fast with status="deploying" so the caller can
# poll again rather than either hanging or reporting a false hard failure.
# Deploying is idempotent (see _deploy_and_connect()'s own state check), so
# calling this repeatedly while an account spins up is safe and cheap.
_CONNECTION_CHECK_TIMEOUT_SECONDS = 20

# account.state values that mean deployment genuinely, terminally failed --
# confirmed live 2026-09-02 via direct read of the installed SDK's `State`
# Literal (clients/metaapi/metatrader_account_client.py), same discipline as
# every other SDK fact in this file. CREATED/DEPLOYING/DEPLOYED/UNDEPLOYING/
# UNDEPLOYED/DELETING/DRAFT are all normal in-progress or already-passed
# states, not failures, and are deliberately NOT in this set.
_FAILED_ACCOUNT_STATES = frozenset({
    "DEPLOY_FAILED", "UNDEPLOY_FAILED", "DELETE_FAILED", "REDEPLOY_FAILED",
})


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
    Returns (api, account) -- the MetaApi client instance used AND the
    MetatraderAccount object for this user, creating the account on
    MetaApi's side (and caching the returned id back into this user's
    own encrypted credential row) the first time this is called. Every
    later call reuses the cached metaapi_account_id -- this is what
    avoids MetaApi's one-time "adding a trading account" fee being
    charged again on every check, and avoids silently creating duplicate
    MetaApi accounts for the same MT login.

    FIX 2026-09-02 (task #238, found via test_mt_phase3.py's rapid-fire
    live test run): this used to return ONLY `account`, with `api`
    (a fresh MetaApi(token=...) client built fresh on every single call
    -- see _get_api()) going out of scope and never explicitly closed.
    Confirmed live: six calls in under 3 minutes left "Unclosed client
    session" warnings and a background SubscriptionManager task that
    crashed with a KeyError trying to run after its owning event loop
    (each of mt_broker.py's sync wrappers tears down its own -- see that
    section's docstring) was already gone. `api` is returned now
    specifically so callers can `await api.close()` when done -- see
    _mt_connection() below, which does this (and connection.close())
    automatically for every function in this file that opens one.
    """
    creds = tenant.get_broker_credentials(user_id, BROKER_CODE)
    if creds is None:
        raise ValueError(f"No {BROKER_CODE} credentials saved for this user.")

    extra = _parse_extra(creds["extra"])
    api = _get_api()

    metaapi_account_id = extra.get("metaapi_account_id")
    if metaapi_account_id:
        try:
            return api, await api.metatrader_account_api.get_account(metaapi_account_id)
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
    return api, account


async def _deploy_and_connect(account, deploy_timeout_seconds=_DEPLOY_TIMEOUT_SECONDS):
    """
    Deploys the account if it isn't already, THEN explicitly waits for
    both (a) the cloud terminal to finish deploying and (b) that
    terminal to actually establish its own connection to the broker's
    MT server -- these are two separate steps, and skipping straight to
    connection.connect() after only calling deploy() is what caused the
    live test failure on 2026-09-02 (see module docstring). Does NOT
    undeploy when done -- see module docstring's COST MODEL section for
    why staying deployed is now the deliberate, cheaper default.

    FIX 2026-09-02 (task #238 follow-up): deploy_timeout_seconds is now a
    parameter (was hardcoded to module-level _DEPLOY_TIMEOUT_SECONDS for
    every wait below) so check_user_mt_connection() can use a much
    shorter budget for its UI-facing check (see
    _CONNECTION_CHECK_TIMEOUT_SECONDS) without affecting the patient
    full-length wait execute_buy()/execute_sell_close()/
    get_user_mt_positions() still use via the default. Also fixes a gap
    found in that same investigation: connection.connect() below was the
    ONLY call in this chain with no timeout at all -- its source location
    in the installed SDK wasn't found to confirm whether it accepts a
    timeout_in_seconds kwarg the way the other three calls do, so it's
    wrapped in asyncio.wait_for() instead, which bounds it regardless of
    whatever the SDK's own internal default is.
    """
    if getattr(account, "state", None) not in ("DEPLOYING", "DEPLOYED"):
        await account.deploy()

    await account.wait_deployed(timeout_in_seconds=deploy_timeout_seconds)
    await account.wait_connected(timeout_in_seconds=deploy_timeout_seconds)

    connection = account.get_rpc_connection()
    await asyncio.wait_for(connection.connect(), timeout=deploy_timeout_seconds)
    await connection.wait_synchronized(timeout_in_seconds=deploy_timeout_seconds)
    return connection


@asynccontextmanager
async def _mt_connection(user_id, deploy_timeout_seconds=_DEPLOY_TIMEOUT_SECONDS):
    """
    Async context manager: yields a live RPC connection for this user's
    MT4/5 account, guaranteeing BOTH connection.close() and api.close()
    run afterward -- even if the code using the connection raises -- so
    a single call's aiohttp session(s) and background subscription task
    never outlive that call's own event loop (mt_broker.py's sync
    wrappers each tear down their own via asyncio.run() -- see the
    SYNC WRAPPERS section below). Added 2026-09-02 (task #238) after
    test_mt_phase3.py's rapid-fire live run left "Unclosed client
    session" warnings and a background SubscriptionManager task crashing
    at process exit -- close() and close() are both real, confirmed-live
    methods on the connection and MetaApi client respectively (see
    inspect_close_methods.py), not guessed.

    FIX 2026-09-02 (task #238 follow-up): api is now fetched/closed in
    its own try/finally wrapping _deploy_and_connect() too, not just the
    yielded body -- the original version fetched `api` and then called
    _deploy_and_connect(account) BEFORE entering the try/finally that
    closes it, so a deploy/connect failure (confirmed live: this is
    exactly what happened on 3 of 4 calls in a real test) leaked `api`'s
    aiohttp session every time, the same class of bug this function was
    built to fix in the first place -- just not on the failure path.

    Does NOT undeploy the account itself -- see module docstring's COST
    MODEL section for why staying deployed between calls is deliberate.
    Only the RPC-level connection and client are closed here, which is
    safe to do after every call and cheap to re-open next time (a fresh
    RPC connection to an already-deployed, already-connected account is
    fast -- it's the deploy/broker-connect step that's slow, see
    _deploy_and_connect()'s wait_deployed()/wait_connected() calls,
    neither of which is repeated once an account is already DEPLOYED/
    CONNECTED).
    """
    api, account = await _get_or_create_metaapi_account(user_id)
    try:
        connection = await _deploy_and_connect(account, deploy_timeout_seconds=deploy_timeout_seconds)
        try:
            yield connection
        finally:
            try:
                await connection.close()
            except Exception:
                pass
    finally:
        try:
            await api.close()
        except Exception:
            pass


def _mt_connection_result(connected, status, account_status, error,
                           buying_power=0.0, cash=0.0, equity=0.0, broker_name="",
                           environment=None, leverage=None):
    """Builds check_user_mt_connection()'s return dict -- one place for
    the shape so all three outcomes below (connected/deploying/failed)
    stay consistent. `status` is the field added 2026-09-02 (task #238
    follow-up); `environment` is the field added 2026-09-08 (task #305)
    -- "live" or "demo", derived from MetaApi's own account_information.
    type when connected==True, None otherwise (see LIVE TRADING GATE note
    near the top of this file for why this is never the source of truth
    for the actual execution gate, only a display label). `leverage`
    (added 2026-09-18, SaaS pre-funding sanity audit) is this account's
    REAL leverage as reported by MetaApi -- None unless connected==True.
    Used by saas_broker_factory.get_user_mt_bridge_leverage() so
    saas_decision_engine.py can pass this account's actual leverage into
    risk_engine.calculate_trade_amount()'s risk-based sizing path instead
    of guessing or hardcoding one -- see that function's docstring for
    why sizing a leveraged MT4/5 CFD without knowing the account's real
    leverage silently reintroduces oversized-risk bugs.
    `connected`/`account_status`/etc. are unchanged from the original
    shape so existing callers (saas_broker_factory.py, saas_app.py) that
    only read `connected`/`error` keep working as-is."""
    return {
        "connected": connected,
        "status": status,
        "account_status": account_status,
        "trading_blocked": not connected,
        "buying_power": buying_power,
        "cash": cash,
        "equity": equity,
        "broker_name": broker_name,
        "environment": environment,
        "leverage": leverage,
        "error": error,
    }


async def check_user_mt_connection(user_id):
    """
    Connects (deploying the account if needed -- see module docstring
    for why this deliberately does NOT undeploy afterward) and reads
    account info. Mirrors saas_broker_factory.py's
    check_user_etoro_connection()/check_user_alpaca_connection() return
    shape so this can be plugged into the same "Test Connection" UI
    pattern in Phase 2 without changing that shape.

    FIX 2026-09-02 (task #238 follow-up -- first-connect UX): previously
    returned only a flat connected=True/False, with no way to tell
    "still deploying -- completely normal on a brand-new account, try
    again shortly" apart from "actually broken" (bad password/server,
    deploy genuinely failed). Live-tested 2026-09-02: a fresh MetaApi
    account took ~16 minutes end-to-end for its first real broker
    connection -- every check during that window would previously have
    reported a flat, unqualified "connected": False, indistinguishable
    from a real error to any caller/UI (saas_app.py was showing the raw
    exception text as "Connection failed", which is exactly wrong for a
    still-deploying account). Now adds a `status` field -- "connected",
    "deploying", or "failed" -- by inspecting account.state (confirmed
    live via the installed SDK's State Literal,
    clients/metaapi/metatrader_account_client.py) whenever the connect
    attempt raises: state in _FAILED_ACCOUNT_STATES means deployment
    genuinely, terminally failed; anything else (CREATED/DEPLOYING/
    DEPLOYED-but-not-yet-broker-connected/unrecognized) is treated as
    still in progress, not a hard failure. This also covers the
    DISCONNECTED_FROM_BROKER connection_status case, which looks
    identical to a real bad-password rejection at this layer on a
    first-ever connect -- if credentials are genuinely wrong, this will
    keep reporting "deploying" on every retry rather than ever
    resolving, which the UI should surface after enough repeated
    attempts (see saas_app.py's Test Connection button).

    Uses _CONNECTION_CHECK_TIMEOUT_SECONDS (short) rather than the full
    _DEPLOY_TIMEOUT_SECONDS (5 min per stage) _deploy_and_connect() gives
    execute_buy()/execute_sell_close()/get_user_mt_positions() -- this
    function backs a synchronous "Test Connection" UI button/HTTP
    request that cannot block for minutes, so it returns fast instead
    and expects the caller to check again later. Deploying is idempotent
    (see _deploy_and_connect()'s own state check), so polling this
    repeatedly while an account spins up is safe and cheap.
    """
    try:
        api, account = await _get_or_create_metaapi_account(user_id)
    except Exception as e:
        # No credentials, no server name saved, etc. -- a real
        # configuration problem, not a timing issue, so this is always
        # "failed" (there is no account yet to be "still deploying").
        # FIX 2026-09-03 (#257): don't show raw exception text to users --
        # see engines/broker_error_messages.py.
        return _mt_connection_result(False, "failed", None, friendly_broker_error_message("MT4/5", e))

    try:
        try:
            connection = await _deploy_and_connect(
                account, deploy_timeout_seconds=_CONNECTION_CHECK_TIMEOUT_SECONDS,
            )
        except Exception as e:
            state = getattr(account, "state", None)
            status = "failed" if state in _FAILED_ACCOUNT_STATES else "deploying"
            # FIX 2026-09-03 (#257): don't show raw exception text to
            # users -- see engines/broker_error_messages.py. Note this
            # branch's message is only ever shown to the user when
            # status=="failed" (saas_app.py's "deploying" branch never
            # reads .get("error")), but it's sanitized either way for
            # consistency and in case a future caller does read it.
            return _mt_connection_result(False, status, state, friendly_broker_error_message("MT4/5", e))

        try:
            info = await connection.get_account_information()
            # FIX 2026-09-08 (task #305, LIVE TRADING GATE): correct the
            # credential row's stored environment label from MetaApi's
            # own ground truth now that we actually know it -- see
            # tenant.update_broker_environment()'s docstring for why this
            # is purely a display-label fix, never consulted for the
            # actual execution gate. Best-effort: a DB hiccup here must
            # never turn a successful "Test Connection" into a reported
            # failure.
            detected_environment = "live" if _mt_is_live_account_type(info.get("type")) else "demo"
            try:
                tenant.update_broker_environment(user_id, BROKER_CODE, detected_environment)
            except Exception:
                pass
            return _mt_connection_result(
                True, "connected", "CONNECTED", None,
                buying_power=float(info.get("freeMargin", 0) or 0),
                cash=float(info.get("balance", 0) or 0),
                equity=float(info.get("equity", 0) or 0),
                broker_name=info.get("broker", ""),
                environment=detected_environment,
                leverage=float(info.get("leverage") or 0) or None,
            )
        except Exception as e:
            state = getattr(account, "state", None)
            status = "failed" if state in _FAILED_ACCOUNT_STATES else "deploying"
            return _mt_connection_result(False, status, state, str(e))
        finally:
            try:
                await connection.close()
            except Exception:
                pass
    finally:
        try:
            await api.close()
        except Exception:
            pass


async def get_user_mt_positions(user_id):
    """Returns MetaApi's raw position list. Does not undeploy -- see
    module docstring's COST MODEL section."""
    async with _mt_connection(user_id) as connection:
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
    async with _mt_connection(user_id) as connection:
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
    execute_buy() above.

    LIVE TRADING GATE (task #305, 2026-09-08): same check as
    execute_buy_by_usd_amount() -- see this file's module-level note near
    the top. Applied symmetrically to closes for consistency with how
    Alpaca/Binance/eToro's own _is_live() checks are re-verified on every
    call including sells (see saas_broker_factory.py); accepted tradeoff
    there is that reverting Lock 1 after a real position was opened means
    the automated close can no longer fire either -- not fixed here, just
    matched, since changing that tradeoff is out of scope for this task.
    """
    async with _mt_connection(user_id) as connection:
        account_info = await connection.get_account_information()
        if _mt_is_live_account_type(account_info.get("type")) and not _user_has_live_trading_enabled(user_id):
            raise LiveTradingNotEnabledError(
                f"MT4/5 account for user {user_id} is real-money "
                f"(ACCOUNT_TRADE_MODE_REAL) but allow_live_trading is not enabled."
            )
        return await connection.close_position(position_id=position_id)


async def disconnect_user_mt_account(user_id):
    """
    THE ONLY function in this file that undeploys. Call this when a user
    explicitly disconnects/removes their MT4/MT5 broker connection from
    account settings -- not after every trade or check (see module
    docstring's COST MODEL section for why). Best-effort: swallows
    errors from undeploy() itself so a flaky MetaApi call can't block a
    user from disconnecting in our own UI. Closes the `api` client
    afterward too (task #238) -- unlike the functions above, this
    doesn't open an RPC connection at all, but the client itself still
    needs closing.
    """
    api, account = await _get_or_create_metaapi_account(user_id)
    try:
        await account.undeploy()
    except Exception:
        pass
    try:
        await api.close()
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

    # INDICES (task #390-393) -- added alongside FOREX/COMMODITIES as a
    # 5th asset class. THESE ARE UNVERIFIED GUESSES, more so than any
    # other entry in this table: index symbol names AND lot/contract-size
    # conventions vary far more broker-to-broker than commodities do (the
    # note above about "SpotCrude vs a blind guess at a similarly-named
    # symbol" applies here even more strongly). The names below match a
    # common raw/ECN-broker convention, but MUST be checked against
    # whatever MT4/5 broker a real account actually connects through
    # (symbols() lookup or the broker's own contract specification page)
    # before enabling real-money INDICES trading via MT4/5. A wrong
    # contract-size assumption in _compute_lot_size() below would size a
    # real position wrong, not just fail to find the symbol.
    "^GSPC": "US500",    # S&P 500
    "^IXIC": "NAS100",   # Nasdaq 100
    "^DJI": "US30",      # Dow Jones Industrial Average
    "^FTSE": "UK100",    # FTSE 100
    "^GDAXI": "GER40",   # DAX 40
    "^N225": "JPN225",   # Nikkei 225
}


def resolve_mt_symbol(project_ticker):
    """
    Translate one of this project's own tickers (data/asset_universe.py
    -- yfinance-style, e.g. "EURUSD=X", "GC=F", or the "^"-prefixed
    INDICES convention, e.g. "^GSPC") into the real MT symbol name this
    broker uses (e.g. "EURUSD", "XAUUSD", "US500"). Mirrors
    etoro_broker.resolve_project_ticker()'s exact same job for eToro.
    Every "^"-prefixed ticker this project trades is listed explicitly in
    _MT_TICKER_OVERRIDES above (index symbol names vary far more
    broker-to-broker than the "=X"/"=F" fallback below could safely
    guess), so the override lookup is what actually resolves indices --
    the endswith("=X")/endswith("=F") fallback below never sees them.
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

    Returns None if usd_amount is too small to reach this symbol's
    minimum lot size at this account's leverage -- saas_decision_engine.py
    should treat that the same as any other "skip this trade" gate, not
    attempt the order. Otherwise returns a structured dict --
    {"position_id", "executed_price", "lot_size", "raw"} -- matching the
    same shape convention saas_broker_factory.buy_etoro_for_user() uses,
    rather than MetaApi's raw response, which does NOT include a fill
    price (confirmed live 2026-09-02, see test_mt_buy.py's first real
    order: the response only carried stringCode/orderId/positionId/
    timestamps). "executed_price" here is therefore the quote price used
    to compute the lot size, not a broker-confirmed fill price -- market
    IOC orders (this symbol's only fillingMode) fill at or extremely
    close to the quoted price with negligible slippage, so this is a
    reasonable stand-in, same tradeoff every other part of this project
    already accepts when an exact fill price isn't directly available.
    position_id confirms the position genuinely opened (MetaApi returns
    stringCode="TRADE_RETCODE_DONE" with a real positionId synchronously
    for a filled market order -- no polling needed, unlike eToro).
    """
    symbol = resolve_mt_symbol(ticker)

    async with _mt_connection(user_id) as connection:
        account_info = await connection.get_account_information()

        # LIVE TRADING GATE (task #305) -- see this file's module-level
        # note near the top for the full reasoning. Checked fresh on
        # every single BUY, from MetaApi's own account type, not from
        # any stored/user-entered flag.
        if _mt_is_live_account_type(account_info.get("type")) and not _user_has_live_trading_enabled(user_id):
            raise LiveTradingNotEnabledError(
                f"MT4/5 account for user {user_id} is real-money "
                f"(ACCOUNT_TRADE_MODE_REAL) but allow_live_trading is not enabled."
            )

        lot_size = await _compute_lot_size(connection, account_info, symbol, usd_amount)
        if lot_size is None:
            return None

        price_data = await connection.get_symbol_price(symbol)
        quote_price = float(price_data.get("ask") or price_data.get("bid") or 0)

        order = await connection.create_market_buy_order(
            symbol=symbol,
            volume=lot_size,
            stop_loss=stop_loss_price,
            take_profit=take_profit_price,
            options={"comment": "OrderTradeAI"},
        )

    position_id = order.get("positionId") if order.get("stringCode") == "TRADE_RETCODE_DONE" else None

    return {
        "position_id": position_id,
        "executed_price": quote_price if position_id else None,
        "lot_size": lot_size,
        "raw": order,
    }


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
