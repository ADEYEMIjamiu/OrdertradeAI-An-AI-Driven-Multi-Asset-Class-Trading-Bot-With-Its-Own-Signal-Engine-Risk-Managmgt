def _add_real_stock_and_etoro_values(allocation):
    """
    Adds real US_STOCKS (Alpaca) and FOREX/COMMODITIES (eToro) values to
    an allocation dict in place. Shared by calculate_asset_allocation()
    and calculate_asset_allocation_for_limits() below.

    Fixed 2026-08-07: both functions used to derive US_STOCKS and
    FOREX/COMMODITIES exposure entirely from the `positions` dict passed
    in (st.session_state.positions), the same local-paper leftover state
    already found to be silently stale for real Alpaca stock trades
    (LIVE_TRADING) and real eToro forex/commodities trades
    (ETORO_LIVE_TRADING) elsewhere in this project (see
    engines/risk_engine.py's 2026-08-06/07 fixes for the same class of
    bug). Real trades never touch that dict, so MAX_ASSET_CLASS_EXPOSURE
    below was silently never actually enforced for stocks or
    forex/commodities -- their allocation always looked near-zero
    regardless of real holdings. Crypto already had its own real-data
    fix (see the try/except below, pre-existing). This brings the other
    two asset classes up to the same standard, using real broker data
    exactly like execute_alpaca_trades()/execute_etoro_trades() do
    for their own position-cap checks.
    """
    from config import LIVE_TRADING, ETORO_LIVE_TRADING

    if LIVE_TRADING:
        try:
            from broker import get_open_positions
            stock_value = sum(
                float(position.market_value) for position in get_open_positions()
            )
            if stock_value > 0:
                allocation["US_STOCKS"] = allocation.get("US_STOCKS", 0) + stock_value
        except Exception:
            pass

    if ETORO_LIVE_TRADING:
        try:
            import etoro_broker
            from data.asset_universe import ASSET_UNIVERSE

            etoro_positions = etoro_broker.get_positions()
            for asset_class in ("FOREX", "COMMODITIES"):
                class_value = 0.0
                for project_ticker in ASSET_UNIVERSE.get(asset_class, {}).get("symbols", []):
                    etoro_symbol = etoro_broker.resolve_project_ticker(project_ticker)
                    for position in etoro_positions:
                        if position["symbol"] == etoro_symbol:
                            # eToro's "qty" field is the invested/margin
                            # USD amount, not a unit count -- see
                            # etoro_broker.get_positions()'s docstring.
                            class_value += float(position.get("qty") or 0)
                if class_value > 0:
                    allocation[asset_class] = allocation.get(asset_class, 0) + class_value
        except Exception:
            pass


def calculate_asset_allocation(positions, market_df):
    """
    Calculates exposure by asset class.

    `positions` (st.session_state.positions) is only actually
    authoritative for US_STOCKS when LIVE_TRADING is off and for
    FOREX/COMMODITIES when ETORO_LIVE_TRADING is off -- i.e. only when
    those asset classes are genuinely running through the local
    paper-trading engine. When the real brokers are on,
    _add_real_stock_and_etoro_values() overrides/adds the real figures
    instead. See that function's docstring for why this matters.
    """

    from config import LIVE_TRADING, ETORO_LIVE_TRADING

    # Asset classes the real-broker path below will supply instead --
    # skipped here so a stale/leftover local `positions` entry (e.g. old
    # dev-era stock positions still sitting in local_account.db, per
    # config.py's LIVE_TRADING comment) can't get ADDED on top of the
    # real figure and double-count.
    _real_data_classes = set()
    if LIVE_TRADING:
        _real_data_classes.add("US_STOCKS")
    if ETORO_LIVE_TRADING:
        _real_data_classes.update({"FOREX", "COMMODITIES"})

    allocation = {}

    for ticker, position in positions.items():
        row = market_df[market_df["Ticker"] == ticker]

        if row.empty:
            continue

        asset_class = row.iloc[0].get("Asset Class", "UNKNOWN")

        if asset_class in _real_data_classes:
            continue

        price = float(row.iloc[0]["Price ($)"])
        shares = float(position.get("shares", 0))

        value = price * shares

        allocation[asset_class] = allocation.get(asset_class, 0) + value

    # Crypto lives on Binance testnet, not in the local `positions` dict,
    # so it has to be added separately.
    try:
        import binance_broker
        crypto_value = binance_broker.get_crypto_positions_value(market_df)
        if crypto_value > 0:
            allocation["CRYPTO"] = allocation.get("CRYPTO", 0) + crypto_value
    except Exception:
        pass

    _add_real_stock_and_etoro_values(allocation)

    return allocation


