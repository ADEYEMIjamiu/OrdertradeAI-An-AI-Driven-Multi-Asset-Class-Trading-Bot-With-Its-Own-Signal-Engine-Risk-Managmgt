"""
adopt_entry_for_orphaned_positions_2.py -- one-off, 2026-08-27.

Second occurrence of the exact same problem adopt_entry_for_orphaned_positions.py
fixed on 2026-08-23 for 12 tickers. Confirmed live via journalctl (2026-08-27,
right after the TRADE_PLAN_LOOKBACK_DAYS deploy): apply_crypto_risk_management()
is now alerting -- exactly as the 2026-08-24 fix in app.py intended -- for
4 NEW orphaned positions that weren't part of the original batch:

    ZEC-USD, BCH-USD, AAVE-USD, PYTH-USD

These 4 were added to data/asset_universe.py's tracked CRYPTO list on
2026-08-23 (see that file's own comment) -- the same day the original
adopt-entry script ran, but not included in it, so they were never
covered. Same root cause as before: Binance testnet accounts commonly
come pre-seeded with nonzero balances of tracked coins that were never
bought through the bot, so no FILLED BUY order exists for them anywhere
in the local order book, and stop-loss/take-profit/lifecycle protection
can't apply without a known entry price.

Per the same reasoning as the original 2026-08-23 fix: rather than guess
a historical entry price, this creates a synthetic FILLED BUY order for
each, dated now, priced at the current live Binance price, sized to the
actual wallet quantity currently held. This deliberately does NOT create
any real order or touch the exchange -- it only writes a record to the
local order book so entry-price lookups succeed going forward.
strategy="ADOPTED_ENTRY_2026-08-27" marks these as synthetic and dates
them distinctly from the first batch's ADOPTED_ENTRY_2026-08-23 marker.

This does NOT recover these positions' true historical entry/P&L -- that
remains unknown. This only unblocks going-forward protection.

WORTH DOING ONCE, NOT REPEATING FOREVER: this is the second time this
exact class of bug has needed a manual one-off fix (once per newly-added
ticker batch). If a third asset_universe.py CRYPTO expansion happens,
consider building a permanent automatic-adoption safeguard directly into
apply_crypto_risk_management() instead of a third one-off script --
flagged here, not built here, since that's a bigger design decision than
this immediate fix.

Run on the droplet:
    cd /root/AI-Trading-Bot
    source venv/bin/activate
    python3 adopt_entry_for_orphaned_positions_2.py
"""

import binance_broker
import engines.order_manager as order_manager

ORPHANED_TICKERS = {
    "ZEC-USD", "BCH-USD", "AAVE-USD", "PYTH-USD",
}

positions_by_symbol = {
    str(p["symbol"]).upper().strip(): float(p["qty"])
    for p in binance_broker.get_positions()
}

print(f"Adopting today's price as entry for {len(ORPHANED_TICKERS)} orphaned positions.\n")

for ticker in sorted(ORPHANED_TICKERS):
    qty = positions_by_symbol.get(ticker)
    if qty is None or qty <= 0:
        print(f"{ticker:12s} SKIPPED -- not currently held (qty missing or zero)")
        continue

    # Double-check nothing slipped through since the last check (don't
    # overwrite a real recoverable entry if one exists after all).
    existing = order_manager.get_most_recent_filled_buy(ticker, "binance")
    if existing is not None:
        print(f"{ticker:12s} SKIPPED -- an entry order already exists (${existing['filled_price']}), not overwriting")
        continue

    try:
        current_price = binance_broker.get_current_price(ticker)
    except Exception as e:
        print(f"{ticker:12s} SKIPPED -- could not fetch live price: {e}")
        continue

    order = order_manager.create_order(
        ticker=ticker,
        side="BUY",
        quantity=qty,
        trade_amount=qty * current_price,
        price=current_price,
        asset_class="CRYPTO",
        broker="binance",
        strategy="ADOPTED_ENTRY_2026-08-27",
        confidence=0,
        ai_trade_score=0,
        priority=None,
    )
    order = order_manager.mark_order_submitted(order, broker_order_id="ADOPTED_ENTRY")
    order = order_manager.mark_order_filled(order, filled_price=current_price, filled_quantity=qty)
    order_manager.save_order(order)

    print(f"{ticker:12s} entry adopted at ${current_price}  qty={qty}")

print("\nDone. Re-run check_risk_mgmt_coverage.py to confirm all positions are now covered.")
