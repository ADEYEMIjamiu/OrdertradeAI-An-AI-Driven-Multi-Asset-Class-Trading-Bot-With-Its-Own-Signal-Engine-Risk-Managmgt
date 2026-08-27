"""
diagnose_sol_exit_failure.py -- one-off, 2026-08-27.

Two consecutive fixes to saas_exit_engine.py's crypto SELL sizing
(commits d2788f5, 28d64fe) have NOT stopped the live "SOL-USD: Broker
sell failed ... insufficient balance" error on user 2aff7644-222d-45ba-
98a0-04df415aeb67 -- meaning the actual root cause is still unknown.
This prints exactly what the live Binance testnet account reports for
SOL right now, and what the journal thinks the position looks like, so
the real cause can be diagnosed from data instead of another guess.

Run on the droplet:
    cd /root/AI-Trading-Bot
    source venv/bin/activate
    python3 diagnose_sol_exit_failure.py
"""

import engines.saas_broker_factory as factory
import engines.saas_order_manager as journal

USER_ID = "2aff7644-222d-45ba-98a0-04df415aeb67"
TICKER = "SOL-USD"

print(f"=== Journal state for {TICKER}, user {USER_ID} ===")
entry_order = journal.get_most_recent_filled_buy_for_user(USER_ID, TICKER, "BINANCE")
if entry_order is None:
    print("No FILLED BUY order found in the journal for this ticker/user/broker.")
else:
    for key in ["order_id", "ticker", "side", "quantity", "filled_quantity",
                "price", "filled_price", "status", "stop_loss", "take_profit",
                "created_at", "updated_at", "broker_order_id", "strategy"]:
        print(f"  {key}: {entry_order.get(key)}")

is_open = journal.has_open_position_for_user(USER_ID, TICKER, "BINANCE") if hasattr(journal, "has_open_position_for_user") else None
print(f"\njournal.has_open_position_for_user: {is_open}")

print(f"\n=== Live Binance testnet exchange state ===")
try:
    exchange = factory._require_binance_exchange(USER_ID)
except Exception as e:
    print(f"Could not build exchange client: {e}")
    exchange = None

if exchange is not None:
    try:
        balance = exchange.fetch_balance()
        sol_free = balance.get("free", {}).get("SOL")
        sol_used = balance.get("used", {}).get("SOL")
        sol_total = balance.get("total", {}).get("SOL")
        print(f"  SOL free:  {sol_free}")
        print(f"  SOL used:  {sol_used}")
        print(f"  SOL total: {sol_total}")
    except Exception as e:
        print(f"  fetch_balance() failed: {e}")

    symbol = factory._to_binance_symbol(TICKER)
    print(f"\n  Symbol used for orders: {symbol}")
    try:
        ticker_data = exchange.fetch_ticker(symbol)
        print(f"  Current price: {ticker_data.get('last')}")
    except Exception as e:
        print(f"  fetch_ticker() failed: {e}")

    try:
        market = exchange.market(symbol)
        limits = market.get("limits", {})
        precision = market.get("precision", {})
        print(f"  Market limits: {limits}")
        print(f"  Market precision: {precision}")
    except Exception as e:
        print(f"  Could not load market info for {symbol}: {e}")

    try:
        open_orders = exchange.fetch_open_orders(symbol)
        print(f"\n  Open orders on {symbol}: {len(open_orders)}")
        for o in open_orders:
            print(f"    {o.get('id')}: {o.get('side')} {o.get('amount')} @ {o.get('price')} status={o.get('status')}")
    except Exception as e:
        print(f"  fetch_open_orders() failed (may be unsupported on testnet): {e}")

print(f"\n=== What get_user_crypto_held_qty() actually returns ===")
real_qty = factory.get_user_crypto_held_qty(USER_ID, TICKER)
print(f"  get_user_crypto_held_qty(user, 'SOL-USD') = {real_qty}")

if entry_order is not None:
    journaled_qty = float(entry_order.get("filled_quantity") or entry_order.get("quantity") or 0)
    attempted_qty = min(journaled_qty, real_qty)
    print(f"  Journaled quantity:  {journaled_qty}")
    print(f"  Quantity that WOULD be sent to sell_crypto_for_user(): {attempted_qty}")

print("\nDone.")