def can_add_asset_class(asset_class, allocation, portfolio_value, max_asset_exposure):
    """
    Checks whether adding more exposure to an asset class is allowed.
    """

    if portfolio_value <= 0:
        return False, "Portfolio value is zero."

    current_value = allocation.get(asset_class, 0)
    current_exposure = current_value / portfolio_value

    if current_exposure >= max_asset_exposure:
        return False, f"{asset_class} exposure limit reached."

    return True, "OK"


def rank_trades_by_portfolio_fit(trade_queue):
    """
    Ranks trades by priority and score before execution.
    """

    if trade_queue.empty:
        return trade_queue

    return trade_queue.sort_values(
        by=[
            "Priority",
            "AI Trade Score",
            "Strategy Score",
            "AI Confidence %"
        ],
        ascending=False
    ).reset_index(drop=True)
    
def preview_allocation_after_trades(
    positions,
    market_df,
    executable_trades,
    portfolio_value,
    default_trade_amount=1000,
):
    """
    Preview portfolio allocation after approved trades.

    Rules:
    - Preserve the value of current open positions.
    - Do not add another preview amount for a ticker already held.
    - Use the approved Position Size when available.
    - Use default_trade_amount only as a final fallback.
    """

    preview_values = {}

    # -----------------------------------------
    # 1. Add current open-position values
    # -----------------------------------------
    for ticker, position in positions.items():
        shares = float(position.get("shares", 0))

        current_price = None

        if (
            market_df is not None
            and not market_df.empty
            and "Ticker" in market_df.columns
            and "Price ($)" in market_df.columns
        ):
            ticker_rows = market_df.loc[
                market_df["Ticker"].astype(str).eq(str(ticker))
            ]

            if not ticker_rows.empty:
                current_price = float(ticker_rows.iloc[0]["Price ($)"])

        if current_price is None:
            current_price = float(
                position.get(
                    "current_price",
                    position.get("entry_price", 0),
                )
            )

        position_value = shares * current_price

        asset_class = str(
            position.get("asset_class", "US_STOCKS")
        )

        preview_values[asset_class] = (
            preview_values.get(asset_class, 0.0)
            + position_value
        )

    # -----------------------------------------
    # 1b. Add real current crypto holdings (Binance testnet) as a baseline
    # -----------------------------------------
    crypto_held_tickers = set()
    try:
        import binance_broker
        crypto_value = binance_broker.get_crypto_positions_value(market_df)
        if crypto_value > 0:
            preview_values["CRYPTO"] = preview_values.get("CRYPTO", 0.0) + crypto_value
        crypto_held_tickers = {
            p["symbol"] for p in binance_broker.get_positions()
        }
    except Exception:
        pass

    # -----------------------------------------
    # 2. Add only genuinely new approved trades
    # -----------------------------------------
    if (
        executable_trades is not None
        and not executable_trades.empty
        and "Ticker" in executable_trades.columns
    ):
        held_tickers = {str(ticker) for ticker in positions.keys()} | crypto_held_tickers

        # filter_trades_by_portfolio_limits() only checks per-asset-class
        # exposure %, not actual cash on hand, so executable_trades can
        # still contain far more BUY rows than there is cash to fill. Cap
        # the running preview spend at what's actually available so this
        # preview can't report an impossible >100% total allocation.
        current_invested_total = sum(preview_values.values())
        remaining_cash = max(0.0, portfolio_value - current_invested_total)

        for _, row in executable_trades.iterrows():
            ticker = str(row.get("Ticker", ""))

            # Do not preview another purchase for an existing holding
            if ticker in held_tickers:
                continue

            signal = str(row.get("Signal", "")).upper()

            # Only BUY trades increase future allocation
            if signal != "BUY":
                continue

            position_size = row.get("Position Size", default_trade_amount)

            try:
                position_size = float(position_size)
            except (TypeError, ValueError):
                position_size = float(default_trade_amount)

            if position_size <= 0:
                position_size = float(default_trade_amount)

            # Stop previewing further buys once available cash runs out.
            if remaining_cash <= 0:
                continue

            position_size = min(position_size, remaining_cash)
            remaining_cash -= position_size

            asset_class = str(
                row.get("Asset Class", "US_STOCKS")
            )

            preview_values[asset_class] = (
                preview_values.get(asset_class, 0.0)
                + position_size
            )

    # -----------------------------------------
    # 3. Convert result to display table
    # -----------------------------------------
    rows = []

    for asset_class, preview_value in preview_values.items():
        allocation_percent = (
            (preview_value / portfolio_value) * 100
            if portfolio_value > 0
            else 0
        )

        rows.append(
            {
                "Asset Class": asset_class,
                "Preview Value": round(preview_value, 2),
                "Preview Allocation %": round(
                    allocation_percent,
                    2,
                ),
            }
        )

    return rows

