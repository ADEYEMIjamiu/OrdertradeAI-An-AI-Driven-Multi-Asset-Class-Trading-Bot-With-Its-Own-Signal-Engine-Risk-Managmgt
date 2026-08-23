"""
diagnose_crypto_position_cap.py -- one-off diagnostic, 2026-08-22.

The dashboard's Crypto Positions table (after fixing binance_broker.py's
hardcoded TRACKED_ASSETS filter) revealed 22 of 23 crypto universe
tickers are simultaneously held, against MAX_CRYPTO_POSITIONS = 8. This
checks two possible explanations:

1. These are mostly OLD positions (opened weeks ago, before caps were
   enforced correctly) that have just been sitting there -- historical
   debt, not an active ongoing bug.
2. risk_engine.py's _get_crypto_position_count() is silently failing
   (it has a bare `except Exception: return 0` around the journal read)
   and returning 0, which would make can_open_position() think there's
   always room regardless of how many positions are actually open --
   an active, ongoing enforcement failure.

This calls the real functions DIRECTLY, without any try/except
swallowing, so any real exception surfaces instead of being hidden.

Run on the droplet:
    cd /root/AI-Trading-Bot
    source venv/bin/activate
    python3 diagnose_crypto_position_cap.py
"""

from datetime import datetime

from config import MAX_CRYPTO_POSITIONS, CRYPTO_VALIDATION_START
from engines import performance_engine

print("=" * 70)
print("STEP 1: calling get_closed_trades_and_open_lots() directly")
print("=" * 70)

# No try/except here on purpose -- if this throws, we WANT to see the
# real traceback, since risk_engine.py's _get_crypto_position_count()
# would otherwise silently swallow it and return 0.
closed_trades, open_lots = performance_engine.get_closed_trades_and_open_lots()

print(f"Loaded OK. {len(open_lots)} tickers have at least one open lot "
      f"(before any crypto-validation-cutoff filtering).\n")

print("=" * 70)
print("STEP 2: per-ticker open-lot breakdown for crypto (-USD) tickers")
print("=" * 70)
print(f"CRYPTO_VALIDATION_START cutoff: {CRYPTO_VALIDATION_START}\n")

crypto_tickers = {t: lots for t, lots in open_lots.items() if str(t).upper().endswith("-USD")}

for ticker in sorted(crypto_tickers):
    lots = crypto_tickers[ticker]
    times = []
    for lot in lots:
        t = performance_engine._parse_time(lot["time"])
        times.append(t)
    valid_times = [t for t in times if t is not None]
    excluded = sum(1 for t in valid_times if t < CRYPTO_VALIDATION_START)
    included = len(lots) - excluded
    earliest = min(valid_times) if valid_times else None
    latest = max(valid_times) if valid_times else None
    total_shares = sum(l["shares"] for l in lots)
    print(
        f"{ticker:12s} lots={len(lots):3d}  included={included:3d}  "
        f"excluded(pre-cutoff)={excluded:3d}  shares={total_shares:12.4f}  "
        f"earliest={earliest}  latest={latest}"
    )

print()
print("=" * 70)
print("STEP 3: what get_open_positions_cost_basis() actually returns")
print("=" * 70)

open_positions = performance_engine.get_open_positions_cost_basis()
crypto_open = {t: v for t, v in open_positions.items() if str(t).upper().endswith("-USD")}
print(f"Crypto tickers counted as OPEN (post-cutoff-filter): {len(crypto_open)}")
for ticker, info in sorted(crypto_open.items()):
    print(f"  {ticker:12s} shares={info['shares']:12.4f}  cost_basis=${info['cost_basis']:,.2f}")

print()
print("=" * 70)
print("STEP 4: what risk_engine._get_crypto_position_count() reports RIGHT NOW")
print("=" * 70)

from engines import risk_engine
live_count = risk_engine._get_crypto_position_count()
print(f"_get_crypto_position_count() returns: {live_count}")
print(f"MAX_CRYPTO_POSITIONS: {MAX_CRYPTO_POSITIONS}")

if live_count == 0 and len(crypto_open) > 0:
    print(
        "\n*** MISMATCH: the live cap-check function returned 0, but "
        f"{len(crypto_open)} crypto positions are actually open. This "
        "confirms the silent exception-swallowing theory -- something "
        "throws inside _get_crypto_position_count()'s try block, and "
        "the bare except is hiding it, making the position cap a no-op. ***"
    )
elif live_count >= MAX_CRYPTO_POSITIONS:
    print(
        f"\n*** live_count ({live_count}) already >= MAX_CRYPTO_POSITIONS "
        f"({MAX_CRYPTO_POSITIONS}) -- the cap SHOULD currently be blocking "
        "all new crypto BUYs. If new crypto buys are still happening "
        "after this, something else is bypassing this check. ***"
    )
else:
    print(
        f"\nlive_count ({live_count}) is under the cap ({MAX_CRYPTO_POSITIONS}) "
        f"-- but the dashboard shows {len(crypto_open)} tickers actually held. "
        "This gap itself needs explaining -- see Step 2/3 above for which "
        "specific tickers differ."
    )
