"""
Binance-native market data for crypto -- built 2026-08-22 as the first
piece of the crypto scalping engine (roadmap item #9), per explicit user
request to make crypto's AI decisions react within ~60 seconds instead of
the yfinance-backed refresh the rest of the dashboard uses (5 minutes,
often more stale than that for crypto specifically).

IMPORTANT DESIGN NOTE -- daily-bar SHAPE, live-updating VALUES:
The trained model (models/trading_model.pkl) was fit on DAILY indicators
-- SMA20/SMA50 are 20/50-DAY moving averages, RSI-14 is a 14-DAY
oscillator, etc. (see train_model.py's FEATURES list). If this module
fetched 1-minute bars and computed "SMA50" with the same window=50, that
would silently become a 50-*minute* average instead -- a completely
different quantity the model was never calibrated on, which would make
its predictions meaningless without anyone noticing (the code would run
fine, it would just be wrong).

So this fetches DAILY-resolution OHLCV from Binance (timeframe="1d"),
matching the model's actual training scale -- but Binance includes the
current, still-forming day's candle as the last row, which keeps
updating live as trades happen on the exchange. Re-fetching every ~60
seconds (see crypto_scalping_engine.py) means "today"'s close, and
therefore SMA/RSI/MACD/Returns/Volatility, reflect the live price
continuously -- without changing what those indicators actually MEAN to
the model. This is the same principle as the position-lifecycle and
position-count fixes earlier this session: reuse an already-correct
calculation, just feed it fresher input.

Deliberately separate from engines/market_data_engine.py (the existing
yfinance path) rather than replacing it -- per explicit user instruction,
this runs ALONGSIDE yfinance, not instead of it. Stocks/forex/commodities
are completely untouched by this module.
"""

import pandas as pd

from binance_broker import exchange, _to_binance_symbol


def get_binance_market_data(ticker: str, limit: int = 100) -> pd.DataFrame:
    """
    Daily-resolution OHLCV for a crypto ticker (e.g. "SOL-USD"), sourced
    directly from Binance testnet via the same ccxt connection
    binance_broker.py already uses for execution -- so the price this
    function reports and the price a trade would actually fill at come
    from the same place, unlike the yfinance/Binance split that existed
    for the rest of the pipeline.

    Returns a DataFrame shaped like engines/market_data_engine.py's
    get_market_data() (columns: Open, High, Low, Close, Volume; indexed
    by timestamp) so the exact same feature-computation code (ta.trend.
    sma_indicator, ta.momentum.rsi, etc. -- see app.py's prepare_data())
    can run on either source unchanged.

    Raises on failure rather than returning an empty frame -- callers
    (crypto_scalping_engine.py) are expected to catch this and fall back
    to engines.market_data_engine.get_market_data() for that ticker/cycle,
    per the "run alongside, never block on Binance alone" requirement.
    """
    symbol = _to_binance_symbol(ticker)

    ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1d", limit=limit)

    if not ohlcv:
        raise ValueError(f"Binance returned no OHLCV data for {symbol}")

    df = pd.DataFrame(
        ohlcv, columns=["timestamp", "Open", "High", "Low", "Close", "Volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("timestamp")

    return df
