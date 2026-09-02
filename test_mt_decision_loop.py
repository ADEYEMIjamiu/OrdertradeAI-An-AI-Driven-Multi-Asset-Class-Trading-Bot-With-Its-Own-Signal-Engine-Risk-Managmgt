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

Usage:
    python3 test_mt_decision_loop.py
"""

import json

from engines import tenant_engine as tenant
from engines import saas_decision_engine

_TEST_USER_ID = "test_mt_user_debug"


def main():
    print(f"1. Enabling FOREX/COMMODITIES for user_id={_TEST_USER_ID!r} "
          f"(does not touch any other setting)...")
    tenant.save_user_settings(
        _TEST_USER_ID,
        enabled_asset_classes=["FOREX", "COMMODITIES"],
        max_position_size=0.1,
    )

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
