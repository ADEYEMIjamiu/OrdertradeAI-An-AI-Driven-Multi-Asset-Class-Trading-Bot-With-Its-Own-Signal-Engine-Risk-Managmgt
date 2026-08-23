"""
check_bnb_history.py -- one-off diagnostic, 2026-08-23.

Pull BNB-USD's complete order history (all BUYs and SELLs, all statuses)
from the persistent order book, oldest first, to find the original entry
price and compute realized P&L on today's rotation SELL.

Run on the droplet:
    cd /root/AI-Trading-Bot
    source venv/bin/activate
    python3 check_bnb_history.py
"""

import engines.order_manager as order_manager

all_orders = order_manager.load_orders(limit=100000)
bnb_orders = [o for o in all_orders if str(o.get("ticker", "")).upper() == "BNB-USD"]
bnb_orders.sort(key=lambda o: o.get("updated_at") or "")

print(f"Found {len(bnb_orders)} BNB-USD orders total (all statuses).\n")

for o in bnb_orders:
    print(
        f"{o.get('updated_at')}  {o.get('side'):5s}  {o.get('broker'):10s}  "
        f"status={o.get('status'):10s}  qty={o.get('quantity')}  "
        f"price={o.get('price')}  filled_price={o.get('filled_price')}  "
        f"amount={o.get('amount')}"
    )

filled_buys = [o for o in bnb_orders if o.get("side", "").upper() == "BUY" and o.get("status") == "FILLED"]
filled_sells = [o for o in bnb_orders if o.get("side", "").upper() == "SELL" and o.get("status") == "FILLED"]

if filled_buys and filled_sells:
    first_buy = filled_buys[0]
    last_sell = filled_sells[-1]
    buy_price = float(first_buy.get("filled_price") or first_buy.get("price") or 0)
    sell_price = float(last_sell.get("filled_price") or last_sell.get("price") or 0)
    qty = float(last_sell.get("quantity") or 0)

    if buy_price > 0:
        pnl = (sell_price - buy_price) * qty
        pct = ((sell_price - buy_price) / buy_price) * 100
        print(f"\n=== Realized result ===")
        print(f"Entry (first FILLED BUY): ${buy_price} on {first_buy.get('updated_at')}")
        print(f"Exit  (last FILLED SELL): ${sell_price} on {last_sell.get('updated_at')}")
        print(f"Quantity: {qty}")
        print(f"P&L: ${pnl:,.2f}  ({pct:+.2f}%)")
    else:
        print("\nCould not find a usable BUY price to compute P&L.")
else:
    print("\nNo matching FILLED BUY and/or FILLED SELL found for BNB-USD.")
