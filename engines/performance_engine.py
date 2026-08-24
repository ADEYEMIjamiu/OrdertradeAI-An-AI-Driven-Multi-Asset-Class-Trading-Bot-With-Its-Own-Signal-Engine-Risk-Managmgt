"""
Performance Engine — single source of truth for "did this strategy make money."

Design principle: every other part of this dashboard (session state counters,
Alpaca's live position list, etc.) can drift, reset, or disagree with itself.
The trade_journal.db table is the one thing every execution path is meant to
write to. This engine reads ONLY from that table and does FIFO matching of
BUY fills to SELL fills, per ticker, to reconstruct actual closed round-trip
trades with real realized profit/loss — not guesses.

This replaces the previous calculate_performance() in app.py, which counted
every SELL as a "win" (since it only checked that the notional trade amount
was positive, which is always true) and never actually computed profit.
"""

from collections import defaultdict, deque
from datetime import datetime

from trade_journal import load_trade_journal
from data.asset_universe import get_enabled_symbols
from config import CRYPTO_VALIDATION_START


def _load_trades_chronological():
    """
    load_trade_journal() returns rows newest-first (ORDER BY id DESC).
    FIFO matching needs oldest-first, so reverse it here.
    """
    rows = load_trade_journal()
    columns = [
        "time", "ticker", "action", "price", "shares", "amount",
        "confidence", "trend_score", "reason", "mode",
    ]
    trades = [dict(zip(columns, row)) for row in rows]
    trades.reverse()
    return trades


def _match_round_trips(trades):
    """
    FIFO-match BUY fills to SELL fills per ticker.

    Returns a list of closed round-trip trades, each with:
        ticker, entry_price, exit_price, shares, pnl, pnl_percent,
        entry_time, exit_time
    Any BUY fills left unmatched (still open) are simply not included —
    they are open positions, not closed trades, and shouldn't count
    toward win rate.
    """
    open_lots = defaultdict(deque)  # ticker -> deque of {shares, price, time}
    closed_trades = []

    for trade in trades:
        ticker = trade["ticker"]
        action = str(trade["action"]).upper()
        shares = float(trade["shares"] or 0)
        price = float(trade["price"] or 0)

        if shares <= 0 or price <= 0:
            continue  # skip malformed/partial rows rather than corrupt the stats

        if action == "BUY":
            open_lots[ticker].append({
                "shares": shares,
                "price": price,
                "time": trade["time"],
            })

        elif action == "SELL":
            remaining_to_sell = shares
            lots = open_lots[ticker]

            while remaining_to_sell > 1e-9 and lots:
                lot = lots[0]
                matched_shares = min(lot["shares"], remaining_to_sell)

                pnl = (price - lot["price"]) * matched_shares
                cost_basis = lot["price"] * matched_shares
                pnl_percent = (pnl / cost_basis * 100) if cost_basis > 0 else 0

                closed_trades.append({
                    "ticker": ticker,
                    "entry_price": lot["price"],
                    "exit_price": price,
                    "shares": matched_shares,
                    "pnl": pnl,
                    "pnl_percent": pnl_percent,
                    "entry_time": lot["time"],
                    "exit_time": trade["time"],
                    # 2026-08-24: the SELL's own "reason" (AI SELL Signal,
                    # STOP_LOSS, TAKE_PROFIT, BREAKEVEN_STOP, PARTIAL_PROFIT,
                    # a TRAILING/TIME LIMIT variant, etc.) -- carried through
                    # so calculate_strategy_breakdown() below can group
                    # realized P&L by what actually closed each trade,
                    # not just report one all-up total.
                    "exit_reason": trade.get("reason") or "",
                })

                lot["shares"] -= matched_shares
                remaining_to_sell -= matched_shares

                if lot["shares"] <= 1e-9:
                    lots.popleft()
            # Any remaining_to_sell here means a SELL with no matching BUY in
            # the journal (e.g. a position opened before logging existed).
            # We don't fabricate a fake entry price for it -- just skip it.

    return closed_trades, open_lots


def get_closed_trades_and_open_lots():
    """
    Public entry point for other engines (e.g. the performance digest)
    that need the same FIFO-matched closed trades / open lots used
    below, without duplicating the load-and-match logic themselves or
    reaching into this module's underscore-prefixed internals directly.
    """
    trades = _load_trades_chronological()
    return _match_round_trips(trades)


