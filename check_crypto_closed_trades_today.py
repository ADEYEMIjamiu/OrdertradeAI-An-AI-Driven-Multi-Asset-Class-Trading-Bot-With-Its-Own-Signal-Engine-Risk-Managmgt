"""
check_crypto_closed_trades_today.py -- one-off, 2026-08-23.

Follow-up to check_crypto_closed_trades_detail.py: that script listed
every closed crypto round-trip since CRYPTO_VALIDATION_START (13
total). This narrows to just today's exits, to match exactly what the
dashboard's Performance Digest "Today" filter is showing (10 trades
closed, 3 wins / 7 losses, -$13.20, profit factor 0.16) -- so every one
of those 10 is visible, not just the four tickers already traced
(LPT/ADA/DOT/FET).
"""

from datetime import datetime
from engines.performance_engine import (
    get_closed_trades_and_open_lots,
    _filter_pre_pyramiding_fix_crypto,
)

TODAY = datetime.now().date()

closed_trades, _ = get_closed_trades_and_open_lots()
crypto_closed = [t for t in closed_trades if str(t["ticker"]).upper().endswith("-USD")]
crypto_closed = _filter_pre_pyramiding_fix_crypto(crypto_closed)

def _exit_date(t):
    try:
        return datetime.strptime(t["exit_time"], "%Y-%m-%d %H:%M:%S").date()
    except (TypeError, ValueError):
        return None

todays_trades = [t for t in crypto_closed if _exit_date(t) == TODAY]
todays_trades.sort(key=lambda t: t["exit_time"])

print(f"Today ({TODAY}) closed crypto trades: {len(todays_trades)}")
print("=" * 90)
print(f"{'TICKER':<10} {'ENTRY':>10} {'EXIT':>10} {'SHARES':>10} {'P&L $':>10} {'P&L %':>8}  {'EXIT TIME'}")
print("-" * 90)
for t in todays_trades:
    print(
        f"{t['ticker']:<10} {t['entry_price']:>10.4f} {t['exit_price']:>10.4f} "
        f"{t['shares']:>10.4f} {t['pnl']:>10.2f} {t['pnl_percent']:>7.2f}%  {t['exit_time']}"
    )

print("=" * 90)
total = sum(t["pnl"] for t in todays_trades)
wins = sum(1 for t in todays_trades if t["pnl"] > 0)
losses = sum(1 for t in todays_trades if t["pnl"] <= 0)
print(f"TOTAL: ${total:.2f}  |  Wins: {wins}  Losses: {losses}  |  Count: {len(todays_trades)}")
