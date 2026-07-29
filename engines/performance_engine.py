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

from trade_journal import load_trade_journal


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
    """
    _, open_lots = get_closed_trades_and_open_lots()

    result = {}
    for ticker, lots in open_lots.items():
        total_shares = sum(lot["shares"] for lot in lots)
        total_cost = sum(lot["shares"] * lot["price"] for lot in lots)
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
