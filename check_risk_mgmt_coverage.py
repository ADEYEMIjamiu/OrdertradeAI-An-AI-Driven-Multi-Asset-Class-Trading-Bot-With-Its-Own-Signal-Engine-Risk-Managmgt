"""
check_risk_mgmt_coverage.py -- one-off diagnostic, 2026-08-23.

Read-only check: for every crypto ticker currently held on Binance
testnet, mirrors apply_crypto_risk_management()'s exact entry-price
lookup (recent_orders = order_manager.load_orders(limit=200), find the
most recent FILLED BUY for that ticker) to see whether risk management
can actually find an entry price for it right now. If entry_order is
None, apply_crypto_risk_management() silently `continue`s past that
ticker every single cycle -- no stop-loss, no take-profit, no lifecycle
management, no error message anywhere.

This does NOT place any orders. Purely read-only.

Run on the droplet:
    cd /root/AI-Trading-Bot
    source venv/bin/activate
    python3 check_risk_mgmt_coverage.py
"""

import binance_broker
import engines.order_manager as order_manager

positions = binance_broker.get_positions()

print(f"Currently held: {len(positions)} crypto positions.")
print(f"Using order_manager.get_most_recent_filled_buy() -- direct per-ticker DB query, not a capped recent-orders window.\n")

covered = []
uncovered = []

for position in positions:
    ticker = str(position["symbol"]).upper().strip()

    entry_order = order_manager.get_most_recent_filled_buy(ticker, "binance")

    if entry_order is None:
        uncovered.append(ticker)
        print(f"{ticker:12s} qty={position['qty']:15.4f}  NO ENTRY FOUND -- risk management SKIPS this ticker every cycle")
    else:
        entry_price = float(entry_order["filled_price"])
        try:
            current_price = binance_broker.get_current_price(ticker)
            change_pct = ((current_price / entry_price) - 1) * 100
            print(
                f"{ticker:12s} qty={position['qty']:15.4f}  entry=${entry_price:<12} "
                f"current=${current_price:<12}  change={change_pct:+.2f}%  "
                f"(entry order from {entry_order.get('updated_at')})"
            )
        except Exception as e:
            print(f"{ticker:12s} qty={position['qty']:15.4f}  entry found (${entry_price}) but live price fetch failed: {e}")
        covered.append(ticker)

print(f"\n=== Summary ===")
print(f"Covered (risk management CAN evaluate): {len(covered)} -- {covered}")
print(f"UNCOVERED (risk management silently skips): {len(uncovered)} -- {uncovered}")
