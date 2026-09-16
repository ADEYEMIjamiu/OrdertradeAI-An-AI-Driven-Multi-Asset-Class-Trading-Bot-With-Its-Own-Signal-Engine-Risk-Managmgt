"""
Per-user realized performance / P&L engine for the SaaS product
(post-launch-audit #262).

Mirrors engines/performance_engine.py's FIFO-matching approach -- the
single-owner bot's "did this strategy actually make money" engine --
but reads from saas_orders (engines/saas_order_manager.py) scoped to
ONE user_id at a time, across every broker/ticker they've traded. This
deliberately never touches trade_journal.db, which belongs to the
single-owner bot alone and must never mix with any SaaS user's data.

LEVERAGED-BROKER CAVEAT (same one already documented in saas_app.py's
render_open_positions() for live unrealized P&L): ETORO and MT_BRIDGE
are leveraged CFD brokers where quantity * (exit_price - entry_price)
is NOT a correct realized dollar P&L -- the margin/leverage terms
aren't captured anywhere in this journal, only the notional
quantity/price the CFD tracks. Realized $ figures below (total_pnl,
gross_profit/loss, profit_factor, expectancy, average_win/loss, max
drawdown, and every per-group $ breakdown) are therefore computed ONLY
from ALPACA (stocks) and BINANCE/KRAKEN/LUNO (crypto) fills, which trade
real, unleveraged share/coin quantities -- see _PRICED_BROKERS below. ETORO/
MT_BRIDGE closed trades are still counted, shown in the closed-trades
table, and included in win/loss/win-rate (a simple price-direction
comparison is valid regardless of leverage), just excluded from every
dollar total so one leveraged CFD trade can't silently distort a real
cash P&L figure.

FIFO matching is keyed per (ticker, broker) -- not just ticker -- since
saas_order_manager.py's own lot tracking (get_open_lot_for_user, etc.)
is scoped the same way: a user COULD in principle hold the same ticker
open on two different connected brokers at once, and matching across
brokers would pair a BUY on one account with a SELL on another,
producing a nonsense entry/exit price.
"""

from collections import defaultdict, deque
from datetime import datetime

from engines import saas_order_manager as journal

# Brokers whose fills represent real, unleveraged quantities -- see
# module docstring's LEVERAGED-BROKER CAVEAT. KRAKEN added task #365
# (2026-09-15) -- real unleveraged spot quantities, same as BINANCE, not
# a leveraged CFD like ETORO/MT_BRIDGE. LUNO added task #378 (2026-09-16)
# -- same real spot quantities; its journaled fill prices are already
# converted to USD by buy_luno_for_user() (see saas_broker_factory.py's
# LUNO section docstring), so no different treatment is needed here.
_PRICED_BROKERS = {"ALPACA", "BINANCE", "KRAKEN", "LUNO"}

_EXIT_STRATEGY_LABELS = {
    "EXIT_PROTECTION": "Stop-Loss / Take-Profit / Time Exit",
    "MAX_HOLD_TIME_EXIT": "Max Hold Time Exit",
    "PARTIAL_PROFIT": "Partial Profit Take",
}


def _label_exit_strategy(raw_strategy):
    """SELL orders are tagged with one of a small fixed set of strategy
    values by the engine that placed them (see saas_exit_engine.py and
    engines/saas_position_lifecycle_engine.py) -- map to a readable
    label, falling back to the raw value for anything unrecognized
    rather than silently dropping it."""
    raw = str(raw_strategy or "").strip()
    return _EXIT_STRATEGY_LABELS.get(raw, raw or "Unknown")