MAX_ASSET_CLASS_EXPOSURE = {
    "US_STOCKS": 0.70,
    "CRYPTO": 0.25,
    "FOREX": 0.20,
    "COMMODITIES": 0.20,
}


def calculate_asset_allocation_for_limits(positions, market_df):
    """
    Same as calculate_asset_allocation() above, except crypto is valued
    using ONLY what the bot itself has bought (via
    risk_engine.get_bot_owned_crypto_value), not the full Binance testnet
    wallet balance.

    Used specifically by filter_trades_by_portfolio_limits() below. Using
    the full wallet value there meant pre-existing testnet dust (never
    bought by the bot) permanently pinned CRYPTO's allocation above
    MAX_ASSET_CLASS_EXPOSURE, silently vetoing every future crypto BUY
    regardless of signal quality -- with no trade message logged anywhere,
    since the rejection happens before a trade ever reaches execution.

    calculate_asset_allocation() itself is left untouched: the Asset
    Allocation table should keep showing real, full holdings.

    Fixed 2026-08-07: same real-broker-data fix as
    calculate_asset_allocation() above, for the same reason -- this is
    the function that actually feeds MAX_ASSET_CLASS_EXPOSURE below,
    which gates real trade execution, so the stale-`positions` bug here
    mattered even more than in the display-only version. See
    _add_real_stock_and_etoro_values()'s docstring for the full story.
    """
    from config import LIVE_TRADING, ETORO_LIVE_TRADING

    _real_data_classes = set()
    if LIVE_TRADING:
        _real_data_classes.add("US_STOCKS")
    if ETORO_LIVE_TRADING:
        _real_data_classes.update({"FOREX", "COMMODITIES"})

    allocation = {}

    for ticker, position in positions.items():
        row = market_df[market_df["Ticker"] == ticker]

        if row.empty:
            continue

        asset_class = row.iloc[0].get("Asset Class", "UNKNOWN")

        if asset_class in _real_data_classes:
            continue

        price = float(row.iloc[0]["Price ($)"])
        shares = float(position.get("shares", 0))

        value = price * shares

        allocation[asset_class] = allocation.get(asset_class, 0) + value

    try:
        from engines.risk_engine import get_bot_owned_crypto_value
        crypto_value = get_bot_owned_crypto_value(market_df)
        if crypto_value > 0:
            allocation["CRYPTO"] = allocation.get("CRYPTO", 0) + crypto_value
    except Exception:
        pass

    _add_real_stock_and_etoro_values(allocation)

    return allocation


def filter_trades_by_portfolio_limits(
    trade_queue,
    positions,
    market_df,
    portfolio_value,
    default_trade_amount=1000
):
    """
    Filters approved trades using portfolio asset-class exposure limits.
    """

    allocation = calculate_asset_allocation_for_limits(positions, market_df)

    approved_trades = []
    rejected_trades = []

    for _, trade in trade_queue.iterrows():
        trade = trade.copy()

        asset_class = trade.get("Asset Class", "UNKNOWN")
        signal = trade.get("Signal", "HOLD")

        if signal != "BUY":
            approved_trades.append(trade)
            continue

        projected_value = allocation.get(asset_class, 0) + default_trade_amount
        projected_exposure = projected_value / portfolio_value if portfolio_value > 0 else 0

        max_allowed = MAX_ASSET_CLASS_EXPOSURE.get(asset_class, 0.10)

        if projected_exposure > max_allowed:
            trade["Portfolio Approved"] = False
            trade["Portfolio Reason"] = (
                f"{asset_class} would become {round(projected_exposure * 100, 2)}%, "
                f"max allowed is {round(max_allowed * 100, 2)}%."
            )
            rejected_trades.append(trade)
        else:
            trade["Portfolio Approved"] = True
            trade["Portfolio Reason"] = "Portfolio limit OK."
            approved_trades.append(trade)

            allocation[asset_class] = projected_value

    return approved_trades, rejected_trades