"""
check_etoro_trailing_stop.py -- one-off, 2026-08-24.

User noticed the OIL eToro position gave back ~$57 of weekend profit
(peaked ~$70+, now ~$13) without its stop-loss ever moving up. On the
dashboard, SL is still exactly 81.67 -- the same static 3% fixed stop
computed from the 84.18 entry price, unchanged. That's the visible
symptom of set_trailing_stop() (etoro_broker.py) having possibly
failed silently when this position opened (2026-08-18 17:53) -- that
conversion was explicitly marked "NOT yet live-tested" as of when it
was built (2026-08-05).

This pulls the RAW eToro portfolio response directly (not through
get_positions(), which only exposes symbol/qty/position_id) so the
actual stopLossType field eToro is enforcing right now is visible,
regardless of what the dashboard UI shows.
"""

from etoro_broker import _fetch_client_portfolio

portfolio = _fetch_client_portfolio()
positions = portfolio.get("positions", [])

print(f"Open eToro positions: {len(positions)}\n")
for p in positions:
    print("=" * 70)
    for key, value in p.items():
        print(f"  {key}: {value}")
    print()