def _match_round_trips_for_user(orders):
    """
    FIFO-match BUY fills to SELL fills per (ticker, broker), same
    algorithm as engines/performance_engine.py._match_round_trips()
    but keyed by the (ticker, broker) tuple instead of ticker alone.

    Returns (closed_trades, open_lots) where closed_trades is a list of
    dicts: ticker, broker, asset_class, entry_price, exit_price,
    quantity, pnl, pnl_percent, entry_time, exit_time, exit_strategy,
    priced (bool -- whether broker is in _PRICED_BROKERS).
    """
    open_lots = defaultdict(deque)  # (ticker, broker) -> deque of {qty, price, time}
    closed_trades = []

    for order in orders:
        ticker = order["ticker"]
        broker = str(order["broker"] or "").upper()
        key = (ticker, broker)
        side = str(order["side"] or "").upper()
        qty = float(order.get("filled_quantity") or 0)
        price = float(order.get("filled_price") or 0)

        if qty <= 0 or price <= 0:
            continue  # skip malformed/partial rows rather than corrupt the stats

        if side == "BUY":
            open_lots[key].append({
                "qty": qty,
                "price": price,
                "time": order.get("updated_at") or order.get("created_at"),
            })

        elif side == "SELL":
            remaining_to_sell = qty
            lots = open_lots[key]
            priced = broker in _PRICED_BROKERS

            while remaining_to_sell > 1e-9 and lots:
                lot = lots[0]
                matched_qty = min(lot["qty"], remaining_to_sell)

                pnl = (price - lot["price"]) * matched_qty
                cost_basis = lot["price"] * matched_qty
                pnl_percent = (pnl / cost_basis * 100) if cost_basis > 0 else 0

                closed_trades.append({
                    "ticker": ticker,
                    "broker": broker,
                    "asset_class": order.get("asset_class"),
                    "entry_price": lot["price"],
                    "exit_price": price,
                    "quantity": matched_qty,
                    "pnl": pnl,
                    "pnl_percent": pnl_percent,
                    "entry_time": lot["time"],
                    "exit_time": order.get("updated_at") or order.get("created_at"),
                    "exit_strategy": _label_exit_strategy(order.get("strategy")),
                    # FIX 2026-09-09: the "exit_strategy" label above is
                    # baked in English at match-time (via
                    # _label_exit_strategy(), used elsewhere for stable
                    # English-keyed grouping in
                    # calculate_pnl_by_exit_strategy_for_user() -- left
                    # untouched to avoid re-keying that). The dashboard's
                    # eToro-style exit badges need the ORIGINAL raw code
                    # (EXIT_PROTECTION/MAX_HOLD_TIME_EXIT/PARTIAL_PROFIT/
                    # None) so saas_app.py can pick a translated label via
                    # its own i18n system instead of showing this
                    # English-only string to non-English users.
                    "exit_strategy_raw": str(order.get("strategy") or "").strip() or None,
                    "priced": priced,
                })

                lot["qty"] -= matched_qty
                remaining_to_sell -= matched_qty

                if lot["qty"] <= 1e-9:
                    lots.popleft()
            # Any remaining_to_sell here means a SELL with no matching BUY
            # in this user's journal (e.g. a position adopted/reconciled
            # rather than opened through this dashboard) -- skipped
            # rather than fabricating a fake entry price for it, same
            # policy as the single-owner bot's engine.

    return closed_trades, open_lots


def get_closed_trades_for_user(user_id):
    """Public entry point: (closed_trades, open_lots) for this user,
    FIFO-matched across every broker/ticker they've traded."""
    orders = journal.load_filled_orders_for_user_chronological(user_id)
    return _match_round_trips_for_user(orders)


def calculate_performance_metrics_for_user(user_id):
    """
    Realized performance for one SaaS user. win_rate/wins/losses/
    trades_closed cover ALL closed round-trips (leveraged brokers
    included -- price-direction win/loss is valid regardless of
    leverage). Every dollar figure (total_pnl, gross_profit/loss,
    profit_factor, expectancy, average_win/loss, max_drawdown) is
    computed ONLY from the "priced" (ALPACA/BINANCE/KRAKEN/LUNO) subset
    -- see module docstring's LEVERAGED-BROKER CAVEAT.
    """
    closed_trades, _ = get_closed_trades_for_user(user_id)
    priced_trades = [t for t in closed_trades if t["priced"]]

    if not closed_trades:
        return {
            "trades_closed": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "priced_trades_closed": 0,
            "total_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": None,
            "expectancy": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "max_drawdown": 0.0,
            "closed_trades": [],
        }

    all_wins = [t for t in closed_trades if t["pnl_percent"] > 0]
    all_losses = [t for t in closed_trades if t["pnl_percent"] <= 0]
    win_rate = (len(all_wins) / len(closed_trades)) * 100

    priced_wins = [t for t in priced_trades if t["pnl"] > 0]
    priced_losses = [t for t in priced_trades if t["pnl"] <= 0]

    gross_profit = sum(t["pnl"] for t in priced_wins)
    gross_loss = abs(sum(t["pnl"] for t in priced_losses))
    total_pnl = gross_profit - gross_loss

    average_win = (gross_profit / len(priced_wins)) if priced_wins else 0.0
    average_loss = (gross_loss / len(priced_losses)) if priced_losses else 0.0
    largest_win = max((t["pnl"] for t in priced_trades), default=0.0)
    largest_loss = min((t["pnl"] for t in priced_trades), default=0.0)

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    expectancy = (total_pnl / len(priced_trades)) if priced_trades else 0.0

    # Max drawdown from the cumulative realized-P&L curve of PRICED
    # closed trades only, in the order they closed -- a leveraged CFD's
    # notional price swing would otherwise distort a real cash drawdown
    # figure the same way it would distort total_pnl.
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for t in priced_trades:
        cumulative += t["pnl"]
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    return {
        "trades_closed": len(closed_trades),
        "wins": len(all_wins),
        "losses": len(all_losses),
        "win_rate": win_rate,
        "priced_trades_closed": len(priced_trades),
        "total_pnl": total_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "average_win": average_win,
        "average_loss": average_loss,
        "largest_win": largest_win,
        "largest_loss": largest_loss,
        "max_drawdown": max_drawdown,
        "closed_trades": closed_trades,
    }


