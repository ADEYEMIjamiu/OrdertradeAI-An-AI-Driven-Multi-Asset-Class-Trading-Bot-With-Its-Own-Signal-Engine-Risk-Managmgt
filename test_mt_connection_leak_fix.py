"""
test_mt_connection_leak_fix.py -- verifies the task #238 fix
(mt_broker.py's new _mt_connection() context manager, which explicitly
closes the RPC connection and MetaApi client after every call). Calls
check_user_mt_connection_sync() four times back-to-back -- the same
kind of rapid repeated calling that produced "Unclosed client session"
warnings and a crashing background task in test_mt_phase3.py -- and
prints PASS/FAIL based on whether any of those warnings appear on
stderr this time.

Re-saves test credentials first (test_mt_user_debug was fully removed
by test_mt_cleanup.py) -- reads MT_TEST_LOGIN/MT_TEST_PASSWORD/
MT_TEST_SERVER from .env, same as test_mt_connection.py -- never
hardcode credentials in this file. Run test_mt_cleanup.py again
afterward to remove it.

Usage:
    python3 test_mt_connection_leak_fix.py
"""

import io
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import mt_broker

_TEST_USER_ID = "test_mt_user_debug"


def main():
    login = os.environ.get("MT_TEST_LOGIN")
    password = os.environ.get("MT_TEST_PASSWORD")
    server = os.environ.get("MT_TEST_SERVER")
    missing = [name for name, val in [
        ("MT_TEST_LOGIN", login), ("MT_TEST_PASSWORD", password),
        ("MT_TEST_SERVER", server),
    ] if not val]
    if missing:
        print(f"Missing from .env: {', '.join(missing)}.")
        return

    print("1. Re-saving test credentials (removed by test_mt_cleanup.py)...")
    mt_broker.save_mt_credentials_sync(
        _TEST_USER_ID, login=login, password=password,
        server=server, platform="mt5", environment="demo",
    )

    print("2. Calling check_user_mt_connection_sync() 4 times back-to-back...")
    captured = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured
    try:
        for i in range(4):
            result = mt_broker.check_user_mt_connection_sync(_TEST_USER_ID)
            print(f"   Call {i + 1}: connected={result['connected']}")
    finally:
        sys.stderr = old_stderr

    stderr_output = captured.getvalue()
    print(stderr_output, file=sys.stderr)  # still show it, just also inspect it

    leak_markers = ("Unclosed client session", "Task exception was never retrieved")
    found = [m for m in leak_markers if m in stderr_output]

    print("\n3. Result:")
    if found:
        print(f"   *** FAIL: still seeing {found} in stderr. ***")
    else:
        print("   PASS: no 'Unclosed client session' / 'Task exception was never "
              "retrieved' warnings across 4 rapid calls.")


if __name__ == "__main__":
    main()
