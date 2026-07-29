"""
One-off utility: closes every open position in your Alpaca PAPER account.

This does NOT touch real money — it only works against the paper-trading
endpoint, same as the rest of this app. Use this whenever you want a truly
clean slate (the in-app "Reset" buttons only clear local Streamlit state,
they cannot touch positions that already exist on Alpaca's servers).

Run from the project root:
    python3 close_all_alpaca_positions.py
"""

from broker import client

open_positions = client.get_all_positions()

if not open_positions:
    print("✅ No open positions on Alpaca — already clean.")
else:
    print(f"Found {len(open_positions)} open position(s):")
    for p in open_positions:
        print(f"  {p.symbol}: {p.qty} shares @ avg entry {p.avg_entry_price}")

    confirm = input("\nClose ALL of these positions now? (yes/no): ").strip().lower()

    if confirm == "yes":
        client.close_all_positions(cancel_orders=True)
        print("✅ Close-all request submitted. Positions will clear shortly.")
    else:
        print("Cancelled — nothing was closed.")
