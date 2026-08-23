"""
One-off diagnostic: lists every USDT spot trading pair actually available
on Binance TESTNET (not mainnet -- testnet usually supports far fewer
pairs than the live exchange). Run this on the droplet, where the real
BINANCE_TESTNET_API_KEY/SECRET_KEY env vars and network access already
work, to find out which of the user's requested coins can actually be
added to ASSET_UNIVERSE["CRYPTO"]["symbols"] in data/asset_universe.py.

Usage (on the droplet):
    cd /root/AI-Trading-Bot
    source venv/bin/activate
    python3 check_binance_testnet_pairs.py
"""

from binance_broker import exchange

markets = exchange.load_markets()

usdt_spot_pairs = sorted(
    symbol for symbol, market in markets.items()
    if symbol.endswith("/USDT") and market.get("spot")
)

print(f"Total USDT spot pairs available on Binance TESTNET: {len(usdt_spot_pairs)}\n")
for pair in usdt_spot_pairs:
    print(pair)
