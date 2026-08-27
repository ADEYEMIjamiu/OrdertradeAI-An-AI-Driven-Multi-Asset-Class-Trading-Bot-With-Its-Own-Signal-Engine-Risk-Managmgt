"""
reconcile_closed_sol_position.py -- one-off, 2026-08-27.

Mirror-image of adopt_entry_for_orphaned_positions_2.py. That script
handles positions the wallet HOLDS with no journaled BUY; this handles
the opposite case: a position the journal thinks is still OPEN with no
journaled SELL, even though the live wallet actually holds zero.

Confirmed via diagnose_sol_exit_failure.py (run on the droplet,
2026-08-27): user 2aff7644-222d-45ba-98a0-04df415aeb67's SOL-USD
position (order_id 472ff681-aa9d-4931-9b74-c30d191006b9, FILLED BUY,
9.164219 SOL @ $96.70 on 2026-08-26T18:43:41) is stuck open in the
journal, but Binance testnet shows SOL free/used/total all 0.0. The
saas_exit_engine.py fix from commit d2788f5/28d64fe already stops this
from causing a crash -- it now logs a harmless skip message instead of
a raw "insufficient balance" error -- but with nothing to flip the
journal's state, that skip message will repeat every 5-minute tick
forever, since journal.has_open_position_for_user() will keep
returning True.

engines/saas_order_manager.py's has_open_position_for_user() looks at
each ticker's most recently *updated* FILLED order's side (see
_latest_filled_status_by_ticker's docstring) -- so writing a new FILLED
SELL order for this ticker/user/broker right now (which will naturally
get the latest updated_at on insert) is enough to flip it closed. No
real order is placed and the exchange is never touched -- this only
corrects the local journal to match reality.

Since the wallet holds zero, there's no real fill price to record for
this synthetic close -- it's priced at the current live SOL-USD mark
purely for bookkeeping/journal completeness (P&L on this specific
"exit" is not meaningful; the position was actually already closed
outside this journal, exact real exit price unknown).
strategy="RECONCILED_CLOSED_2026-08-27" marks this as synthetic
bookkeeping, following the ADOPTED_ENTRY_* naming convention already
used for the mirror-image case.

Run on the droplet:
    cd /root/AI-Trading-Bot
    source venv/bin/activate
    python3 reconcile_closed_sol_position.py
"""

import engines.saas_broker_factory as factory
import engines.saas_order_manager as journal

USER_ID = "2aff7644-222d-45ba-98a0-04df415aeb67"
TICKER = "SOL-USD"
BROKER = "BINANCE"

print(f"Reconciling {TICKER} for user {USER_ID} ({BROKER})...\n")

# Safety check 1: only do this if the journal actually still thinks
# it's open -- don't write a redundant/confusing extra SELL if a prior
# run (or the real exit engine) already closed it.
if not journal.has_open_position_for_user(USER_ID, TICKER, BROKER):
    print(f"{TICKER:12s} SKIPPED -- journal already shows this closed. Nothing to do.")
    raise SystemExit(0)

# Safety check 2: only do this if the wallet genuinely holds zero --
# don't blow away a real open position by mistake.
real_qty = factory.get_user_crypto_held_qty(USER_ID, TICKER)
if real_qty > 0:
    print(f"{TICKER:12s} ABORTED -- wallet actually holds {real_qty} {TICKER}, "
          f"this is NOT an orphaned-closed position. Not touching the journal.")
    raise SystemExit(1)

entry_order = journal.get_most_recent_filled_buy_for_user(USER_ID, TICKER, BROKER)
if entry_order is None:
    print(f"{TICKER:12s} ABORTED -- no FILLED BUY found to reconcile against.")
    raise SystemExit(1)

journaled_qty = float(entry_order.get("filled_quantity") or entry_order.get("quantity") or 0)

# No dedicated per-user price-lookup helper exists in saas_broker_factory,
# so fetch the live mark directly -- same call buy_crypto_for_user() makes
# internally. This is bookkeeping only (the position is already gone from
# the wallet), so the exact price doesn't affect anything real; it just
# keeps the journal's numbers plausible instead of leaving them at $0.
try:
    exchange = factory._require_binance_exchange(USER_ID)
    symbol = factory._to_binance_symbol(TICKER)
    current_price = float(exchange.fetch_ticker(symbol)["last"])
except Exception as e:
    print(f"  (could not fetch live price, falling back to entry price: {e})")
    current_price = float(entry_order.get("filled_price") or entry_order.get("price") or 0)

order = journal.create_order(
    user_id=USER_ID,
    ticker=TICKER,
    side="SELL",
    quantity=journaled_qty,
    trade_amount=journaled_qty * current_price,
    price=current_price,
    asset_class="CRYPTO",
    broker=BROKER,
    strategy="RECONCILED_CLOSED_2026-08-27",
    confidence=0,
    ai_trade_score=0,
    priority=None,
)
order = journal.mark_order_submitted(order, broker_order_id="RECONCILED_CLOSED")
order = journal.mark_order_filled(order, filled_price=current_price, filled_quantity=journaled_qty)
journal.save_order(order)

still_open = journal.has_open_position_for_user(USER_ID, TICKER, BROKER)
print(f"{TICKER:12s} reconciled -- synthetic SELL written (qty={journaled_qty} @ ${current_price}).")
print(f"has_open_position_for_user now returns: {still_open}")
assert still_open is False, "Reconciliation did not flip the journal closed -- investigate."

print("\nDone. The SOL-USD skip message should not appear again on future ticks.")
