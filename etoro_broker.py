"""
eToro connector -- a third broker alongside broker.py (Alpaca) and
binance_broker.py (Binance testnet).

STATUS (2026-08-05): connectivity, portfolio reads, ticker resolution,
buy, and close have all been live-tested against a real eToro Demo
account (crypto BTC round-trip and a real-world AAPL market-closed
queue both confirmed working -- see the docstrings on
_load_instrument_catalog(), buy(), and close_position() for the full
history of what was tried and what broke). Now wired into app.py as a
manual-confirm-only executor for FOREX/COMMODITIES signals (see
execute_etoro_trades() in app.py) -- still ETORO_ENVIRONMENT=demo only,
same as everywhere else in this file. buy() also now converts each
leveraged CFD position's stop-loss into a broker-side TRAILING stop right
after it fills, via set_trailing_stop() -- NOT yet live-confirmed as of
this deploy, see that function's docstring.

Defaults hard to ETORO_ENVIRONMENT=demo -- this NEVER touches a real
eToro account unless that env var is explicitly set to "real" in .env.

Key differences from the other two brokers, worth knowing before wiring
this in:
  - Alpaca and Binance both identify trades by SYMBOL (+ qty for
    Binance). eToro identifies open positions by a numeric position ID
    returned when the position was opened -- closing a position later
    requires that ID, not just the ticker. get_positions() below returns
    it as "position_id" for that reason.
  - eToro trades "instruments" by an internal instrumentId, not a plain
    ticker string like "AAPL". _get_instrument_id() resolves ticker ->
    instrumentId against a locally-cached copy of eToro's full
    instrument catalog (see _load_instrument_catalog()) -- their
    documented search/filter endpoints turned out not to actually
    filter anything when live-tested, so this fetches the whole catalog
    once and matches client-side instead.
  - eToro's order-open endpoint lives on their v2 API
    (public-api.etoro.com/api/v2), while almost everything else
    (search, rates, portfolio, and even closing a position) is v1. This
    isn't a typo -- it's exactly how eToro's own "Building an Algo
    Trading Bot" guide splits it, so it's preserved as-is here.
"""

import os
import time
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ETORO_API_KEY")
USER_KEY = os.getenv("ETORO_USER_KEY")

# Defaults to "demo" even if the env var is missing/misspelled -- fails
# safe. Only an exact "real" opts into eToro's live trading endpoint.
ENVIRONMENT = os.getenv("ETORO_ENVIRONMENT", "demo")
IS_DEMO = ENVIRONMENT != "real"

API_BASE = "https://public-api.etoro.com/api/v1"
EXECUTION_BASE_V2 = "https://public-api.etoro.com/api/v2"
EXECUTION_PREFIX = "trading/execution/demo" if IS_DEMO else "trading/execution"

# Confirmed via builders.etoro.com/learn/portfolio-management-and-positions:
# the portfolio READ endpoint also splits by environment, same idea as
# EXECUTION_PREFIX above but with its own separate path shape:
#   Demo: /trading/info/demo/portfolio
#   Real: /trading/info/portfolio
# Originally this file called the Real path unconditionally, which is
# exactly why a demo-only-scoped API key got a 403 Forbidden -- the key
# was correctly blocked from a Real-account endpoint it has no
# permission for. This was live-tested on 2026-08-02 and confirmed to
# be the actual bug (not a missing permission, as first suspected).
PORTFOLIO_PATH = "trading/info/demo/portfolio" if IS_DEMO else "trading/info/portfolio"

# Confirmed via api-portal.etoro.com/api-reference/trading--demo/modify-
# stop-loss-and-take-profit-settings-on-an-open-position (2026-08-05): the
# endpoint for editing an OPEN position's stop-loss/take-profit lives at a
# different v2 path than order open/close -- "trading/demo/positions/{id}"
# (or "trading/real/positions/{id}"), NOT under "trading/execution/demo/..."
# like EXECUTION_PREFIX above. Used only by set_trailing_stop() below.
POSITIONS_PREFIX = "trading/demo" if IS_DEMO else "trading/real"

# ticker -> instrumentId, populated on first lookup by _get_instrument_id()
_instrument_id_cache = {}

