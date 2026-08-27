"""
Per-user eToro trailing-stop ratchet for the multi-tenant SaaS product.

Mirrors app.py's apply_etoro_trailing_lock() (the single-owner bot) --
see that function's own docstring for the full investigation showing
eToro's own broker-side "trailing" stop does NOT actually work as
documented: confirmed live 2026-08-24, a position that rallied +4.17%
over two sustained days had isTslEnabled=True the whole time (both the
raw eToro API and this project's own logs confirmed the trailing flag
was successfully set) yet its stopLossRate never moved once. The
single-owner bot stopped trusting that black-box flag and took over the
ratcheting itself; SaaS eToro positions had the same exposure until now
-- saas_broker_factory.py's buy_etoro_for_user() still best-effort-
requests eToro's own broker-side trailing at trade-open (harmless,
matches the single-owner bot's belt-and-suspenders approach), but
nothing was actually re-pushing a tighter FIXED stop-loss as price made
new highs.

Peak-price state is persisted in saas_platform.db (NOT a shared JSON
file, unlike the single-owner bot's _load_etoro_highest_price_state()) --
a flat file would be the wrong choice here: this runs once per user,
potentially with overlapping saas-scheduler ticks across different
users or a slow tick still running when the next one starts, and a
single shared file has no real concurrency guarantee. SQLite's own
locking handles concurrent access safely, and it's the same database
every other piece of durable SaaS state (saas_orders, users, settings)
already lives in.

Not yet wired into saas_decision_engine.py's per-tick loop -- see
saas_scheduler.py / saas_decision_engine.py for where exit protection
(saas_exit_engine.py) is already called each tick; this module is built
and tested standalone first, wiring it in is a separate, deliberate step
once this is verified correct on its own.
"""

import sqlite3

from engines import saas_broker_factory as factory
from etoro_broker import ETORO_STOP_LOSS_PCT, ETORO_TRAILING_STEP_PCT

DB_NAME = "saas_platform.db"


def _get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS saas_etoro_trailing_state (
            user_id TEXT NOT NULL,
            position_id TEXT NOT NULL,
            peak_price REAL NOT NULL,
            updated_at TEXT,
            PRIMARY KEY (user_id, position_id)
        )
    """)
    return conn


def _get_peak(user_id, position_id):
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT peak_price FROM saas_etoro_trailing_state WHERE user_id = ? AND position_id = ?",
            (user_id, str(position_id)),
        ).fetchone()
        return float(row[0]) if row else None
    finally:
        conn.close()


def _set_peak(user_id, position_id, peak_price):
    conn = _get_connection()
    try:
        conn.execute("""
            INSERT INTO saas_etoro_trailing_state (user_id, position_id, peak_price, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(user_id, position_id) DO UPDATE SET
                peak_price = excluded.peak_price,
                updated_at = excluded.updated_at
        """, (user_id, str(position_id), peak_price))
        conn.commit()
    finally:
        conn.close()


def _clear_stale_peaks(user_id, held_position_ids):
    """
    Drop peak-state rows for positions no longer open, same reasoning as
    the single-owner bot's stale-entry cleanup in apply_etoro_trailing_
    lock() -- otherwise a closed position's old peak could sit around
    forever and, worse, wrongly seed the ratchet if that same
    position_id number were ever reused by eToro for a new position.
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT position_id FROM saas_etoro_trailing_state WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        for (position_id,) in rows:
            if position_id not in held_position_ids:
                conn.execute(
                    "DELETE FROM saas_etoro_trailing_state WHERE user_id = ? AND position_id = ?",
                    (user_id, position_id),
                )
        conn.commit()
    finally:
        conn.close()


def apply_etoro_trailing_lock_for_user(user_id):
    """
    Per-user equivalent of app.py's apply_etoro_trailing_lock() -- see
    module docstring for the full reasoning. For every open LONG eToro
    position this user holds, tracks the highest price seen so far and,
    whenever that peak has risen by at least ETORO_TRAILING_STEP_PCT
    since the last pushed stop, PATCHes an updated FIXED stop-loss to
    (peak * (1 - ETORO_STOP_LOSS_PCT)) -- the same distance-from-peak
    logic eToro's own (unreliable) trailing docs describe, just enforced
    here instead of trusted to their black box. Never moves the stop
    down, never touches take-profit. SHORT positions are skipped (not
    currently supported, same as the single-owner bot -- the ratchet
    direction inverts for shorts and would need its own logic).

    Returns a list of result dicts ({"ticker", "action", "message"}) for
    logging/dashboard use. Never raises -- each position gets its own
    try/except so a problem on one ticker can't block protection for any
    other open eToro position this user holds.
    """
    try:
        positions = factory.get_user_etoro_positions(user_id)
    except Exception as e:
        return [{
            "ticker": None,
            "action": "error",
            "message": f"eToro trailing-lock check failed to fetch positions: {e}",
        }]

    held_position_ids = {
        str(p["position_id"]) for p in positions if p.get("position_id") is not None
    }
    _clear_stale_peaks(user_id, held_position_ids)

    results = []
    for position in positions:
        if position.get("direction") != "LONG":
            continue  # see docstring -- shorts not supported here yet

        position_id = position.get("position_id")
        ticker = position.get("symbol")
        if position_id is None or ticker is None:
            continue

        try:
            current_price = factory.get_etoro_current_price_for_user(user_id, ticker)
            open_price = float(position["open_price"])
            current_stop = position.get("stop_loss_rate")
            take_profit = position.get("take_profit_rate")

            previous_peak = _get_peak(user_id, position_id)
            if previous_peak is None:
                previous_peak = open_price
            peak_price = max(previous_peak, current_price)
            _set_peak(user_id, position_id, peak_price)

            if peak_price <= open_price:
                continue  # never below water yet -- nothing to ratchet

            candidate_stop = round(peak_price * (1 - ETORO_STOP_LOSS_PCT), 5)

            # Only push an update if it's a real, meaningful improvement
            # over what's already set -- both a minimum step size (avoid
            # spamming PATCH on every cent of noise) and a hard guarantee
            # this never moves the stop down.
            if current_stop is not None:
                min_step = current_stop * ETORO_TRAILING_STEP_PCT
                if candidate_stop < current_stop + min_step:
                    continue

            factory.set_etoro_fixed_stop_loss_for_user(
                user_id, position_id, candidate_stop, take_profit_rate=take_profit
            )
            results.append({
                "ticker": ticker,
                "action": "trailing_lock_updated",
                "message": f"{ticker}: trailing lock moved stop-loss to {candidate_stop} "
                           f"(peak price {round(peak_price, 5)}).",
            })
        except Exception as e:
            results.append({
                "ticker": ticker,
                "action": "error",
                "message": f"eToro trailing-lock update failed for {ticker or position_id}: {e}",
            })

    return results
