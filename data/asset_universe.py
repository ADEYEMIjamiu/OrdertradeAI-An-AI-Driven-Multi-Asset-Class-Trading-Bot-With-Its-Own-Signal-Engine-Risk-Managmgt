ASSET_UNIVERSE = {
    "US_STOCKS": {
        "broker": "alpaca",
        "symbols": [
            # Market ETFs (foundation)
            "SPY", "QQQ", "DIA", "IWM",

            # Big Tech (AI + growth)
            "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",

            # Semiconductor / AI infrastructure
            "AMD", "INTC", "AVGO", "TSM",

            # High-growth / high-volatility
            "TSLA", "NFLX", "CRM", "ADBE",

            # Financials (macro signals)
            "JPM", "BAC", "GS",

            # Defensive stocks (stability)
            "KO", "PEP", "PG", "JNJ",

            # Energy (macro + inflation hedge)
            "XOM", "CVX"
        ],
        "enabled": True
    },

    "CRYPTO": {
        "broker": "binance",
        "symbols": ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD"],
        "enabled": True
    },

    "FOREX": {
        # "broker": "oanda" is aspirational -- there is no real OANDA
        # integration yet (that requires a demo account + identity
        # verification + a new broker module, see engines/risk_engine.py
        # and app.py comments near the forex execution calls). Until that
        # exists, forex trades are routed through the exact same local
        # paper-trading path as US_STOCKS (fake session-state cash and
        # positions, no real orders anywhere) so signal quality can be
        # validated on real forex price data without waiting on a broker.
        "broker": "oanda",
        "symbols": ["EURUSD=X", "GBPUSD=X", "USDJPY=X"],
        "enabled": True
    },

    "COMMODITIES": {
        # Same situation as FOREX above: "broker": "ibkr" is aspirational,
        # no real IBKR integration exists. Routed through the same local
        # paper-trading engine as US_STOCKS/FOREX until a real broker is
        # built. GC=F/CL=F/SI=F are standard yfinance front-month futures
        # tickers (gold, crude oil, silver) with real daily OHLC history.
        "broker": "ibkr",
        "symbols": ["GC=F", "CL=F", "SI=F"],
        "enabled": True
    }
}


def get_enabled_symbols():
    symbols = []

    for asset_class, config in ASSET_UNIVERSE.items():
        if config["enabled"]:
            for symbol in config["symbols"]:
                symbols.append({
                    "symbol": symbol,
                    "asset_class": asset_class,
                    "broker": config["broker"]
                })

    return symbols