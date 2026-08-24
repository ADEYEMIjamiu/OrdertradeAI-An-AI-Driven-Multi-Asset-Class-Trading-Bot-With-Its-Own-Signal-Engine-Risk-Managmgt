"""
rotation_history.py -- persisted log of rotation candidates.

Added 2026-08-24. find_rotation_candidates() (app.py) has always been
purely live: it recomputes the single weakest-held vs. strongest-not-held
pair per asset class fresh on every dashboard render, and nothing about
that result was ever written anywhere -- not to the logs, not to a file,
not to Telegram. The moment the next 5-minute autorefresh (or a market
condition change) produced a different pair, the previous suggestion was
gone with zero trace: no record of when it appeared, what it suggested,
or whether it was ever acted on.

This gives that a persisted, queryable history, using the exact same
sqlite3-with-CREATE-TABLE-IF-NOT-EXISTS pattern engines/order_manager.py
already uses for trade_journal.db, so the project has one consistent
approach to local persistence rather than introducing a second one.

Two write paths:
  - record_candidate_seen(candidate): called every time
    find_rotation_candidates() returns a candidate, but only inserts a
    new row when the suggested pair actually CHANGED since the last row
    logged for that asset class -- otherwise every 5-minute autorefresh
    while the same unconfirmed suggestion just sits on screen would
    write a near-duplicate row forever.
  - mark_candidate_confirmed(candidate): called when the user clicks
    "Confirm Rotation", updates the most recent matching unconfirmed row
    so the history shows which suggestions were actually acted on.
"""
from datetime import datetime
import sqlite3

DB_NAME = "trade_journal.db"


def _get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rotation_candidate_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT,
            asset_class TEXT,
            weak_ticker TEXT,
            weak_broker TEXT,
            weak_score REAL,
            hours_held REAL,
            candidate_ticker TEXT,
            candidate_score REAL,
            gap REAL,
            confirmed INTEGER DEFAULT 0,
            confirmed_at TEXT
        )
    """)
    return conn


def _most_recent_row(conn, asset_class):
    cur = conn.execute(
        """
        SELECT weak_ticker, candidate_ticker FROM rotation_candidate_history
        WHERE asset_class = ?
        ORDER BY id DESC LIMIT 1
        """,
        (asset_class,),
    )
    return cur.fetchone()


def record_candidate_seen(candidate):
    """
    Insert a new history row for `candidate` (the same dict
    find_rotation_candidates() builds in app.py), unless the identical
    weak_ticker/candidate_ticker pair is already the most recent row
    logged for this asset class -- so an unconfirmed suggestion sitting
    on screen across many autorefresh cycles logs once, not every cycle.
    """
    try:
        conn = _get_connection()
        last = _most_recent_row(conn, candidate["asset_class"])
        if last is not None and last[0] == candidate["weak_ticker"] and last[1] == candidate["candidate_ticker"]:
            conn.close()
            return

        conn.execute(
            """
            INSERT INTO rotation_candidate_history (
                recorded_at, asset_class, weak_ticker, weak_broker,
                weak_score, hours_held, candidate_ticker, candidate_score,
                gap, confirmed, confirmed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                candidate["asset_class"],
                candidate["weak_ticker"],
                candidate["weak_broker"],
                candidate["weak_score"],
                candidate["hours_held"],
                candidate["candidate_ticker"],
                candidate["candidate_score"],
                candidate["gap"],
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[rotation_history] could not record candidate: {e}")


def mark_candidate_confirmed(candidate):
    """
    Marks the most recent unconfirmed row matching this candidate's
    asset_class/weak_ticker/candidate_ticker as confirmed. Called right
    when the user clicks "Confirm Rotation" in app.py, regardless of
    whether execute_rotation() itself ends up fully succeeding -- this
    records that the swap was approved and attempted, which is the
    useful signal for the history view.
    """
    try:
        conn = _get_connection()
        conn.execute(
            """
            UPDATE rotation_candidate_history
            SET confirmed = 1, confirmed_at = ?
            WHERE id = (
                SELECT id FROM rotation_candidate_history
                WHERE asset_class = ? AND weak_ticker = ? AND candidate_ticker = ?
                  AND confirmed = 0
                ORDER BY id DESC LIMIT 1
            )
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                candidate["asset_class"],
                candidate["weak_ticker"],
                candidate["candidate_ticker"],
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[rotation_history] could not mark candidate confirmed: {e}")


def get_recent_history(limit=30):
    """
    Returns the most recent `limit` rows, newest first, as a list of
    dicts -- ready for st.dataframe() in app.py.
    """
    try:
        conn = _get_connection()
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT recorded_at, asset_class, weak_ticker, weak_score,
                   hours_held, candidate_ticker, candidate_score, gap,
                   confirmed, confirmed_at
            FROM rotation_candidate_history
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"[rotation_history] could not load history: {e}")
        return []