def get_open_positions_cost_basis():
    """
    Returns {ticker: {"shares": total_open_shares, "cost_basis": total_$_paid}}
    for every position still open (unmatched BUY lots), derived straight
    from the trade journal via FIFO. Works for stocks and crypto alike,
    since it only cares about BUY/SELL rows, not which broker placed them.

    Crypto lots opened before CRYPTO_VALIDATION_START are excluded. This
    is the position-cap / exposure counterpart to the closed-trade
    filtering already applied in digest_engine.py and
    _filter_pre_pyramiding_fix_crypto() below -- without it, this was the
    one remaining place a pre-pyramiding-fix crypto lot could sit
    "open" forever (e.g. a SELL that silently failed to journal) and
    count toward the live crypto position cap and exposure gate, with no
    independent live-broker check to catch it the way stocks/forex/
    commodities now have. Stocks are unaffected -- this only touches
    "-USD" tickers.
    """
    _, open_lots = get_closed_trades_and_open_lots()

    result = {}
    for ticker, lots in open_lots.items():
        is_crypto = str(ticker).upper().endswith("-USD")
        total_shares = 0.0
        total_cost = 0.0
        for lot in lots:
            if is_crypto:
                lot_time = _parse_time(lot["time"])
                if lot_time is not None and lot_time < CRYPTO_VALIDATION_START:
                    continue
            total_shares += lot["shares"]
            total_cost += lot["shares"] * lot["price"]
        if total_shares > 1e-9:
            result[ticker] = {"shares": total_shares, "cost_basis": total_cost}

    return result


