def calculate_asset_allocation(positions, market_df):
    """
    Calculates exposure by asset class.
    """

    allocation = {}

    for ticker, position in positions.items():
        row = market_df[market_df["Ticker"] == ticker]

        if row.empty:
            continue

        asset_class = row.iloc[0].get("Asset Class", "UNKNOWN")
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
    """

    allocation = {}

    for ticker, position in positions.items():
        row = market_df[market_df["Ticker"] == ticker]

        if row.empty:
            continue

        asset_class = row.iloc[0].get("Asset Class", "UNKNOWN")
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