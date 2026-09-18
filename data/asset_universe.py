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
            "XOM", "CVX",

            # Berkshire-style value/holding companies -- added 2026-07-30
            # at user request. BRK.B uses Alpaca's dot notation for share
            # classes; if the first BUY signal for it ever fails/errors,
            # the likely fix is switching to "BRK/B" instead -- flag it
            # if that happens rather than assuming the ticker is dead.
            "BRK.B", "BH",

            # Additional blue-chip large caps -- broadens beyond the
            # existing tech/semis/financials/defensive/energy mix with
            # payments, retail, healthcare, and enterprise software.
            "V", "MA", "WMT", "COST", "UNH", "LLY", "HD", "ORCL"
        ],
        "enabled": True
    },

    "CRYPTO": {
        "broker": "binance",
        # Expanded 2026-08-22 at user request from the original 4
        # (BTC/ETH/SOL/BNB) -- Binance testnet actually lists 489 USDT
        # pairs (confirmed live via check_binance_testnet_pairs.py), far
        # more than this project ever traded. Each new symbol below was
        # verified two ways before being added: (1) tradable on Binance
        # TESTNET specifically, not just mainnet, and (2) has usable
        # price history via get_market_data() (yfinance) for the AI
        # model's technical indicators -- both checked live via
        # check_crypto_data_coverage.py. A few Binance-tradable coins
        # (UNI, POL, TAO, GRT, IO) were left out for now because yfinance
        # had no data under their plain "-USD" ticker, most likely due to
        # ticker-collision suffixes on Yahoo's side -- worth revisiting
        # individually later, not blocking this expansion.
        "symbols": [
            "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD",

            # Established majors
            "XRP-USD", "ADA-USD", "DOGE-USD", "AVAX-USD", "DOT-USD",
            "LINK-USD", "LTC-USD", "TRX-USD", "ATOM-USD", "NEAR-USD",
            "XLM-USD",

            # AI-category tokens -- flagged directly by the user from
            # Binance's own AI markets page (binance.com/markets/coinInfo-AI)
            "FET-USD", "WLD-USD", "INJ-USD", "THETA-USD", "LPT-USD",
            "RENDER-USD", "KAITO-USD", "VIRTUAL-USD",

            # Added 2026-08-23 at user request, after noticing several
            # coins with real live volume/momentum weren't tracked at
            # all (ZEC/PYTH/AAVE/BCH each independently confirmed via
            # CoinMarketCap's top-gainers list and/or Binance's own
            # top-50-by-volume ranking). Verified the same two ways as
            # every other symbol here via check_new_crypto_candidates.py:
            # tradable on Binance TESTNET, and has usable yfinance
            # history. TRUMP-USD and UNI-USD were considered from the
            # same shortlist but failed the yfinance check (no data
            # under the plain "-USD" ticker, same collision issue as
            # UNI/POL/TAO/GRT/IO above) -- left out for now.
            "ZEC-USD", "PYTH-USD", "AAVE-USD", "BCH-USD",
        ],
        "enabled": True
    },

    "FOREX": {
        # UPDATED 2026-08-06: this used to say "broker": "oanda" as an
        # aspirational placeholder, back when no real forex broker existed
        # and these traded through the same fake local paper-trading path
        # as US_STOCKS. That's no longer true -- etoro_broker.py is a real,
        # live-tested integration (see that file's own docstring history)
        # and execute_etoro_trades() in app.py has been routing every real
        # FOREX BUY/SELL through eToro's Demo environment for a while now.
        # Left as "oanda" for days after eToro actually went live, which is
        # exactly why the AI Decision Engine table was showing "oanda" in
        # its Broker column for real eToro trades -- purely a stale label,
        # never affected where orders actually went.
        "broker": "etoro",
        "symbols": ["EURUSD=X", "GBPUSD=X", "USDJPY=X"],
        "enabled": True
    },

    "COMMODITIES": {
        # Same fix as FOREX above, same reason: "ibkr" was an aspirational
        # placeholder from before eToro existed in this project. Real
        # COMMODITIES trades have been going through etoro_broker.py via
        # execute_etoro_trades() in app.py for a while now (GC=F/CL=F/SI=F
        # resolve to eToro's GOLD/OIL/SILVER instruments -- see
        # etoro_broker.py's _PROJECT_TICKER_OVERRIDES). Updating this label
        # to match reality.
        "broker": "etoro",
        "symbols": ["GC=F", "CL=F", "SI=F"],
        "enabled": True
    },

    "INDICES": {
        # Added as a 5th asset class (task #390) alongside FOREX/
        # COMMODITIES -- same eToro/MT4-5 CFD path, same "broker": "etoro"
        # placeholder label (the SaaS decision engine actually resolves
        # the real per-user broker between ETORO/MT_BRIDGE at runtime;
        # see saas_decision_engine._resolve_broker_for_asset_class() --
        # this key is only read directly by the single-owner bot's own
        # app.py/rotation_engine.py paths).
        #
        # Tickers are yfinance's real index symbols (^-prefixed, not a
        # suffix like FOREX's "=X" or COMMODITIES' "=F" -- see the new
        # _is_index_ticker() helper added alongside this). Six major
        # indices chosen to mirror the six symbols each FOREX/COMMODITIES
        # started with: US (S&P 500, Nasdaq 100, Dow), UK, Germany, Japan.
        #
        # NOT YET VERIFIED LIVE: unlike GC=F/CL=F/SI=F (confirmed against
        # eToro's real catalog before shipping), these six have NOT been
        # checked against eToro's or any MT4/5 broker's actual live
        # instrument list yet -- see etoro_broker._PROJECT_TICKER_OVERRIDES
        # and mt_broker._MT_TICKER_OVERRIDES for the mapped names and their
        # own "needs live verification" warnings. Do not enable real-money
        # INDICES trading until that verification pass is done.
        "broker": "etoro",
        "symbols": ["^GSPC", "^IXIC", "^DJI", "^FTSE", "^GDAXI", "^N225"],
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