def calculate_performance_metrics():
    """
    Returns a dict of real performance metrics computed from closed,
    FIFO-matched round-trip trades in the trade journal.
    """
    closed_trades, open_lots = get_closed_trades_and_open_lots()

    if not closed_trades:
        return {
            "trades_closed": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": None,
            "expectancy": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "max_drawdown": 0.0,
            "closed_trades": [],
        }

    wins = [t for t in closed_trades if t["pnl"] > 0]
    losses = [t for t in closed_trades if t["pnl"] <= 0]

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    total_pnl = gross_profit - gross_loss

    win_rate = (len(wins) / len(closed_trades)) * 100
    average_win = (gross_profit / len(wins)) if wins else 0.0
    average_loss = (gross_loss / len(losses)) if losses else 0.0

    # Profit factor: gross profit / gross loss. None (undefined) if there
    # have been no losing trades yet, rather than showing a misleading
    # infinite or zero value.
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    # Expectancy: average $ P&L per trade, the number that matters most
    # for "is this strategy worth running."
    expectancy = total_pnl / len(closed_trades)

    # Max drawdown from the cumulative realized-P&L curve of closed trades,
    # in the order they closed.
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for t in closed_trades:
        cumulative += t["pnl"]
        peak = max(peak, cumulative)
        drawdown = peak - cumulative
        max_drawdown = max(max_drawdown, drawdown)

    return {
        "trades_closed": len(closed_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "average_win": average_win,
        "average_loss": average_loss,
        "max_drawdown": max_drawdown,
        "closed_trades": closed_trades,
    }


def _ticker_asset_class_map():
    """Same lookup engines/digest_engine.py uses -- duplicated here rather
    than imported from there to avoid a circular import (digest_engine
    already imports get_closed_trades_and_open_lots from this module)."""
    return {
        asset["symbol"]: asset["asset_class"]
        for asset in get_enabled_symbols()
    }


def _classify_unknown_ticker(ticker):
    ticker = str(ticker).upper()
    if ticker.endswith("-USD"):
        return "CRYPTO"
    if ticker.endswith("=X"):
        return "FOREX"
    if ticker.endswith("=F"):
        return "COMMODITIES"
    return "US_STOCKS"


def _parse_time(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _filter_pre_pyramiding_fix_crypto(closed_trades):
    """
    Drop crypto round-trips whose ENTRY (the BUY that opened the lot)
    happened before CRYPTO_VALIDATION_START -- the same pre-pyramiding-fix
    exclusion engines/digest_engine.py already applies (see the comment
    on CRYPTO_VALIDATION_START in config.py for the full story). Without
    this, Sharpe/Sortino and the monthly breakdown below would silently
    include the old bulk-pyramided crypto trades and understate
    performance with data from a bug that's already been fixed.
    Stocks/forex/commodities never had this bug, so they pass through.
    """
    ticker_map = _ticker_asset_class_map()

    def _is_valid(trade):
        asset_class = ticker_map.get(
            trade["ticker"], _classify_unknown_ticker(trade["ticker"])
        )
        if asset_class != "CRYPTO":
            return True

        entry_time = _parse_time(trade["entry_time"])
        if entry_time is None:
            return True  # malformed/missing timestamp -- don't silently drop it

        return entry_time >= CRYPTO_VALIDATION_START

    return [t for t in closed_trades if _is_valid(t)]


def calculate_risk_adjusted_metrics():
    """
    Sharpe and Sortino ratios computed from the per-trade percent-return
    distribution of the same FIFO-matched closed trades used by
    calculate_performance_metrics() above.

    These are PER-TRADE ratios, not annualized daily-return Sharpe/Sortino.
    Trades don't land on a fixed daily cadence, so annualizing by an
    assumed trade frequency would manufacture false precision this early
    in the validation run. Risk-free rate is treated as 0 -- at this
    account size and time horizon it wouldn't move the number enough to
    matter, and fabricating a benchmark rate would be less honest than
    just being explicit that it's excluded.

    Returns None for both ratios (with the actual sample size still
    reported) when there are fewer than 2 closed trades, since a
    standard deviation from a single data point isn't meaningful.
    """
    closed_trades, _ = get_closed_trades_and_open_lots()
    closed_trades = _filter_pre_pyramiding_fix_crypto(closed_trades)
    n = len(closed_trades)

    if n < 2:
        return {"sharpe_ratio": None, "sortino_ratio": None, "sample_size": n}

    returns = [t["pnl_percent"] / 100 for t in closed_trades]
    mean_return = sum(returns) / n

    variance = sum((r - mean_return) ** 2 for r in returns) / (n - 1)
    std_dev = variance ** 0.5
    sharpe_ratio = (mean_return / std_dev) if std_dev > 0 else None

    # Sortino: semi-deviation from a 0% target, computed over ALL trades
    # (not just the losers) in the denominator -- the standard definition,
    # so a strategy with zero losing trades so far doesn't produce an
    # undefined/divide-by-zero ratio the way profit factor does.
    downside_sq_sum = sum(min(r, 0.0) ** 2 for r in returns)
    downside_deviation = (downside_sq_sum / n) ** 0.5
    sortino_ratio = (mean_return / downside_deviation) if downside_deviation > 0 else None

    return {
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "sample_size": n,
    }


def calculate_monthly_returns():
    """
    Realized P&L grouped by calendar month, keyed off each closed trade's
    exit_time (when the SELL that closed it actually filled). Lets you see
    whether performance is trending up or down over time instead of only
    ever seeing one all-time total.

    Journal timestamps are stored as "YYYY-MM-DD HH:MM:SS" strings (see
    trade_journal.log_trade), so the first 7 characters are always a
    "YYYY-MM" key -- no datetime parsing needed. Rows with a missing or
    malformed timestamp are skipped rather than guessed at.

    Returns a list of {"month", "trades_closed", "wins", "losses",
    "win_rate", "total_pnl"} dicts, sorted oldest month first.
    """
    closed_trades, _ = get_closed_trades_and_open_lots()
    closed_trades = _filter_pre_pyramiding_fix_crypto(closed_trades)

    months = defaultdict(list)
    for t in closed_trades:
        month_key = str(t.get("exit_time") or "")[:7]
        if len(month_key) != 7:
            continue
        months[month_key].append(t)

    result = []
    for month_key in sorted(months.keys()):
        trades = months[month_key]
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]

        result.append({
            "month": month_key,
            "trades_closed": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
            "total_pnl": sum(t["pnl"] for t in trades),
        })

    return result


def _normalize_exit_reason(raw_reason):
    """
    Buckets the many raw exit_reason strings that have accumulated across
    stocks and crypto into one consistent label per underlying strategy.

    The two asset classes never agreed on a format: stocks use
    underscores ("STOP_LOSS", "TRAILING_STOP"), crypto uses spaces
    ("STOP LOSS") and, for TRAILING PROFIT LOCK / TIME LIMIT EXIT,
    embeds a dynamic value in the string itself (e.g. "TRAILING PROFIT
    LOCK (peak 3.4%)", "TIME LIMIT EXIT (hard 7-day limit)") -- which
    would make every single one of those exits its own unique group if
    matched literally. This normalizes by substring so grouping is
    stable regardless of formatting or embedded numbers.
    """
    reason = str(raw_reason or "").upper()

    if "AI BUY" in reason or "AI SELL" in reason:
        return "AI Signal"
    if "BREAKEVEN" in reason:
        return "Breakeven Stop"
    if "PARTIAL" in reason:
        return "Partial Profit"
    if "TRAILING" in reason:
        return "Trailing Profit Lock"
    if "STOP LOSS" in reason or "STOP_LOSS" in reason:
        return "Stop Loss"
    if "TAKE PROFIT" in reason or "TAKE_PROFIT" in reason:
        return "Take Profit"
    if "TIME LIMIT" in reason or "TIME_LIMIT" in reason:
        return "Time Limit Exit"
    if "ROTATION" in reason or "RISK MANAGEMENT" in reason:
        return "Rotation / Manual"
    if not reason:
        return "Unknown (no reason logged)"
    return raw_reason


def calculate_strategy_breakdown():
    """
    Groups closed, FIFO-matched round-trip trades by what actually closed
    them (see _normalize_exit_reason() above), so it's possible to see
    which exit mechanism is actually making money versus which one might
    be cutting winners short or letting losers run -- instead of only
    ever seeing one blended win-rate/P&L total across every strategy at
    once.

    Excludes pre-pyramiding-fix crypto trades, same as
    calculate_risk_adjusted_metrics()/calculate_monthly_returns() above,
    so a bug that's already been fixed doesn't skew which strategy looks
    good today.

    Note: eToro (forex/commodities) exits happen broker-side once a
    stop-loss/take-profit level is hit -- this bot never places the
    closing SELL itself for those, so eToro exits aren't logged with a
    reason and won't appear broken out here. Only stocks and crypto,
    where every automatic exit is bot-initiated and journaled, get a
    real per-strategy breakdown.

    Returns a list of {"strategy", "trades_closed", "wins", "losses",
    "win_rate", "total_pnl", "average_pnl"} dicts, sorted by total_pnl
    descending (best-performing strategy first).
    """
    closed_trades, _ = get_closed_trades_and_open_lots()
    closed_trades = _filter_pre_pyramiding_fix_crypto(closed_trades)

    groups = defaultdict(list)
    for t in closed_trades:
        strategy = _normalize_exit_reason(t.get("exit_reason"))
        groups[strategy].append(t)

    result = []
    for strategy, trades in groups.items():
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in trades)

        result.append({
            "strategy": strategy,
            "trades_closed": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
            "total_pnl": total_pnl,
            "average_pnl": total_pnl / len(trades) if trades else 0.0,
        })

    result.sort(key=lambda r: r["total_pnl"], reverse=True)
    return result