# Full ticker->instrumentId catalog, built once by _load_instrument_catalog()
# and reused for the life of the process. See that function's docstring
# for why this exists instead of a per-ticker API call.
_instrument_catalog = None


def _headers():
    return {
        "x-api-key": API_KEY,
        "x-user-key": USER_KEY,
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


def _load_instrument_catalog():
    """
    Fetch eToro's full instrument catalog once and build a
    SYMBOL -> instrumentId map out of it, cached for the life of the
    process.

    Live-tested 2026-08-02: /market-data/search (this file's original
    approach) does NOT filter by its "query" param at all -- three
    different query strings ("AAPL", "Apple", "apple") all returned an
    identical unfiltered page of results. /market-data/instruments
    (tried next, with symbol/name/query params) turned out to have the
    exact same problem: every param combination returned the identical
    16,148-item response regardless of filter. Both endpoints appear to
    just ignore their documented filter params and always return the
    entire catalog.

    Given that, the working approach is to fetch the whole catalog once
    (confirmed real shape: {"instrumentDisplayDatas": [{"instrumentID":
    1001, "instrumentDisplayName": "Apple", "symbolFull": "AAPL", ...}]})
    and do the ticker matching ourselves, client-side, by exact
    (case-insensitive) symbolFull match.

    Exact match matters: the catalog contains multiple near-duplicate
    entries per ticker for different trading sessions/currencies (e.g.
    for AAPL: instrumentID 1001 symbolFull "AAPL" [the one we want],
    plus 8754 "AAPL.RTH", 14254 "AAPL.EUR", 15569 "AAPL.24-7" -- all
    confirmed live). A substring/startswith match would wrongly grab
    one of those variants instead of the plain instrument.
    """
    global _instrument_catalog

    if _instrument_catalog is not None:
        return _instrument_catalog

    response = requests.get(
        f"{API_BASE}/market-data/instruments",
        headers=_headers(),
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

    _instrument_catalog = catalog
    return catalog


# This project's FOREX/COMMODITIES tickers (data/asset_universe.py) are
# yfinance-style, not eToro's symbolFull. Forex needed no real mapping
# -- stripping yfinance's "=X" suffix already lands on eToro's exact
# symbolFull (EURUSD=X -> EURUSD, confirmed live 2026-08-03). Commodities
# needed an explicit override: eToro doesn't use yfinance's futures-
# ticker convention (GC=F/CL=F/SI=F) at all -- confirmed live via a full
# catalog scan that eToro's plain (lowest-instrumentId, non-dated/non-
# regional-variant) commodity instruments are simply named GOLD (18),
# OIL (17), SILVER (19).
_PROJECT_TICKER_OVERRIDES = {
    "GC=F": "GOLD",
    "CL=F": "OIL",
    "SI=F": "SILVER",
}


def resolve_project_ticker(project_ticker: str) -> str:
    """
    Translate one of this project's own tickers (as used in
    data/asset_universe.py and throughout app.py -- yfinance-style, e.g.
    "EURUSD=X", "GC=F") into the symbolFull eToro's catalog actually
    uses (e.g. "EURUSD", "GOLD"). Stocks/crypto tickers ("AAPL",
    "BTC-USD") don't need this -- eToro's symbolFull already matches
    plain US stock tickers directly, but this project doesn't currently
    route stocks/crypto through eToro (see execute_etoro_trades() in
    app.py), so only the forex "=X" stripping and the commodities
    override table above are exercised in practice right now.
    """
    ticker = project_ticker.upper().strip()

    if ticker in _PROJECT_TICKER_OVERRIDES:
        return _PROJECT_TICKER_OVERRIDES[ticker]

    if ticker.endswith("=X") or ticker.endswith("=F"):
        return ticker.split("=")[0]

    return ticker


# Live-tested 2026-08-03: eToro rejected every FOREX/COMMODITIES order this
# project tried with "Initial Leveraged Position Amount is under the
# minimum... MinimumPositionAmount: 1000 (Dollars)" -- confirmed via
# eToro's own support chat that this $1000 floor is the LEVERAGED notional
# (Invested Amount x Leverage), not the actual cash required. The old code
# sent "leverage": 1, which forces Invested Amount == Leveraged Amount,
# meaning every trade needed a full $1000 in real cash -- far above this
# project's normal $100-$1000 sizing (config.MIN_TRADE_AMOUNT/
# MAX_TRADE_AMOUNT) and not viable for trading with limited capital.
# leverage=10 was confirmed live to fill a real $100 EURUSD buy
# (100 * 10 = 1000, exactly eToro's floor -- positionID 3574717554,
# statusID 3 Filled, errorCode 0). Do not lower this without re-testing
# that boundary; do not raise it casually either, since commodities
# typically have lower max-leverage caps than forex under standard
# retail-CFD regulation and a too-high value risks a different rejection.
ETORO_LEVERAGE = 10

# eToro also rejects any order with leverage > 1 unless a stopLossRate
# (an actual price level, not a percentage) is included -- confirmed live
# 2026-08-03: "StopLossRate must be provided when Leverage is greater than
# 1 or for SellShort transactions." buy() below computes this from the
# current ask price using these percentages, mirroring config.py's
# STOP_LOSS/TAKE_PROFIT (3%/5%) used elsewhere in this project for stocks
# and crypto, so the real broker-enforced stop on the leveraged CFD lines
# up with what the rest of the project already expects for this ticker.
ETORO_STOP_LOSS_PCT = 0.03
ETORO_TAKE_PROFIT_PCT = 0.05

# 2026-08-05: a user reviewing live positions noticed the obvious problem
# with a FIXED stop-loss/take-profit band -- a position can rally partway
# toward its take-profit, showing a real paper profit, then reverse all the
# way back past entry and close at a loss, having given back every bit of
# that unrealised gain because nothing locks it in along the way. eToro's
# own API turns out to support a real broker-side fix for exactly this:
# PATCH .../positions/{id} accepts stopLossType="trailing", which its own
# docs describe as moving the stop-loss up whenever price makes a new high,
# keeping the same distance from the peak that the original stop-loss had
# from the entry price. That means once this is set, eToro's own servers
# lock in progressively more profit as price rises -- no local polling loop
# needed, same as how the fixed stop-loss/take-profit was already enforced
# broker-side, not by this bot watching prices. See set_trailing_stop()
# and buy() below for how this gets applied. Toggle this off (without
# touching buy()'s core order logic) if the trailing conversion below ever
# needs to be disabled for debugging.
ETORO_USE_TRAILING_STOP = True


def _is_forex_or_commodity_ticker(project_ticker: str) -> bool:
    """
    True for this project's forex/commodities tickers ("EURUSD=X",
    "GC=F", etc.), False for stocks/crypto ("AAPL", "BTC-USD"). Used by
    buy() to decide whether to apply ETORO_LEVERAGE/stop-loss handling --
    based on the project's own yfinance-style ticker suffix convention
    (data/asset_universe.py), not an eToro API call, so it's free to call
    on every buy().
    """
    upper = project_ticker.upper().strip()
    return upper in _PROJECT_TICKER_OVERRIDES or upper.endswith(("=X", "=F"))


def _get_instrument_id(ticker: str):
    """
    Resolve a ticker to eToro's internal instrumentId, via the
    locally-cached full catalog (see _load_instrument_catalog()).
    Cached per-ticker too, so repeat lookups are a plain dict get.

    Accepts either eToro's own symbolFull directly ("AAPL", "GOLD") or
    one of this project's own tickers ("GC=F") -- resolve_project_ticker()
    translates the latter first, and is a harmless no-op for the former.
    """
    ticker = resolve_project_ticker(ticker)

    if ticker in _instrument_id_cache:
        return _instrument_id_cache[ticker]

    catalog = _load_instrument_catalog()
    instrument_id = catalog.get(ticker)

    if instrument_id is None:
        raise ValueError(
            f"eToro instrument catalog has no exact symbolFull match for "
            f"'{ticker}'. It may be listed under a different symbol, or "
            f"not available on eToro at all."
        )

    _instrument_id_cache[ticker] = instrument_id
    return instrument_id


def get_current_price(ticker: str) -> float:
    """
    Current price for a ticker, via eToro's rates endpoint -- specifically
    the ask price (what a BUY would actually execute near), used by buy()
    below to compute a real stopLossRate/takeProfitRate for leveraged
    orders.

    Live-tested 2026-08-03: the real response has no "lastPrice" field at
    all (contrary to what this function originally assumed) -- it's
    {"rates": [{"instrumentID": ..., "ask": ..., "bid": ...,
    "lastExecution": ..., ...}]}. This was never actually exercised until
    buy() needed a real price for stopLossRate, which is how the bug
    surfaced.
    """
    instrument_id = _get_instrument_id(ticker)

    response = requests.get(
        f"{API_BASE}/market-data/instruments/rates",
        params={"instrumentIds": instrument_id},
        headers=_headers(),
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    rates = data.get("rates", [])

    if not rates:
        raise ValueError(f"No rates in eToro response for {ticker}: {data!r}")

    price = rates[0].get("ask")
    if price is None:
        raise ValueError(f"No ask price in eToro rates response for {ticker}: {rates[0]!r}")

    return float(price)


def _fetch_client_portfolio():
    """
    GET the portfolio endpoint and unwrap the response.

    Live-tested 2026-08-02: the real response shape is NOT the flat
    {"positions": [...], "equity": ...} shown in eToro's own docs
    example -- everything is actually nested one level deeper under a
    "clientPortfolio" key, and there's no separate "equity" field at
    all. The balance field is called "credit" (confirmed live: matched
    the $101,320.08 shown on the actual eToro Demo dashboard for this
    account, with zero open positions). Real response:
        {"clientPortfolio": {"positions": [...], "credit": 101320.08, ...}}
    This helper centralises that unwrapping so every caller doesn't
    have to repeat it.
    """
    response = requests.get(
        f"{API_BASE}/{PORTFOLIO_PATH}",
        headers=_headers(),
        timeout=25,
    )
    response.raise_for_status()
    return response.json().get("clientPortfolio", {})


def check_broker_connection():
    """
    Validate the eToro connection. Mirrors broker.check_broker_connection()
    (Alpaca) / binance_broker.check_broker_connection() shape so the
    dashboard can display all three consistently, once this is wired in.
    """
    try:
        portfolio = _fetch_client_portfolio()

        # "credit" is the real field name for account balance (see
        # _fetch_client_portfolio() docstring) -- this is cash/buying
        # power when there are open positions using some of it. There's
        # no separate distinct "equity incl. unrealised P&L" field
        # confirmed yet, so equity == credit for now (accurate whenever
        # there are no open positions; once positions are open, this
        # will need refining using each position's netProfit).
        credit = float(portfolio.get("credit", 0.0))
        positions = portfolio.get("positions", [])
        unrealized_pnl = sum(float(p.get("netProfit", 0) or 0) for p in positions)
        equity = credit + unrealized_pnl

        return {
            "connected": True,
            "account_status": "DEMO" if IS_DEMO else "REAL",
            "trading_blocked": False,
            "buying_power": credit,
            "cash": credit,
            "equity": equity,
            "market_open": True,  # eToro spans multiple asset classes with different hours; refine per-instrument later if needed
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


def get_positions():
    """
    Return current open eToro positions as
    [{"symbol": ..., "qty": ..., "position_id": ...}, ...].

    Unlike binance_broker.get_positions() (wallet balances) or Alpaca's
    position list (symbol-keyed), eToro positions are ID-keyed -- the
    position_id here is required to close the position later via
    close_position().

    Field names confirmed live 2026-08-02 from a real opened BTC
    position -- eToro's own "Position Details" docs describe different
    names (instrumentName, investedAmount, positionId, netProfit,
    currentRate) than what the API actually returns
    (instrumentID, amount, positionID, no netProfit/currentRate at all
    on a freshly-opened position). "symbol" is resolved back from
    instrumentID via the same catalog _get_instrument_id() uses, since
    the raw position has no ticker string on it, only the numeric ID.
    net_profit/current_price aren't available from this endpoint at all
    -- leaving them None rather than guessing a field name that doesn't
    exist; compute current_price via get_current_price() separately if
    needed.
    """
    try:
        portfolio = _fetch_client_portfolio()
        raw_positions = portfolio.get("positions", [])
        positions = []

        catalog = _load_instrument_catalog()
        id_to_symbol = {v: k for k, v in catalog.items()}

        for p in raw_positions:
            positions.append({
                "symbol": id_to_symbol.get(p.get("instrumentID")),
                "qty": float(p.get("amount") or 0),
                "position_id": p.get("positionID"),
                "direction": "LONG" if p.get("isBuy") else "SHORT",
                "open_price": p.get("openRate"),
                "current_price": None,
                "net_profit": None,
            })

        return positions

    except Exception as e:
        print(f"Error fetching eToro positions: {e}")
        return []


def find_position_by_symbol(project_ticker: str):
    """
    Find the (first) open position matching a project ticker (e.g.
    "GC=F", "EURUSD=X"), by resolving it to eToro's instrumentId and
    matching directly against each open position's raw instrumentID --
    deliberately not reusing get_positions()'s cached id_to_symbol
    lookup here, since that could be cold for a symbol this process
    hasn't resolved yet and silently miss a real position.

    Returns the same dict shape as a get_positions() entry, or None if
    no open position matches. Used by execute_etoro_trades() in app.py
    for both the "already holds this pair" BUY dedup check and to find
    the position_id a SELL signal needs to pass to close_position().
    """
    try:
        instrument_id = _get_instrument_id(project_ticker)
    except ValueError:
        return None

    portfolio = _fetch_client_portfolio()

    for p in portfolio.get("positions", []):
        if p.get("instrumentID") == instrument_id:
            return {
                "symbol": project_ticker,
                "qty": float(p.get("amount") or 0),
                "position_id": p.get("positionID"),
                "direction": "LONG" if p.get("isBuy") else "SHORT",
                "open_price": p.get("openRate"),
                "current_price": None,
                "net_profit": None,
            }

    return None


def buy(ticker: str, usd_amount: float):
    """
    Open a market BUY position on eToro, sized by dollar amount.
    leverage=1 explicitly, so this behaves like a normal cash buy rather
    than a leveraged CFD position -- do not change this without a
    deliberate decision, since eToro supports leverage and it changes
    the risk profile completely.

    Live-tested 2026-08-02: the immediate POST response only contains
    {"token", "orderId", "referenceId"} -- no positionId or
    executionPrice, contrary to what was originally assumed here. Order
    fill behaviour differs by asset class:
      - Crypto (24/7 market, e.g. BTC): fills within ~1-2 seconds. The
        filled position then appears in the portfolio's "positions"
        list, matched back to this call via "orderID" on the position
        equalling the orderId returned here.
      - Stocks (e.g. AAPL) placed while their exchange is closed: sit
        queued in the portfolio's "ordersForOpen" list (statusID 11)
        until the market reopens -- confirmed live over a weekend. This
        is expected broker behaviour, not a bug, and this function will
        correctly return position_id=None for that case after the
        short poll below gives up.
      - FOREX/COMMODITIES (2026-08-03): repeatedly vanished without a
        trace -- no fill, no entry in "ordersForOpen", nothing in eToro's
        own pending-orders UI, despite a real orderId coming back from
        this POST. eToro's own documented example payload
        (api-portal.etoro.com/guides/market-orders) does NOT include a
        "stopLossType" field at all; this code was previously sending
        "stopLossType": "fixed" with no accompanying stop-loss value,
        which is incomplete relative to that documented shape. Removed
        below, along with adding "symbol" to match the documented
        example exactly, since forex/commodities CFDs are exactly the
        asset classes where a regulator-mandated stop-loss config is
        most likely to be silently enforced server-side. Needs a live
        re-test to confirm this was actually the cause.

        Live-tested 2026-08-03 (immediately after the change above): eToro
        rejected a request with BOTH "symbol" and "instrumentId" set with
        a 400 -- "Exactly one of Symbol or InstrumentID must be provided.
        Both were supplied." eToro's own documented example
        (api-portal.etoro.com/guides/market-orders) shows both fields
        together, which is simply wrong against the real API (same
        category of doc-vs-reality mismatch as everything else in this
        file). Sending "symbol" was reverted; instrumentId alone is kept,
        matching what already worked for AAPL/BTC.

        Root cause of the vanishing orders finally confirmed live
        2026-08-03, via eToro's own support chat: FOREX/COMMODITIES orders
        weren't malformed at all -- they were being rejected for real
        (errorCode 797, "User is blocked from CFD - opening position",
        visible via GET /trading/info/demo/orders/{orderId}, which this
        function didn't check before) until the account's CFD
        appropriateness questionnaire was completed. After that, a second
        rejection appeared (errorCode 720): eToro enforces a $1000 MINIMUM
        LEVERAGED POSITION VALUE (amount x leverage) on these instruments,
        and the "leverage": 1 this function always sent meant that
        minimum had to be met in real cash -- unworkable for this
        project's normal $100-$1000 trade sizing. eToro support confirmed
        the $1000 figure is leveraged notional, not cash required,
        directly. Fixed below: forex/commodities tickers now use
        ETORO_LEVERAGE (10, live-confirmed sufficient) instead of 1, which
        also requires a real stopLossRate on the order (eToro: "StopLossRate
        must be provided when Leverage is greater than 1") -- computed here
        from the current ask price and ETORO_STOP_LOSS_PCT/
        ETORO_TAKE_PROFIT_PCT. Stocks/crypto tickers are untouched (still
        leverage=1, no stop-loss fields), since those were already working
        correctly and introducing leverage there would be an unrelated,
        undiscussed risk change.
    """
    instrument_id = _get_instrument_id(ticker)
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
        current_price = get_current_price(ticker)
        order_payload["stopLossRate"] = round(current_price * (1 - ETORO_STOP_LOSS_PCT), 5)
        order_payload["takeProfitRate"] = round(current_price * (1 + ETORO_TAKE_PROFIT_PCT), 5)

    response = requests.post(
        f"{EXECUTION_BASE_V2}/{EXECUTION_PREFIX}/orders",
        headers=_headers(),
        json=order_payload,
        timeout=25,
    )
    response.raise_for_status()
    order = response.json()
    order_id = order.get("orderId")

    position_id = None
    executed_price = None

    # Poll for a fill. Crypto confirms in ~1-2s; a stock order placed
    # outside market hours will never fill during this call at all, so a
    # short poll used to be enough for everything this function handled.
    # FOREX/COMMODITIES changed that: live-tested 2026-08-03, several
    # leveraged CFD orders genuinely filled (confirmed on the eToro
    # dashboard afterward) but took longer than the old 5-second window,
    # so this function returned position_id=None and app.py's
    # execute_etoro_trades() correctly withheld the Telegram "filled"
    # alert for a trade that had, in fact, filled -- not wrong, just slow
    # to notice. Leveraged CFD tickers get a longer window (15s) to catch
    # these; stocks/crypto keep the original short one since there's no
    # evidence they need longer and a stock outside market hours would
    # just waste 10 extra seconds polling for something that can't happen.
    poll_attempts = 15 if is_leveraged_cfd else 5
    for _ in range(poll_attempts):
        portfolio = _fetch_client_portfolio()
        match = next(
            (p for p in portfolio.get("positions", []) if p.get("orderID") == order_id),
            None,
        )
        if match is not None:
            position_id = match.get("positionID")
            executed_price = match.get("openRate")
            break
        time.sleep(1)

    # Once a leveraged CFD position is confirmed open, convert its stop-loss
    # to a broker-enforced trailing stop (see set_trailing_stop()'s
    # docstring for the full reasoning: this is what actually locks in
    # profit as price moves favourably, instead of the fixed 3%/5% band
    # riding all the way back down to a loss after having been up). This
    # is deliberately best-effort: if it fails for any reason, the position
    # still has the fixed stopLossRate/takeProfitRate already set by the
    # order above, so nothing is left unprotected -- it just doesn't trail.
    trailing_stop_set = False
    if is_leveraged_cfd and position_id is not None and ETORO_USE_TRAILING_STOP:
        try:
            set_trailing_stop(
                position_id,
                stop_loss_rate=order_payload["stopLossRate"],
                take_profit_rate=order_payload.get("takeProfitRate"),
            )
            trailing_stop_set = True
        except Exception as trailing_error:
            print(
                f"Could not set trailing stop for position {position_id} "
                f"({ticker}): {trailing_error}"
            )

    return {
        "position_id": position_id,
        "executed_price": executed_price,
        "trailing_stop_set": trailing_stop_set,
        "raw": order,
    }


def close_position(position_id):
    """
    Close an existing eToro position by its position_id (from
    get_positions() or the return value of buy()). Note this hits the
    v1 API base, not v2 -- that split is intentional, see module
    docstring.

    Live-tested 2026-08-02: posting an empty body ({}) fails with
    "Validation failed: -- InstrumentId: The instrument id does not
    exist" -- the endpoint requires the position's instrumentId in the
    request body even though the position is already identified by
    position_id in the URL. This looks the position up first (to get
    its instrumentID) so callers don't have to already know it.
    Confirmed working live: {"instrumentId": <id>} is sufficient: the
    position disappeared from the portfolio and credit updated
    correctly afterward.
    """
    portfolio = _fetch_client_portfolio()
    position = next(
        (p for p in portfolio.get("positions", []) if p.get("positionID") == position_id),
        None,
    )
    if position is None:
        raise ValueError(f"No open eToro position found with position_id {position_id}.")

    response = requests.post(
        f"{API_BASE}/{EXECUTION_PREFIX}/market-close-orders/positions/{position_id}",
        headers=_headers(),
        json={"instrumentId": position["instrumentID"]},
        timeout=25,
    )
    response.raise_for_status()
    return response.json()


def set_trailing_stop(position_id: int, stop_loss_rate: float, take_profit_rate: float = None):
    """
    Convert an already-open position's stop-loss into a broker-enforced
    TRAILING stop, via PATCH /api/v2/trading/demo/positions/{positionId}
    (see POSITIONS_PREFIX comment above for why this is a different path
    than the order open/close endpoints).

    eToro's own docs describe stopLossType="trailing" as: "the stop-loss
    rate moves up whenever the instrument rate goes up such that the stop
    loss is triggered from the same distance from the last peak rate as
    the distance of the stop-loss rate from the rate at the open." In
    plain terms: pass it the position's current (fixed) stop-loss rate and
    eToro's own servers take over from there, ratcheting that stop-loss up
    automatically as price makes new highs, always keeping the same
    distance below the peak instead of staying pinned to the entry price.
    No local monitoring loop needed for this -- it's enforced the same way
    the original fixed stop-loss already was, just broker-side and dynamic.

    take_profit_rate is passed through unchanged rather than cleared: the
    trailing stop protects the downside as price rises, but the
    take-profit stays in place as a hard ceiling in case of one extreme
    single move, same reasoning as the fixed-band version this replaces.

    Called by buy() immediately after a leveraged CFD position is
    confirmed open. buy() wraps this call in a try/except, deliberately,
    so that a failure here can never undo the buy that already succeeded --
    worst case the position just keeps the original fixed stop-loss
    (already proven working), not a naked position with none at all.

    NOT live-tested against a real eToro Demo position as of this deploy
    (2026-08-05) -- built directly from eToro's current published OpenAPI
    spec (v1.332.0), but this whole file's history is one long list of
    that spec disagreeing with the real API in some way once actually
    tried. The next real forex/commodities BUY after this deploys IS the
    live test; check the eToro dashboard afterward for whether the
    position's stop-loss actually starts moving up as price rises, and
    check this process's logs for "Could not set trailing stop" if it
    silently failed instead.
    """
    payload = {"stopLossType": "trailing", "stopLossRate": stop_loss_rate}
    if take_profit_rate is not None:
        payload["takeProfitRate"] = take_profit_rate

    response = requests.patch(
        f"{EXECUTION_BASE_V2}/{POSITIONS_PREFIX}/positions/{position_id}",
        headers=_headers(),
        json=payload,
        timeout=25,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    # Manual connectivity test -- run `python3 etoro_broker.py` directly
    # once ETORO_API_KEY / ETORO_USER_KEY are set in .env. Does NOT place
    # any orders; only checks the connection and prints account info.
    print(f"eToro environment: {'DEMO' if IS_DEMO else 'REAL'}")
    result = check_broker_connection()
    print(result)
