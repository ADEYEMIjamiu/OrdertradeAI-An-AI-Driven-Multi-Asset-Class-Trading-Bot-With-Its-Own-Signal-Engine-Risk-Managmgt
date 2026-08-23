"""
check_daily_trade_count.py -- one-off, 2026-08-23.

Mirrors risk_engine.py's trades_today counting exactly, to check whether
today's 12 synthetic "adopted entry" orders pushed the crypto daily
trade count anywhere near CRYPTO_MAX_TRADES_PER_DAY.
"""

from datetime import datetime
from config import CRYPTO_MAX_TRADES_PER_DAY
from engines.order_manager import load_orders

recent_orders = load_orders(limit=500)

def _order_timestamp(order):
    timestamp_text = order.get("filled_at") or order.get("updated_at") or order.get("created_at")
    if not timestamp_text:
        return None
    try:
        return datetime.fromisoformat(timestamp_text)
    except Exception:
        return None

today = datetime.now().date()
all_todays_crypto_orders = [
    o for o in recent_orders
    if str(o.get("asset_class", "")).upper() == "CRYPTO"
    and (lambda ts: ts is not None and ts.date() == today)(_order_timestamp(o))
]

adopted = [o for o in all_todays_crypto_orders if str(o.get("strategy", "")).startswith("ADOPTED_ENTRY_")]

# Mirrors risk_engine.py's fix: exclude ADOPTED_ENTRY_ synthetic orders
# from what actually counts toward the daily budget.
counted_orders = [
    o for o in all_todays_crypto_orders
    if not str(o.get("strategy", "")).startswith("ADOPTED_ENTRY_")
]

print(f"Total CRYPTO orders today (all): {len(all_todays_crypto_orders)}")
print(f"  -- of which synthetic ADOPTED_ENTRY (now excluded from counting): {len(adopted)}")
print(f"Counted toward daily budget: {len(counted_orders)}")
print(f"CRYPTO_MAX_TRADES_PER_DAY: {CRYPTO_MAX_TRADES_PER_DAY}")
print(f"Remaining budget for real trades today: {CRYPTO_MAX_TRADES_PER_DAY - len(counted_orders)}")
