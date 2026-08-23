"""
check_crypto_closed_trades_detail.py -- one-off, 2026-08-23.

Follow-up to check_crypto_balance_growth.py: lists every closed crypto
round-trip (ticker, entry, exit, P&L) since CRYPTO_VALIDATION_START, so
it's clear which specific tickers are dragging the -$0.39 realized
total down vs. which are actually working.

Same FIFO-matched data source as the dashboard's Performance tab --
no separate calculation.
"""

from engines.performance_engine import (
    get_closed_trades_and_open_lots,
    _filter_pre_pyramiding_fix_crypto,
)

closed_trades, _ = get_closed_trades_and_open_lots()
crypto_closed = [t for t in closed_trades if str(t["ticker"]).upper().endswith("-USD")]
crypto_closed = _filter_pre_pyramiding_fix_crypto(crypto_closed)

# Sort worst to best so the biggest drag is at the top.
crypto_closed.sort(key=lambda t: t["pnl"])

print("=" * 78)
print(f"{'TICKER':<10} {'ENTRY':>10} {'EXIT':>10} {'SHARES':>10} {'P&L $':>10} {'P&L %':>8}")
print("=" * 78)
for t in crypto_closed:
    print(
        f"{t['ticker']:<10} {t['entry_price']:>10.4f} {t['exit_price']:>10.4f} "
        f"{t['shares']:>10.4f} {t['pnl']:>10.2f} {t['pnl_percent']:>7.2f}%"
    )

print("=" * 78)
total = sum(t["pnl"] for t in crypto_closed)
print(f"TOTAL REALIZED: ${total:.2f} across {len(crypto_closed)} closed trades")

print("\nWorst performers (dragging the total down):")
for t in crypto_closed:
    if t["pnl"] < 0:
        print(
            f"  {t['ticker']:<10} entered {t['entry_time']} @ {t['entry_price']:.4f}, "
            f"exited {t['exit_time']} @ {t['exit_price']:.4f}  ({t['pnl']:+.2f} / {t['pnl_percent']:+.2f}%)"
        )

print("\nBest performers:")
for t in sorted(crypto_closed, key=lambda t: -t["pnl"]):
    if t["pnl"] > 0:
        print(
            f"  {t['ticker']:<10} entered {t['entry_time']} @ {t['entry_price']:.4f}, "
            f"exited {t['exit_time']} @ {t['exit_price']:.4f}  ({t['pnl']:+.2f} / {t['pnl_percent']:+.2f}%)"
        )
