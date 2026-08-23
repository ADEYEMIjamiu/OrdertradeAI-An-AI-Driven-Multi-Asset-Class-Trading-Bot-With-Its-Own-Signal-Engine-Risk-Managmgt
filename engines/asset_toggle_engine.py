"""
Asset Class Toggle Engine -- lets the user turn AI trade ENTRY on/off per
asset class (US_STOCKS, CRYPTO, FOREX, COMMODITIES) from the dashboard,
e.g. "only trade crypto today" or "no forex this week."

Built 2026-08-22 per explicit user request: a real money-making machine
should let the operator choose which markets it's allowed to enter on a
given day, not just run everything all the time regardless of what's
actually wanted.

Safety design:
- Only gates NEW BUY/entry signals. SELL exits, stop-loss, take-profit,
  and trailing-stop closes are NEVER gated by this -- blocking an exit
  because a toggle happens to be off would be actively dangerous (an
  open position could no longer be protected). This mirrors the existing
  broker_execution_gate() pattern in broker_sync_engine.py, which applies
  the exact same BUY-only restriction for broker-health reasons.
- Persisted in a small SQLite file (asset_toggles.db), matching the
  project's existing pattern (trade_journal.db, equity_curve.db,
  local_account.db) -- state must survive a systemd restart, otherwise a
  restart would silently re-enable everything the user had turned off.
- Defaults to ENABLED for every asset class when no row exists yet, so
  deploying this for the first time changes nothing about current live
  behavior until the user actually flips a toggle off.
"""

import sqlite3

_DB_NAME = "asset_toggles.db"

ASSET_CLASSES = ["US_STOCKS", "CRYPTO", "FOREX", "COMMODITIES"]


def _get_connection():
    conn = sqlite3.connect(_DB_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS asset_toggles (
            asset_class TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL
        )
    """)
    return conn


def is_asset_class_enabled(asset_class):
    """
    True (new entries allowed) unless the user has explicitly turned this
    asset class off. Never raises -- a read failure defaults to enabled
    so a broken toggle store can't silently block all trading.
    """
    asset_class = str(asset_class).upper().strip()
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT enabled FROM asset_toggles WHERE asset_class = ?",
            (asset_class,),
        ).fetchone()
        conn.close()
    except Exception:
        return True

    if row is None:
        return True  # no row yet -- default ON, matches pre-toggle behavior

    return bool(row[0])


def set_asset_class_enabled(asset_class, enabled):
    """Never raises -- a failed write is logged, not propagated to the UI."""
    asset_class = str(asset_class).upper().strip()
    try:
        conn = _get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO asset_toggles (asset_class, enabled) VALUES (?, ?)",
            (asset_class, 1 if enabled else 0),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[asset_toggle_engine] could not save toggle for {asset_class}: {e}")


def get_all_toggles():
    """{asset_class: bool} for all four classes, defaulting True if unset."""
    return {ac: is_asset_class_enabled(ac) for ac in ASSET_CLASSES}
