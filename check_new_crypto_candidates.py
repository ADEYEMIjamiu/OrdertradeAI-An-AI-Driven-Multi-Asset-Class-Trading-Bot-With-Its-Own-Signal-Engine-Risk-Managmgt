"""
check_new_crypto_candidates.py -- one-off, 2026-08-23.

User flagged that several coins with real volume/momentum right now
(ZEC, PYTH, TRUMP, UNI, AAVE, BCH -- pulled from Binance's own top-50
by 24h volume + CoinMarketCap's today gainers list) aren't in
ASSET_UNIVERSE at all. Before adding any of them, mirrors the same
two-step verification used for the 2026-08-22 crypto expansion
(see data/asset_universe.py's own comment):

  1. Tradable on Binance TESTNET specifically (not just mainnet --
     testnet's pair list is a smaller subset).
  2. Has usable price history via yfinance's "-USD" ticker (the AI
     model's technical indicators need this; Binance testnet only
     keeps ~18 days of candles, nowhere near enough on its own).

Prints a clear PASS/FAIL per candidate so nothing gets added on a
guess.
"""

from binance_broker import exchange  # existing authenticated ccxt testnet client
from engines.market_data_engine import get_market_data  # same yfinance wrapper the AI model uses

CANDIDATES = {
    "ZEC": "ZEC-USD",
    "PYTH": "PYTH-USD",
    "TRUMP": "TRUMP-USD",
    "UNI": "UNI-USD",
    "AAVE": "AAVE-USD",
    "BCH": "BCH-USD",
}

print("=" * 70)
print("STEP 1: Binance TESTNET pair availability")
print("=" * 70)
try:
    testnet_markets = exchange.load_markets()
except Exception as e:
    print(f"Could not fetch testnet markets: {e}")
    testnet_markets = {}

binance_ok = {}
for base, yf_ticker in CANDIDATES.items():
    pair = f"{base}/USDT"
    ok = pair in testnet_markets
    binance_ok[base] = ok
    print(f"  {pair:<12} {'FOUND on testnet' if ok else 'NOT on testnet'}")

print("\n" + "=" * 70)
print("STEP 2: yfinance price history availability")
print("=" * 70)
yfinance_ok = {}
for base, yf_ticker in CANDIDATES.items():
    try:
        df = get_market_data(yf_ticker, period="3mo")
        has_data = df is not None and not df.empty and len(df) >= 50
        yfinance_ok[base] = has_data
        rows = len(df) if df is not None else 0
        print(f"  {yf_ticker:<12} {'OK -- ' + str(rows) + ' rows' if has_data else 'INSUFFICIENT/NO DATA (' + str(rows) + ' rows)'}")
    except Exception as e:
        yfinance_ok[base] = False
        print(f"  {yf_ticker:<12} ERROR -- {e}")

print("\n" + "=" * 70)
print("SUMMARY -- safe to add to ASSET_UNIVERSE")
print("=" * 70)
for base in CANDIDATES:
    passed = binance_ok.get(base) and yfinance_ok.get(base)
    print(f"  {base:<8} {'PASS -- both checks OK' if passed else 'FAIL -- do not add yet'}")
