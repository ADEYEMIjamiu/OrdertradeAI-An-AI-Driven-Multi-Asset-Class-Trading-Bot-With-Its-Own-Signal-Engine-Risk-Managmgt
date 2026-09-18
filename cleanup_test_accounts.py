"""
One-off cleanup script (2026-09-18): wipes the pre-launch test/seed
accounts out of saas_platform.db so the admin panel starts from a
clean slate, keeping only:
  - speeditdg@gmail.com          (admin / owner account)
  - jacobsonjnr22@gmail.com      (first real signup, explicitly kept)
  - speeditdg+test1@gmail.com    (explicitly kept test alias)

Everything else visible in the admin Users table at the time of this
request gets removed, by explicit allowlist below (NOT "delete
everyone not in a keep-list" -- an explicit delete-list means a new
signup that lands between now and whenever this actually runs is
never accidentally caught by it):
  - referral-test-20260915@ordertradeai.com
  - verify-trial-test-20260915@ordertradeai.com
  - carsten.achtelik@gmail.com
  - jamiuadegbenro003@gmail.com
  - quidquick2026@gmail.com   (billing_status was "past_due" -- the
    user confirmed deleting the app-side record anyway; this does NOT
    touch Stripe, so if that subscription is still attempting to
    charge, it needs to be handled separately in the Stripe dashboard)
  - ibrahimdeji@gmail.com
  - speeditdg+billingtest@gmail.com
  - speeditdg+billingtest2@gmail.com

Deletes across every user_id-keyed table in saas_platform.db (none of
them use ON DELETE CASCADE, so each has to be cleared explicitly,
children before the users row itself):
  password_reset_tokens, email_verification_tokens, email_change_tokens,
  login_sessions, execution_locks, live_trading_audit_log,
  user_broker_credentials, user_settings, saas_orders,
  saas_position_lifecycle_state, saas_etoro_trailing_state, users.

Run on the droplet, from /opt/ordertrade-ai (same directory
saas_platform.db lives in):

    python3 cleanup_test_accounts.py            # dry run (default) --
                                                  shows exactly what
                                                  would be deleted,
                                                  changes nothing
    python3 cleanup_test_accounts.py --execute   # actually deletes

ALWAYS take a fresh backup first (belt-and-suspenders on top of the
existing daily cron backup in backup_saas_state.sh):
    cp saas_platform.db backups/saas_platform.db.pre_cleanup_$(date +%Y%m%d_%H%M%S).bak
"""

import sqlite3
import sys

DB_NAME = "saas_platform.db"

DELETE_EMAILS = [
    "referral-test-20260915@ordertradeai.com",
    "verify-trial-test-20260915@ordertradeai.com",
    "carsten.achtelik@gmail.com",
    "jamiuadegbenro003@gmail.com",
    "quidquick2026@gmail.com",
    "ibrahimdeji@gmail.com",
    "speeditdg+billingtest@gmail.com",
    "speeditdg+billingtest2@gmail.com",
]

# Children first, users last. Every table here is keyed by user_id
# per engines/tenant_engine.py, engines/saas_order_manager.py,
# engines/saas_position_lifecycle_engine.py, and
# engines/saas_etoro_trailing_engine.py (the only files that write to
# saas_platform.db).
CHILD_TABLES = [
    "password_reset_tokens",
    "email_verification_tokens",
    "email_change_tokens",
    "login_sessions",
    "execution_locks",
    "live_trading_audit_log",
    "user_broker_credentials",
    "user_settings",
    "saas_orders",
    "saas_position_lifecycle_state",
    "saas_etoro_trailing_state",
]


def main():
    execute = "--execute" in sys.argv

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = []
    for email in DELETE_EMAILS:
        cur.execute(
            "SELECT user_id, email, billing_status, stripe_customer_id, "
            "stripe_subscription_id FROM users WHERE email = ?",
            (email,),
        )
        row = cur.fetchone()
        if row is None:
            print(f"  [not found, skipping] {email}")
            continue
        rows.append(row)

    if not rows:
        print("Nothing to do -- none of the target emails were found in users.")
        conn.close()
        return

    print(f"{'DRY RUN -- ' if not execute else ''}Accounts to delete ({len(rows)}):")
    for r in rows:
        billing_note = ""
        if r["billing_status"] and r["billing_status"] not in ("none", "trialing"):
            billing_note = (
                f"  <-- billing_status={r['billing_status']!r}, "
                f"stripe_customer_id={r['stripe_customer_id']!r} "
                f"(app-side record only; Stripe itself is untouched)"
            )
        print(f"  - {r['email']} (user_id={r['user_id']}){billing_note}")

    if not execute:
        print(
            "\nDry run only -- nothing was deleted. Re-run with --execute "
            "to actually delete these accounts and all their associated data."
        )
        conn.close()
        return

    print("\nDeleting...")
    for r in rows:
        uid = r["user_id"]
        for table in CHILD_TABLES:
            cur.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
        cur.execute("DELETE FROM users WHERE user_id = ?", (uid,))
        print(f"  - deleted {r['email']}")

    conn.commit()
    conn.close()
    print(f"\nDone. {len(rows)} account(s) removed.")


if __name__ == "__main__":
    main()
