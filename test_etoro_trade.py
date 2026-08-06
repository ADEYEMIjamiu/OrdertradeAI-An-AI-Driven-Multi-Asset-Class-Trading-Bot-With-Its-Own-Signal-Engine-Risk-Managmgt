"""
Standalone, one-off test of etoro_broker.buy() and etoro_broker.close_position()
-- the two functions that actually move positions, which check_broker_connection()
and get_positions() never exercised. Not imported by app.py; this is purely a
manual "prove it works before trusting it" step, same discipline used for every
other broker connector in this project.

Deliberately small and boring: a $50 demo BUY on AAPL, nothing exotic. Refuses
to run at all if ETORO_ENVIRONMENT isn't "demo" -- see the IS_DEMO guard below --
so this can never accidentally place a real order.

Usage (two separate steps, on purpose -- go check the eToro website in between
so you're visually confirming each half rather than trusting the script blindly):

    python3 test_etoro_trade.py buy
    # ... go look at your eToro Demo Portfolio page in the browser ...
    python3 test_etoro_trade.py close
    # ... go confirm the position is gone / shows in closed history ...

Optional second argument to "buy" overrides the ticker (still defaults to
AAPL):

    python3 test_etoro_trade.py buy BTC
    python3 test_etoro_trade.py close

NOTE (2026-08-02): AAPL is a real NYSE-listed stock, so a market BUY placed
while the US stock market is closed sits queued in eToro's "ordersForOpen"
list (statusID 11) rather than filling immediately -- confirmed live
against the real Demo account, this is expected broker behaviour, not a
bug. Use "BTC" (or another 24/7 crypto instrument) to validate the
buy/close mechanics on any day of the week; come back to AAPL during NYSE
hours for the real stock-specific validation.
"""

import json
import sys

import etoro_broker

STATE_FILE = "etoro_test_position.json"
DEFAULT_TICKER = "AAPL"
TEST_AMOUNT_USD = 50


def do_buy(ticker):
    print(f"eToro environment: {'DEMO' if etoro_broker.IS_DEMO else 'REAL'}")
    if not etoro_broker.IS_DEMO:
        print("ETORO_ENVIRONMENT is not 'demo' -- refusing to run this test. Aborting.")
        return

    print(f"Opening a ${TEST_AMOUNT_USD} demo BUY on {ticker}...")
    try:
        result = etoro_broker.buy(ticker, TEST_AMOUNT_USD)
    except Exception as e:
        print(f"BUY failed: {e}")
        return

    print("Order result:", result)

    if result.get("position_id") is None:
        print(
            "WARNING: no position_id came back in the response above -- "
            "don't assume this worked. Check the raw response and the "
            "eToro dashboard before proceeding to 'close'."
        )
        return

    with open(STATE_FILE, "w") as f:
        json.dump({"ticker": ticker, "position_id": result["position_id"]}, f)

    print(f"\nSaved position_id {result['position_id']} to {STATE_FILE}.")
    print(
        f"Now go check your eToro Demo Portfolio page in the browser -- "
        f"you should see a new {ticker} position worth about ${TEST_AMOUNT_USD}."
    )
    print("Once you've confirmed it's really there, run: python3 test_etoro_trade.py close")


def do_close():
    print(f"eToro environment: {'DEMO' if etoro_broker.IS_DEMO else 'REAL'}")
    if not etoro_broker.IS_DEMO:
        print("ETORO_ENVIRONMENT is not 'demo' -- refusing to run this test. Aborting.")
        return

    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except FileNotFoundError:
        print(f"No {STATE_FILE} found -- run 'python3 test_etoro_trade.py buy' first.")
        return

    print(f"Closing position_id {state['position_id']} ({state['ticker']})...")
    try:
        result = etoro_broker.close_position(state["position_id"])
    except Exception as e:
        print(f"CLOSE failed: {e}")
        return

    print("Close result:", result)
    print(
        "\nNow go check your eToro Demo Portfolio page in the browser -- "
        "the position should be gone, and it should show up in your "
        "closed-trades / history view."
    )


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in ("buy", "close"):
        print("Usage: python3 test_etoro_trade.py buy [TICKER]")
        print("       python3 test_etoro_trade.py close")
        sys.exit(1)

    if sys.argv[1] == "buy":
        ticker = sys.argv[2] if len(sys.argv) == 3 else DEFAULT_TICKER
        do_buy(ticker)
    else:
        do_close()
