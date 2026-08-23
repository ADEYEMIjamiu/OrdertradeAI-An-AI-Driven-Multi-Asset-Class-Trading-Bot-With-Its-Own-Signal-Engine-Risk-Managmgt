"""
check_cluster_trade_origin.py -- one-off, 2026-08-23.

Follow-up: were the four correlated overnight losers (LPT-USD, ADA-USD,
DOT-USD, FET-USD -- all bought within seconds of each other on 8/22
evening, most stopped out together on 8/23 morning) a coincidence, or
did they share a common origin -- same engine (main AI vs. crypto
scalper), same confidence/reason, same asset-universe category?

Prints every trade_journal row for the four tickers around that window,
with confidence/trend_score/reason/mode -- whichever field distinguishes
the scalper's entries from the main engine's.
"""

from trade_journal import load_trade_journal

TICKERS = {"LPT-USD", "ADA-USD", "DOT-USD", "FET-USD"}

rows = load_trade_journal()
columns = ["time", "ticker", "action", "price", "shares", "amount",
           "confidence", "trend_score", "reason", "mode"]
trades = [dict(zip(columns, row)) for row in rows]

relevant = [t for t in trades if t["ticker"] in TICKERS and t["time"].startswith("2026-08-2")]
relevant.sort(key=lambda t: t["time"])

print(f"{'TIME':<20} {'TICKER':<10} {'ACTION':<6} {'PRICE':>10} {'CONF':>6} {'MODE':<12} {'REASON'}")
print("-" * 100)
for t in relevant:
    print(
        f"{t['time']:<20} {t['ticker']:<10} {t['action']:<6} {t['price']:>10} "
        f"{str(t['confidence']):>6} {str(t['mode']):<12} {t['reason']}"
    )
