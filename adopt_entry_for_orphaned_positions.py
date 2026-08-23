"""
adopt_entry_for_orphaned_positions.py -- one-off, 2026-08-23.

12 crypto positions (LTC-USD, XRP-USD, LINK-USD, XLM-USD, THETA-USD,
ATOM-USD, AVAX-USD, NEAR-USD, INJ-USD, WLD-USD, RENDER-USD, VIRTUAL-USD)
have no FILLED BUY order recorded anywhere in the persisted order book,
so apply_crypto_risk_management() can't find an entry price and silently
skips them every cycle -- confirmed via check_risk_mgmt_coverage.py.
Their true original entry price/date is unrecoverable.

Per explicit user decision (2026-08-23): rather than guess a historical
entry price, this creates a synthetic FILLED BUY order for each,
dated now, priced at the current live Binance price, sized to the
actual wallet quantity currently held. From this point forward, risk
management will see a valid entry (= today's price) and start applying
stop-loss/take-profit/lifecycle protection to them. This deliberately
does NOT create any real order or touch the exchange -- it only writes
a record to the local order book so entry-price lookups succeed.
strategy="ADOPTED_ENTRY_2026-08-23" marks these clearly as synthetic,
distinct from real AI-driven trades, for anyone auditing the order book
later.

This does NOT affect trade_journal FIFO accounting / position-cap
counting (performance_engine.py reads a separate source) or realized
P&L history -- these 12 positions' true historical performance remains
unknown and unrecoverable. This only unblocks going-forward protection.

Run on the droplet:
    cd /root/AI-Trading-Bot
    source venv/bin/activate
    python3 adopt_entry_for_orphaned_positions.py
"""

import binance_broker
import engines.order_manager as order_manager

ORPHANED_TICKERS = {
    "LTC-USD", "XRP-USD", "LINK-USD", "XLM-USD", "THETA-USD", "ATOM-USD",
    "AVAX-USD", "NEAR-USD", "INJ-USD", "WLD-USD", "RENDER-USD", "VIRTUAL-USD",
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
        strategy="ADOPTED_ENTRY_2026-08-23",
        confidence=0,
        ai_trade_score=0,
        priority=None,
    )
    order = order_manager.mark_order_submitted(order, broker_order_id="ADOPTED_ENTRY")
    order = order_manager.mark_order_filled(order, filled_price=current_price, filled_quantity=qty)
    order_manager.save_order(order)

    print(f"{ticker:12s} entry adopted at ${current_price}  qty={qty}")

print("\nDone. Re-run check_risk_mgmt_coverage.py to confirm all positions are now covered.")
