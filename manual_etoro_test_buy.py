"""
One-off manual test: places a REAL eToro Demo order via the SaaS
per-user execution path (engines/saas_broker_factory.py's
buy_etoro_for_user()), bypassing the AI signal gate entirely.

Why this exists: the AI signal engine's current FOREX/COMMODITIES
universe (6 symbols) has no fresh candidate right now -- the two that
score well (USDJPY=X, CL=F) are already "open" per the SaaS journal
(no eToro exit automation exists yet to close them), and the other
four are rejected on weak risk/reward or are SELL signals. Waiting for
a natural signal could take an unknown amount of time. This script
directly exercises the exact same order-placement code path
(buy_etoro_for_user -> instrument catalog lookup -> order POST -> fill
poll) that the real AI-driven flow uses, just without the AI gate, to
confirm end-to-end real execution works today.

Run this ON THE DROPLET (needs the real .env, SAAS_ENCRYPTION_KEY, and
network access to eToro's API):

    cd /root/AI-Trading-Bot
    source venv/bin/activate
    python3 manual_etoro_test_buy.py

Uses a small trade amount and EURUSD=X specifically because it's not
currently "already held" per the journal (unlike USDJPY=X/CL=F), so
this will actually attempt a fresh order rather than being skipped.
"""

from engines import tenant_engine as tenant
from engines import saas_broker_factory as factory

TEST_TICKER = "EURUSD=X"
# eToro enforces a $1000 MINIMUM LEVERAGED notional (amount x leverage) on
# forex/commodities -- see etoro_broker.py's ETORO_LEVERAGE=10 docstring.
# $50 x 10 = $500 was below that floor and got silently rejected (no
# exception, just no confirmed fill) on the first run of this script.
# $100 x 10 = $1000 exactly clears it (live-confirmed in etoro_broker.py's
# own history, positionID 3574717554).
TEST_AMOUNT_USD = 100.0


def main():
    user_ids = tenant.list_active_users()
    if not user_ids:
        print("No active users found -- nothing to test against.")
        return

    user_id = user_ids[0]
    print(f"Using user_id={user_id}")

    print(f"Checking eToro connection for this user...")
    connection = factory.check_user_etoro_connection(user_id)
    print(f"  connected={connection.get('connected')} cash={connection.get('cash')} error={connection.get('error')}")
    if not connection.get("connected"):
        print("Not connected -- aborting before placing an order.")
        return

    print(f"\nPlacing REAL eToro Demo BUY: {TEST_TICKER} for ${TEST_AMOUNT_USD:.2f}...")
    try:
        result = factory.buy_etoro_for_user(user_id, TEST_TICKER, TEST_AMOUNT_USD)
    except Exception as e:
        print(f"FAILED with exception: {e}")
        return

    print("\nResult:")
    print(f"  position_id      = {result.get('position_id')}")
    print(f"  executed_price   = {result.get('executed_price')}")
    print(f"  trailing_stop_set = {result.get('trailing_stop_set')}")
    if result.get("position_id") is None:
        print("\nOrder submitted but no confirmed fill within the poll window -- "
              "check the eToro Demo dashboard directly to see if it filled late.")
    else:
        print("\nSUCCESS: real eToro Demo order confirmed filled through the SaaS execution path.")


if __name__ == "__main__":
    main()
