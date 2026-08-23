"""
One-off diagnostic: for a shortlist of crypto tickers that are confirmed
tradable on Binance TESTNET (see check_binance_testnet_pairs.py's output),
checks whether engines/market_data_engine.py's get_market_data() -- the
same yfinance-backed function the AI model and dashboard already use for
every tracked ticker -- can actually pull live price history for each one.

A coin can be tradable on Binance testnet but still have thin, delayed,
or missing data on Yahoo Finance (yfinance's source), especially newer or
smaller-cap listings -- this is the second half of the "can we actually
add this coin" check, after the exchange-availability check.

Usage (on the droplet):
    cd /root/AI-Trading-Bot
    source venv/bin/activate
    python3 check_crypto_data_coverage.py
"""

from engines.market_data_engine import get_market_data

# Majors not yet tracked (current universe: BTC-USD, ETH-USD, SOL-USD,
# BNB-USD), plus the AI-category tokens flagged from Binance's AI markets
# page, all confirmed present in the testnet USDT pair list.
CANDIDATES = [
    "XRP-USD", "ADA-USD", "DOGE-USD", "AVAX-USD", "DOT-USD", "LINK-USD",
    "LTC-USD", "TRX-USD", "ATOM-USD", "UNI-USD", "NEAR-USD", "XLM-USD",
    "POL-USD",
    "FET-USD", "TAO-USD", "WLD-USD", "INJ-USD", "GRT-USD", "THETA-USD",
    "IO-USD", "LPT-USD", "RENDER-USD", "KAITO-USD", "VIRTUAL-USD",
]

print(f"Checking {len(CANDIDATES)} candidate tickers against get_market_data()...\n")

results = []
for ticker in CANDIDATES:
    try:
        df = get_market_data(ticker, period="5d", interval="1d")
        if df is not None and not df.empty:
            results.append((ticker, "OK", len(df)))
        else:
            results.append((ticker, "EMPTY", 0))
    except Exception as e:
        results.append((ticker, f"ERROR: {e}", 0))

ok = [r for r in results if r[1] == "OK"]
bad = [r for r in results if r[1] != "OK"]

print(f"{'TICKER':<15}{'STATUS':<10}ROWS")
for ticker, status, rows in results:
    print(f"{ticker:<15}{status:<10}{rows}")

print(f"\n{len(ok)}/{len(CANDIDATES)} have usable data.")
if bad:
    print("No usable data for:", ", ".join(t for t, _, _ in bad))