def calculate_pnl_by_asset_class_for_user(user_id):
    """
    Realized $ P&L grouped by asset class, PRICED trades only (see
    module docstring) -- in practice this means US_STOCKS (Alpaca) and
    CRYPTO (Binance) are the only groups that can appear here, since
    FOREX/COMMODITIES only ever trade through the leveraged ETORO/
    MT_BRIDGE brokers today.

    Returns a list of {"asset_class", "trades_closed", "wins",
    "losses", "win_rate", "total_pnl"} dicts, sorted by total_pnl
    descending.
    """
    closed_trades, _ = get_closed_trades_for_user(user_id)
    priced_trades = [t for t in closed_trades if t["priced"]]

    groups = defaultdict(list)
    for t in priced_trades:
        groups[t.get("asset_class") or "Unknown"].append(t)

    result = []
    for asset_class, trades in groups.items():
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        result.append({
            "asset_class": asset_class,
            "trades_closed": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
            "total_pnl": sum(t["pnl"] for t in trades),
        })

    result.sort(key=lambda r: r["total_pnl"], reverse=True)
    return result


def calculate_pnl_by_exit_strategy_for_user(user_id):
    """
    Realized $ P&L grouped by what closed the trade (see
    _label_exit_strategy() above), PRICED trades only -- lets a user
    see whether their profit is mostly coming from stop-loss/take-profit
    exits, the hard time-based exit, or partial profit-taking, instead
    of one blended total. Mirrors the single-owner bot's engines/
    performance_engine.calculate_strategy_breakdown() (post-launch item
    #117), scoped per-user here.

    Returns a list of {"exit_strategy", "trades_closed", "wins",
    "losses", "win_rate", "total_pnl", "average_pnl"} dicts, sorted by
    total_pnl descending.
    """
    closed_trades, _ = get_closed_trades_for_user(user_id)
    priced_trades = [t for t in closed_trades if t["priced"]]

    groups = defaultdict(list)
    for t in priced_trades:
        groups[t["exit_strategy"]].append(t)

    result = []
    for strategy, trades in groups.items():
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in trades)
        result.append({
            "exit_strategy": strategy,
            "trades_closed": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
            "total_pnl": total_pnl,
            "average_pnl": total_pnl / len(trades) if trades else 0.0,
        })

    result.sort(key=lambda r: r["total_pnl"], reverse=True)
    return result


def calculate_monthly_pnl_for_user(user_id):
    """
    Realized $ P&L grouped by calendar month (keyed off exit_time),
    PRICED trades only. Mirrors engines/performance_engine.
    calculate_monthly_returns(), scoped per-user here.

    Orders are stored with an ISO timestamp ("YYYY-MM-DDTHH:MM:SS", see
    saas_order_manager.create_order/save_order), so the first 7
    characters are always a "YYYY-MM" key.

    Returns a list of {"month", "trades_closed", "wins", "losses",
    "win_rate", "total_pnl"} dicts, sorted oldest month first.
    """
    closed_trades, _ = get_closed_trades_for_user(user_id)
    priced_trades = [t for t in closed_trades if t["priced"]]

    months = defaultdict(list)
    for t in priced_trades:
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
