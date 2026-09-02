"""
test_mt_connection.py -- one-off connectivity test for mt_broker.py
(task #231). Mirrors test_etoro_trade.py's role for the eToro
integration: exercises the REAL mt_broker.py functions end-to-end
against a real MetaApi account + a real (demo) MT4/MT5 account, rather
than testing the SDK in isolation, so this proves the actual code
Phase 2 would depend on.

Reads MT_TEST_LOGIN / MT_TEST_PASSWORD / MT_TEST_SERVER / METAAPI_TOKEN
from .env (see mt_test_env_setup.txt) -- never hardcode credentials
here directly.

Usage:
    python3 test_mt_connection.py
"""

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

import mt_broker
from engines import tenant_engine as tenant

_TEST_USER_ID = "test_mt_user_debug"


async def main():
    login = os.environ.get("MT_TEST_LOGIN")
    password = os.environ.get("MT_TEST_PASSWORD")
    server = os.environ.get("MT_TEST_SERVER")

    missing = [name for name, val in [
        ("MT_TEST_LOGIN", login), ("MT_TEST_PASSWORD", password),
        ("MT_TEST_SERVER", server),
    ] if not val]
    if missing:
        print(f"Missing from .env: {', '.join(missing)}. Check for a "
              f"stray '#' still commenting a line out.")
        return

    print(f"1. Saving test credentials for user_id={_TEST_USER_ID!r} "
          f"(login={login}, server={server}, platform=mt5)...")
    await mt_broker.save_mt_credentials(
        _TEST_USER_ID, login, password, server, platform="mt5", environment="demo",
    )
    print("   Saved (encrypted) via tenant_engine.save_broker_credentials.\n")

    print("2. Calling check_user_mt_connection() -- this will create the "
          "MetaApi account, deploy it, connect, read account info, then "
          "undeploy again. Expect this to take 30-90+ seconds...\n")
    result = await mt_broker.check_user_mt_connection(_TEST_USER_ID)

    print("3. Result:")
    for key, value in result.items():
        print(f"   {key}: {value}")

    if result["connected"]:
        print("\nCONNECTED SUCCESSFULLY. mt_broker.py's core connect/deploy/"
              "undeploy cycle works against a real account.")
        print("Next: try get_user_mt_positions() (should be empty on a "
              "fresh demo account) and, if you want to go further, a real "
              "execute_buy() test with a tiny volume like 0.01.")
    else:
        print("\nCONNECTION FAILED -- see 'error' above. Common causes: "
              "wrong server name, MetaApi couldn't auto-detect the broker's "
              "server file (E_SRV_NOT_FOUND), or wrong login/password "
              "(E_AUTH). Do not wire mt_broker.py into any live engine "
              "until this passes.")

    # Cleanup: remove the test credential row so it doesn't linger in the
    # real tenant_engine database under a fake user_id.
    creds = tenant.get_broker_credentials(_TEST_USER_ID, mt_broker.BROKER_CODE)
    if creds is not None:
        print(f"\n(Test credentials for {_TEST_USER_ID!r} left in place "
              f"for now in case you want to re-run get_user_mt_positions() "
              f"manually -- delete manually from user_broker_credentials "
              f"once you're done testing.)")


if __name__ == "__main__":
    asyncio.run(main())
