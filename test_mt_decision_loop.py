"""
test_mt_decision_loop.py -- smoke test for Phase 2 (task #233): runs the
REAL saas_decision_engine.run_decision_loop_for_user() dry_run=True pass
for the test MT4/5 user, with FOREX/COMMODITIES enabled, to catch any
crash in the new MT_BRIDGE per-user broker resolution / balance /
exposure / lot-sizing wiring BEFORE this touches the live systemd
service real users' Preview button hits. dry_run=True never places an
order (see run_decision_loop_for_user()'s own docstring) -- this only
proves the code path runs end-to-end without raising, and prints
whatever it decided so the FOREX/COMMODITIES rows can be sanity-checked
by eye (broker should read MT_BRIDGE, sizing should look reasonable).

FIX 2026-09-02 (found via this script's own first run: it printed "Got
0 result rows" with no error -- a false "all clear"): test_mt_user_debug
was created earlier this project purely by writing rows directly into
mt_credentials (via save_mt_credentials()) for the MT4/5 connectivity
tests -- it never went through tenant.create_user(), so it had NO row
in user_settings at all. tenant.save_user_settings() silently returns
False when get_user_settings() is None (see that function's own "if
existing is None: return False" -- correct behavior for a real caller,
since a real user always has a settings row from signup). That silent
no-op meant enabled_asset_classes was never actually persisted,
run_decision_loop_for_user() read back an empty settings dict, both
FOREX and COMMODITIES hit the asset-class-not-enabled `continue` before
appending anything, and the loop legitimately returned zero rows -- not
a crash in the new MT_BRIDGE wiring, just nothing for it to evaluate.
_ensure_test_user_settings_row() below inserts a minimal user_settings
row directly (bypassing create_user(), which would mint a fresh random
user_id rather than reusing this fixed test id) so save_user_settings()
has an existing row to update. user_settings.user_id has a FOREIGN KEY
to users(user_id) in the schema, but tenant_engine.py's own connections
never run "PRAGMA foreign_keys = ON", so SQLite does not enforce it --
same reason the earlier MT4/5 connectivity tests already worked fine
writing to mt_credentials for this same user_id with no users row
either.

Usage:
    python3 test_mt_decision_loop.py
"""

import json
from datetime import datetime, timezone

from engines import tenant_engine as tenant
from engines import saas_decision_engine

_TEST_USER_ID = "test_mt_user_debug"


def _ensure_test_user_settings_row():
    """Idempotent (INSERT OR IGNORE) -- safe to rerun, never touches a
    row that already exists for this id."""
    conn = tenant._get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO user_settings "
            "(user_id, max_position_size, enabled_asset_classes, "
            "allow_live_trading, created_at, updated_at) "
            "VALUES (?, 0.20, '[]', 0, ?, ?)",
            (_TEST_USER_ID, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def main():
    print(f"0. Ensuring a user_settings row exists for user_id={_TEST_USER_ID!r} "
          f"(this test user was created via save_mt_credentials(), not the "
          f"normal signup flow, so it never got one automatically)...")
    _ensure_test_user_settings_row()

    print(f"1. Enabling FOREX/COMMODITIES for user_id={_TEST_USER_ID!r} "
          f"(does not touch any other setting)...")
    ok = tenant.save_user_settings(
        _TEST_USER_ID,
        enabled_asset_classes=["FOREX", "COMMODITIES"],
        max_position_size=0.1,
    )
    if not ok:
        print("   WARNING: save_user_settings() returned False -- settings "
              "were NOT updated, results below will likely be empty again.")

    print("2. Running the real per-user decision loop, dry_run=True "
          "(never places an order)...\n")
    results = saas_decision_engine.run_decision_loop_for_user(_TEST_USER_ID, dry_run=True)

    print(f"3. Got {len(results)} result rows. Full output:")
    print(json.dumps(results, indent=2, default=str))

    would_buy = [r for r in results if r.get("action") == "would_buy"]
    errors = [r for r in results if r.get("action") == "error"]
    print(f"\nSummary: {len(would_buy)} would_buy, {len(errors)} error rows.")
    if errors:
        print("ERROR rows found -- these need investigating before this "
              "is trusted for real users:")
        for e in errors:
            print(f"  - {e.get('ticker')}: {e.get('message')}")


if __name__ == "__main__":
    main()
