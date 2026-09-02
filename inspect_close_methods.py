"""
inspect_close_methods.py -- diagnostic for task #238 (asyncio cleanup
noise after rapid back-to-back mt_broker.py calls -- see test_mt_phase3.py's
final stack trace: "Unclosed client session" warnings plus a background
SubscriptionManager task crashing at process exit). mt_broker.py's sync
wrappers each spin up a fresh event loop via asyncio.run() and never
explicitly close the RPC connection or MetaApi client before that loop
tears down -- this script lists what close/disconnect-style methods
ACTUALLY exist on those objects (confirmed live, not guessed, same
discipline every other part of mt_broker.py was built with) so the real
fix can call the right one.

Run this BEFORE test_mt_cleanup.py -- it needs test_mt_user_debug's
still-existing MT4/5 credentials to open a connection to inspect.

Usage:
    python3 inspect_close_methods.py
"""

import asyncio

import mt_broker

_TEST_USER_ID = "test_mt_user_debug"


async def main():
    account = await mt_broker._get_or_create_metaapi_account(_TEST_USER_ID)
    connection = await mt_broker._deploy_and_connect(account)

    keywords = ("close", "disconnect", "stop", "dispose", "shutdown")

    print("=== connection methods mentioning close/disconnect/stop/dispose/shutdown ===")
    print(sorted(m for m in dir(connection) if any(k in m.lower() for k in keywords)))

    api = mt_broker._get_api()
    print("\n=== MetaApi client (api) methods mentioning close/disconnect/stop/dispose/shutdown ===")
    print(sorted(m for m in dir(api) if any(k in m.lower() for k in keywords)))

    print("\n=== account methods mentioning close/disconnect/stop/dispose/undeploy ===")
    print(sorted(m for m in dir(account) if any(k in m.lower() for k in keywords + ("undeploy",))))


if __name__ == "__main__":
    asyncio.run(main())
