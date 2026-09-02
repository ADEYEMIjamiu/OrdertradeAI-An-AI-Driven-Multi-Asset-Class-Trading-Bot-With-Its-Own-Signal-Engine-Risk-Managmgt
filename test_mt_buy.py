"""
test_mt_buy.py -- one-off live test of mt_broker.execute_buy_by_usd_amount()
against the real connected Pepperstone demo account (Phase 2, task #233).

Mirrors test_mt_connection.py's role: proves the ACTUAL code
saas_decision_engine.py will depend on, not just the SDK in isolation.
Places one small real (demo) EURUSD buy, sized off a small dollar amount
via the new leverage/contract-size-aware lot sizing, then prints the
RAW MetaApi order response so the real field names for confirming a
fill (order id / position id / state) can be read directly rather than
assumed -- same "confirm live, don't guess" approach used for every
other MetaApi detail this session (wait_connected, account list method,
symbol specification, leverage field, oil symbol name).

Usage:
    python3 test_mt_buy.py
"""

import asyncio
import json

import mt_broker

_TEST_USER_ID = "test_mt_user_debug"
_TEST_TICKER = "EURUSD=X"
_TEST_USD_AMOUNT = 50  # small on purpose -- this is a real order


async def main():
    print(f"1. Placing a real (demo) BUY: ${_TEST_USD_AMOUNT} of {_TEST_TICKER} "
          f"for user_id={_TEST_USER_ID!r}...\n")

    order = await mt_broker.execute_buy_by_usd_amount(
        _TEST_USER_ID, _TEST_TICKER, _TEST_USD_AMOUNT,
    )

    if order is None:
        print("Result: None -- trade_amount was too small to reach this "
              "symbol's minimum lot size. Try a larger _TEST_USD_AMOUNT.")
        return

    print("2. Raw order response from MetaApi:")
    print(json.dumps(order, indent=2, default=str))

    print("\n3. Checking open positions to confirm it actually filled...")
    positions = await mt_broker.get_user_mt_positions(_TEST_USER_ID)
    print(json.dumps(positions, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
