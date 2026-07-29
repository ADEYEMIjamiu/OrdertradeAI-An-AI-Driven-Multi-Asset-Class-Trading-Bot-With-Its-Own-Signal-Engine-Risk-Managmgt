"""
Performance Digest Engine — periodic (today / last N days / all-time)
performance summaries broken down by asset class.

Built on top of performance_engine's FIFO-matched closed-trade data --
the same single source of truth already used by the main Performance
section -- so this never disagrees with it. Exists so a multi-week
paper-trading validation run can be checked at a glance instead of
manually reading the trade log / Order Book every day.
"""

from datetime import datetime, timedelta

from engines.performance_engine import get_closed_trades_and_open_lots
from data.asset_universe import get_enabled_symbols


def _ticker_asset_class_map():
    """
    ticker -> asset_class, sourced from the current ASSET_UNIVERSE.
    Tickers no longer in it (disabled/removed since the trade was made)
    fall back to _classify_unknown_ticker() below.
    """
    return {
        asset["symbol"]: asset["asset_class"]
        for asset in get_enabled_symbols()
    }


def _classify_unknown_ticker(ticker):
    """
    Best-effort asset-class guess for a ticker no longer in
    ASSET_UNIVERSE, based on this project's ticker-naming conventions
    (see data/asset_universe.py): "-USD" = crypto, "=X" = forex,
    "=F" = commodities, anything else = stocks.
    """
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


def _summarize(closed_trades):
    """
    Same math as performance_engine.calculate_performance_metrics(),
    applied to an arbitrary subset of closed trades (e.g. one asset
    class, or one time window).
    """
    if not closed_trades:
        return {
            "trades_closed": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "profit_factor": None,
            "expectancy": 0.0,
        }

    wins = [t for t in closed_trades if t["pnl"] > 0]
    losses = [t for t in closed_trades if t["pnl"] <= 0]

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    total_pnl = gross_profit - gross_loss

    return {
        "trades_closed": len(closed_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(closed_trades)) * 100,
        "total_pnl": total_pnl,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "expectancy": total_pnl / len(closed_trades),
    }


def calculate_performance_digest(period_days=1):
    """
    Performance digest for the last `period_days` days (filtered by
    trade EXIT time), broken down by asset class, plus an overall row
    and a count of currently-open positions per asset class.

    period_days=None means "all time" (no date filtering).
    """
    closed_trades, open_lots = get_closed_trades_and_open_lots()

    if period_days is not None:
        cutoff = datetime.now() - timedelta(days=period_days)
        closed_trades = [
            t for t in closed_trades
            if (_parse_time(t["exit_time"]) or datetime.min) >= cutoff
        ]

    ticker_map = _ticker_asset_class_map()

    by_class_trades = {}
    for t in closed_trades:
        asset_class = ticker_map.get(
            t["ticker"], _classify_unknown_ticker(t["ticker"])
        )
        by_class_trades.setdefault(asset_class, []).append(t)

    breakdown = {
        asset_class: _summarize(trades_in_class)
        for asset_class, trades_in_class in by_class_trades.items()
    }

    open_by_class = {}
    for ticker, lot in open_lots.items():
        total_shares = sum(entry["shares"] for entry in lot)
        if total_shares <= 1e-9:
            continue
        asset_class = ticker_map.get(ticker, _classify_unknown_ticker(ticker))
        open_by_class[asset_class] = open_by_class.get(asset_class, 0) + 1

    return {
        "period_days": period_days,
        "overall": _summarize(closed_trades),
        "by_asset_class": breakdown,
        "open_positions_by_asset_class": open_by_class,
    }
