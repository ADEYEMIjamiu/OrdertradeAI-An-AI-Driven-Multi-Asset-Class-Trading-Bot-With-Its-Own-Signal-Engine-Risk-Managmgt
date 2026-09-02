"""
test_mt_cleanup.py -- removes every test_mt_user_debug artifact created
across this project's MT4/5 testing (tasks #230-237): the MetaApi
account itself (fully removed, not just undeployed -- stops it
appearing in the MetaApi dashboard AND stops any further billing on it,
see mt_broker.py's COST MODEL docstring), the saved broker credentials,
the user_settings row seeded for the Phase 2/3 decision-loop smoke
tests, and every saas_orders row this test user's real test trades
created.

Safe to run: test_mt_user_debug was NEVER used for anything except this
project's own MT4/5 testing (see mt_broker.py's module docstring and the
various test_mt_*.py scripts) -- there is no real end-user data under
this id.

Usage:
    python3 test_mt_cleanup.py
"""

import asyncio
import sqlite3

import mt_broker

_TEST_USER_ID = "test_mt_user_debug"
_DB_NAME = "saas_platform.db"  # shared by tenant_engine.py and saas_order_manager.py


async def _remove_metaapi_account():
    try:
        account = await mt_broker._get_or_create_metaapi_account(_TEST_USER_ID)
    except Exception as e:
        print(f"   No MetaApi account to remove (or lookup failed): {e}")
        return
    try:
        await account.remove()
        await account.wait_removed()
        print("   MetaApi account removed (undeployed + fully deleted).")
    except Exception as e:
        print(f"   Could not remove MetaApi account: {e}")


def main():
    print("1. Removing the MetaApi account entirely (stops billing, removes from dashboard)...")
    asyncio.run(_remove_metaapi_account())

    conn = sqlite3.connect(_DB_NAME)

    print("2. Deleting saved MT4/5 credentials...")
    cur = conn.execute("DELETE FROM user_broker_credentials WHERE user_id = ?", (_TEST_USER_ID,))
    print(f"   Deleted {cur.rowcount} row(s).")

    print("3. Deleting user_settings row...")
    cur = conn.execute("DELETE FROM user_settings WHERE user_id = ?", (_TEST_USER_ID,))
    print(f"   Deleted {cur.rowcount} row(s).")

    print("4. Deleting saas_orders test rows...")
    cur = conn.execute("DELETE FROM saas_orders WHERE user_id = ?", (_TEST_USER_ID,))
    print(f"   Deleted {cur.rowcount} row(s).")

    conn.commit()
    conn.close()

    print("\nCleanup complete.")


if __name__ == "__main__":
    main()