if __name__ == "__main__":
    # Quick manual check: python3 -m engines.performance_engine
    metrics = calculate_performance_metrics()
    print("\n📊 PERFORMANCE SUMMARY (from trade_journal.db)")
    print(f"Trades Closed:  {metrics['trades_closed']}")
    print(f"Wins / Losses:  {metrics['wins']} / {metrics['losses']}")
    print(f"Win Rate:       {metrics['win_rate']:.2f}%")
    print(f"Total P&L:      ${metrics['total_pnl']:.2f}")
    pf = metrics["profit_factor"]
    print(f"Profit Factor:  {'N/A (no losses yet)' if pf is None else f'{pf:.2f}'}")
    print(f"Expectancy:     ${metrics['expectancy']:.2f} per trade")
    print(f"Max Drawdown:   ${metrics['max_drawdown']:.2f}")

    risk_adjusted = calculate_risk_adjusted_metrics()
    sharpe = risk_adjusted["sharpe_ratio"]
    sortino = risk_adjusted["sortino_ratio"]
    print(f"Sharpe Ratio:   {'N/A (need 2+ closed trades)' if sharpe is None else f'{sharpe:.2f}'} (per trade)")
    print(f"Sortino Ratio:  {'N/A (need 2+ closed trades)' if sortino is None else f'{sortino:.2f}'} (per trade)")
