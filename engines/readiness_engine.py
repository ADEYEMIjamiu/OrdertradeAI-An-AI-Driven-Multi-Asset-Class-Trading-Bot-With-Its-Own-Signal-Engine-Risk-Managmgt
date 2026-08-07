"""
Real-Money Readiness Scorecard -- built 2026-08-07 at explicit user
request, to answer "is this bot actually ready to trade real money"
with a data-driven checklist instead of a gut feeling.

User-confirmed thresholds (via AskUserQuestion):
  - At least READINESS_MIN_DAYS (30) days of live paper/demo/testnet
    trading since tracking started.
  - At least READINESS_MIN_TRADES (20) closed trades per asset class
    before that class's win rate / profit factor are treated as
    meaningful rather than noise from a small sample.
  - Profit factor (gross profit / gross loss) >= READINESS_MIN_PROFIT_FACTOR
    (1.3) -- a real, not just breakeven, edge.
  - Max drawdown (see engines/equity_tracker.py) <= READINESS_MAX_DRAWDOWN_PERCENT
    (20%, a reasonable starting default -- adjust in config.py if a
    different comfort level is wanted).

Deliberately read-only / advisory: this never flips LIVE_TRADING,
ETORO_LIVE_TRADING, or any broker from paper/demo/testnet to real money
by itself. Moving to real capital always requires the account-level
switch to be made deliberately by the user, same as
REQUIRE_ALPACA_PAPER_ENVIRONMENT / REQUIRE_ETORO_DEMO_ENVIRONMENT already
require an explicit decision to turn off.
"""

from datetime import datetime

from config import (
    READINESS_VALIDATION_START,
    READINESS_MIN_DAYS,
    READINESS_MIN_TRADES,
    READINESS_MIN_PROFIT_FACTOR,
    READINESS_MAX_DRAWDOWN_PERCENT,
)
from engines.digest_engine import calculate_performance_digest
from engines import equity_tracker


def calculate_readiness_scorecard():
    days_elapsed = max((datetime.now().date() - READINESS_VALIDATION_START.date()).days, 0)
    # calculate_performance_digest() filters by a rolling "last N days"
    # window measured from now, so passing the exact elapsed day count
    # gives "everything since tracking started." Floored at 1 so day
    # zero doesn't pass a 0-day window (which would exclude everything
    # closed earlier today).
    digest = calculate_performance_digest(period_days=max(days_elapsed, 1))

    try:
        max_drawdown = equity_tracker.get_max_drawdown_percent(
            since=READINESS_VALIDATION_START
        )
    except Exception:
        max_drawdown = None

    time_ready = days_elapsed >= READINESS_MIN_DAYS
    drawdown_ok = max_drawdown is None or max_drawdown <= READINESS_MAX_DRAWDOWN_PERCENT

    combined = {"OVERALL": digest["overall"], **digest["by_asset_class"]}

    rows = {}
    for asset_class, stats in combined.items():
        sample_ok = stats["trades_closed"] >= READINESS_MIN_TRADES
        profit_factor = stats["profit_factor"]
        profit_factor_ok = (
            sample_ok
            and profit_factor is not None
            and profit_factor >= READINESS_MIN_PROFIT_FACTOR
        )
        # Drawdown is tracked on total portfolio equity, not broken out
        # per asset class, so it only gates the OVERALL row.
        ready = (
            time_ready
            and sample_ok
            and profit_factor_ok
            and (asset_class != "OVERALL" or drawdown_ok)
        )
        rows[asset_class] = {**stats, "sample_ok": sample_ok, "profit_factor_ok": profit_factor_ok, "ready": ready}

    return {
        "days_elapsed": days_elapsed,
        "days_required": READINESS_MIN_DAYS,
        "time_ready": time_ready,
        "min_trades_required": READINESS_MIN_TRADES,
        "min_profit_factor": READINESS_MIN_PROFIT_FACTOR,
        "max_drawdown_percent": max_drawdown,
        "max_drawdown_limit": READINESS_MAX_DRAWDOWN_PERCENT,
        "drawdown_ok": drawdown_ok,
        "rows": rows,
    }
