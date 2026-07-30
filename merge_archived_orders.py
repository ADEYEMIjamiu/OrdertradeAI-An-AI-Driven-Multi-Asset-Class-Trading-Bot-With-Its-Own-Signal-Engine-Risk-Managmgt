"""
One-off utility: restores Order Book history after archive_trade_journal.py.

archive_trade_journal.py renamed the whole trade_journal.db file to start a
clean slate for Win Rate / Profit Factor / the performance digest -- but it
turns out the "orders" table (which feeds the Order Book) lives in that same
file as the "trades" table (which feeds performance stats). Archiving the
file wiped the Order Book's history too, not just the trades used for
performance stats.

This script copies the "orders" rows out of the most recent
trade_journal_archive_*.db file and merges them into the current
trade_journal.db's "orders" table. It does NOT touch the "trades" table, so
Win Rate / Profit Factor / the digest stay exactly as freshly reset --
this only restores the Order Book's audit trail.

Safe to run more than once: uses INSERT OR IGNORE on order_id (primary key),
so already-merged or already-live orders are never duplicated or overwritten.

Run from the project root:
    python3 merge_archived_orders.py
"""

import glob
import os
import sqlite3

CURRENT_DB = "trade_journal.db"

archives = sorted(glob.glob("trade_journal_archive_*.db"))

if not archives:
    print("No trade_journal_archive_*.db file found -- nothing to merge.")
    raise SystemExit(0)

if not os.path.exists(CURRENT_DB):
    print(f"No {CURRENT_DB} found in this directory -- run from the project root.")
    raise SystemExit(1)

archive_db = archives[-1]
print(f"Using archive: {archive_db}")

conn = sqlite3.connect(CURRENT_DB)

# Make sure the orders table exists in the current db (order_manager.py
# creates it lazily on first save_order() call, which has already happened
# here since 2 SOL-USD orders exist -- but this is a harmless no-op if so).
conn.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        created_at TEXT,
        ticker TEXT,
        side TEXT,
        quantity REAL,
        trade_amount REAL,
        price REAL,
        asset_class TEXT,
        broker TEXT,
        strategy TEXT,
        confidence REAL,
        ai_trade_score REAL,
        priority TEXT,
        stop_loss REAL,
        take_profit REAL,
        status TEXT,
        broker_order_id TEXT,
        filled_price REAL,
        filled_quantity REAL,
        error TEXT,
        updated_at TEXT
    )
""")

before_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

conn.execute("ATTACH DATABASE ? AS archive", (archive_db,))

# Only attempt the merge if the archive actually has an orders table
# (older archives, if any existed, might not -- defensive check).
has_orders_table = conn.execute(
    "SELECT name FROM archive.sqlite_master WHERE type='table' AND name='orders'"
).fetchone()

if not has_orders_table:
    print(f"{archive_db} has no 'orders' table -- nothing to merge from it.")
else:
    archive_count = conn.execute("SELECT COUNT(*) FROM archive.orders").fetchone()[0]
    conn.execute("""
        INSERT OR IGNORE INTO main.orders
        SELECT * FROM archive.orders
    """)
    conn.commit()
    after_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    print(f"Archive had {archive_count} orders. Live orders table: "
          f"{before_count} -> {after_count} (new rows merged: {after_count - before_count}).")

conn.execute("DETACH DATABASE archive")
conn.close()

print("Done. The 'trades' table (Win Rate / Profit Factor / digest) was not touched.")
