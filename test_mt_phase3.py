"""
test_mt_phase3.py -- live smoke test for Phase 3 (task #237): MT4/5 exit
protection + reconciliation.

Two REAL demo trades are placed (small, $50 each, same as test_mt_buy.py)
to exercise both new code paths end to end against the real connected
Pepperstone demo account -- no real money, but real broker actions:

PART A -- reconcile_user_mt_orders() (engines/saas_reconcile_engine.py):
  1. Places a real BUY, journals it into saas_orders exactly like
     saas_decision_engine.py's MT_BRIDGE branch would.
  2. Closes it directly via mt_broker.execute_sell_close_sync() --
     OUTSIDE the journal, simulating what a broker-side stop-loss/take-
     profit hit (or the user closing it by hand in their MT4/5 terminal)
     looks like: the position vanishes from MetaApi with zero
     involvement from this codebase.
  3. Runs reconcile_user_mt_orders() and confirms it notices the journal
     still says "open" while MetaApi says otherwise, and closes the
     journal out.

PART B -- check_and_apply_exits_for_user() hard time-based exit
(engines/saas_exit_engine.py):
  1. Places a second real BUY, journals it the same way.
  2. Backdates its created_at past MAX_HOLD_DAYS_HARD directly in
     saas_platform.db (this project's own established pattern for
     seeding test state the normal code path has no reason to expose --
     see test_mt_decision_loop.py's _ensure_test_user_settings_row()).
  3. Runs check_and_apply_exits_for_user(dry_run=False) and confirms it
     detects the stale hold time (MT_BRIDGE skips the price-based stop-
     loss/take-profit check entirely -- see that file's docstring for
     why -- so this is the only trigger available) and REALLY closes the
     position via factory.sell_mt_for_user().

PART C -- best-effort cleanup of the untracked EURUSD position left open
by test_mt_buy.py (2026-09-02) -- that script called mt_broker directly,
bypassing the journal entirely, so it's an orphan on MetaApi's side that
nothing in this project would otherwise ever close.

Usage:
    python3 test_mt_phase3.py
"""

import json
import sqlite3
from datetime import datetime, timedelta

import mt_broker
from engines import saas_broker_factory as factory
from engines import saas_exit_engine as exit_engine
from engines import saas_order_manager as journal
from engines import saas_reconcile_engine as reconcile

_TEST_USER_ID = "test_mt_user_debug"
_LEFTOVER_POSITION_ID = "87132286"  # from test_mt_buy.py's 2026-09-02 EURUSD trade


def _journal_mt_buy(ticker, usd_amount):
    """Places a real BUY via mt_broker (same call saas_decision_engine.py's
    MT_BRIDGE branch makes) and journals it exactly the way that branch
    does post-2026-09-02-fix (filled_quantity = the real lot size, not
    trade_amount/price). Returns the saved order dict."""
    mt_result = mt_broker.execute_buy_by_usd_amount_sync(_TEST_USER_ID, ticker, usd_amount)
    if mt_result is None or mt_result.get("position_id") is None:
        raise RuntimeError(f"BUY did not confirm filled -- raw: {mt_result}")

    order = journal.create_order(
        user_id=_TEST_USER_ID,
        ticker=ticker,
        side="BUY",
        quantity=mt_result["lot_size"],
        trade_amount=usd_amount,
        price=mt_result["executed_price"] or 0,
        asset_class="FOREX",
        broker="MT_BRIDGE",
        strategy="TEST_PHASE3",
        confidence=0,
        ai_trade_score=0,
        priority=None,
    )
    order = journal.mark_order_submitted(order, broker_order_id=str(mt_result["position_id"]))
    order = journal.mark_order_filled(
        order, filled_price=mt_result["executed_price"] or 0, filled_quantity=mt_result["lot_size"]
    )
    journal.save_order(order)
    print(f"   Journaled BUY: order_id={order['order_id']} position_id={mt_result['position_id']} "
          f"lot_size={mt_result['lot_size']}")
    return order


def _backdate_order(order_id, days_ago):
    conn = sqlite3.connect("saas_platform.db")
    backdated = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
    conn.execute("UPDATE saas_orders SET created_at = ? WHERE order_id = ?", (backdated, order_id))
    conn.commit()
    conn.close()
    print(f"   Backdated order {order_id} created_at to {backdated} ({days_ago} days ago)")


def part_a():
    print("\n=== PART A: reconcile_user_mt_orders() ===")
    print("1. Placing a real $50 BUY (EURUSD=X)...")
    order = _journal_mt_buy("EURUSD=X", 50)
    position_id = order["broker_order_id"]

    print(f"2. Journal says open? {journal.has_open_position_for_user(_TEST_USER_ID, 'EURUSD=X', 'MT_BRIDGE')}")

    print(f"3. Closing position {position_id} DIRECTLY via mt_broker (bypassing the "
          f"journal -- simulates a broker-side stop-loss/take-profit hit)...")
    mt_broker.execute_sell_close_sync(_TEST_USER_ID, position_id)

    print("4. Running reconcile_user_mt_orders()...")
    results = reconcile.reconcile_user_mt_orders(_TEST_USER_ID)
    print(json.dumps(results, indent=2, default=str))

    still_open = journal.has_open_position_for_user(_TEST_USER_ID, "EURUSD=X", "MT_BRIDGE")
    print(f"5. Journal says open now? {still_open} (expected: False)")
    if still_open:
        print("   *** FAIL: reconciliation did not close the journal out. ***")
    elif not results:
        print("   *** FAIL: reconciliation ran but reported nothing changed. ***")
    else:
        print("   PASS.")


def part_b():
    print("\n=== PART B: check_and_apply_exits_for_user() hard time-exit ===")
    print("1. Placing a real $50 BUY (GBPUSD=X)...")
    order = _journal_mt_buy("GBPUSD=X", 50)

    print("2. Backdating this order's created_at to 8 days ago (MAX_HOLD_DAYS_HARD=7)...")
    _backdate_order(order["order_id"], days_ago=8)

    print(f"3. Journal says open? {journal.has_open_position_for_user(_TEST_USER_ID, 'GBPUSD=X', 'MT_BRIDGE')}")

    print("4. Running check_and_apply_exits_for_user(dry_run=False)...")
    results = exit_engine.check_and_apply_exits_for_user(_TEST_USER_ID, dry_run=False)
    gbp_results = [r for r in results if r.get("ticker") == "GBPUSD=X"]
    print(json.dumps(gbp_results, indent=2, default=str))

    still_open = journal.has_open_position_for_user(_TEST_USER_ID, "GBPUSD=X", "MT_BRIDGE")
    print(f"5. Journal says open now? {still_open} (expected: False)")
    if still_open:
        print("   *** FAIL: hard time-exit did not close the position. ***")
    elif not gbp_results or gbp_results[0].get("action") != "sold":
        print(f"   *** FAIL: expected action 'sold' for GBPUSD=X, got: {gbp_results} ***")
    else:
        print("   PASS.")


def part_c():
    print("\n=== PART C: best-effort cleanup of the leftover untracked EURUSD position ===")
    print(f"Closing position {_LEFTOVER_POSITION_ID} (from test_mt_buy.py, 2026-09-02) if still open...")
    try:
        mt_broker.execute_sell_close_sync(_TEST_USER_ID, _LEFTOVER_POSITION_ID)
        print("   Closed.")
    except Exception as e:
        print(f"   Could not close (may already be closed, or ID stale): {e}")


if __name__ == "__main__":
    part_a()
    part_b()
    part_c()
