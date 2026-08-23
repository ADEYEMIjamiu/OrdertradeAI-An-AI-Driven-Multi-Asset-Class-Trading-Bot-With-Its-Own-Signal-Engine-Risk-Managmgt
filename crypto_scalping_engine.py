"""
crypto_scalping_engine.py -- standalone, 60-second-cadence crypto
decision loop. First phase of roadmap item #9, built 2026-08-22 per
explicit user request for crypto to react to the market within roughly
a minute instead of the dashboard's shared 5-minute refresh (which is
also often further delayed for crypto specifically, since it reads
yfinance rather than Binance directly).

SHADOW MODE ONLY -- READS LIVE DATA, PLACES NO ORDERS.
This computes and logs (to crypto_scalper_shadow.db, via
engines/scalper_shadow_log.py) what the AI would decide for every
tracked crypto ticker every 60 seconds, using live Binance-native price
data with a yfinance fallback if Binance is unreachable for a given
ticker/cycle. It never calls binance_broker.buy_crypto()/sell_crypto(),
never writes to trade_journal.db or orders.db, and cannot place a real
order in any way. This is deliberate: the agreed plan (see chat history
2026-08-22) was to run this in shadow mode for a trial period first --
watch what it would have done, compare it against the existing loop,
and only wire it up to real execution once that looks sane. Flipping it
to live trading is a separate, later change, not something this file
does on its own.

WHY A SEPARATE PROCESS, NOT PART OF app.py:
Streamlit's execution model reruns the whole script on a timer/user
interaction; there's no clean way to get one part of it (crypto) running
on its own independent 60-second clock without either dragging the
entire dashboard down to that refresh rate (wasteful -- stocks/forex
don't need it) or running a background thread inside the Streamlit
process (real concurrency/state-sharing risk). A separate process avoids
both: it runs on its own systemd service (see the bottom of this file),
reads the same trained model and config the dashboard uses, and writes
only to its own database.

DATA SOURCE (revised 2026-08-22): see evaluate_ticker()'s docstring --
live testing showed Binance testnet only retains ~18 days of daily
candles, too shallow for SMA50, so this uses yfinance's 6-month history
as the backbone and splices in Binance's live last-traded price (via
fetch_ticker, no history needed) onto the most recent bar for freshness.

DATA SOURCE SIMPLIFICATION vs. the dashboard's get_ai_signal():
This uses the model's raw probability confidence only -- it does NOT
also blend in the multi-timeframe score app.py's get_ai_signal() adds
(get_multi_timeframe_signal(), which itself makes 3 extra yfinance calls
per ticker per evaluation). Doing that every 60 seconds for 23 tickers
would multiply the yfinance load significantly for a signal component
this shadow trial isn't primarily testing -- the thing being validated
here is the Binance-native price freshness, not the multi-timeframe
blend. Worth revisiting once/if this graduates out of shadow mode.

Usage:
    python3 crypto_scalping_engine.py
"""

import time
import traceback
from datetime import datetime

import joblib
import pandas as pd

from config import BUY_CONFIDENCE, SELL_CONFIDENCE
from data.asset_universe import ASSET_UNIVERSE
from binance_broker import get_current_price as get_binance_live_price
from engines.market_data_engine import get_market_data
from engines.feature_engine import compute_technical_features
from engines.scalper_shadow_log import log_decision

MODEL_PATH = "models/trading_model.pkl"
FEATURES_PATH = "models/features.pkl"

LOOP_INTERVAL_SECONDS = 60


def get_crypto_tickers():
    return ASSET_UNIVERSE["CRYPTO"]["symbols"]


def evaluate_ticker(ticker, model, features):
    """
    Returns (price, confidence, signal, data_source) for one ticker.

    DATA SOURCE STRATEGY (revised 2026-08-22 after live testing):
    The original design tried to fetch 100 days of daily OHLCV directly
    from Binance testnet, using yfinance only as a fallback. Live testing
    showed Binance testnet only retains ~18 days of daily candles for
    EVERY ticker (testnets get periodically wiped, unlike real Binance),
    which is short of the 50 days SMA50 needs -- so that path failed
    100% of the time for all 23 tickers, every cycle.

    This still delivers the actual goal (daily-bar SHAPE, live-updating
    VALUES) via a different split: yfinance supplies the deep historical
    backbone (6 months, proven reliable), and Binance supplies only the
    single freshest data point -- the current live last-traded price,
    via a lightweight fetch_ticker() call that needs no history at all.
    That live price is spliced onto the most recent bar before indicators
    are computed, so SMA/RSI/MACD/etc. reflect the live Binance price
    while still being calculated over yfinance's real history.

    Never raises -- returns None on total failure so one bad ticker
    can't take down the whole cycle.
    """
    try:
        df = get_market_data(ticker, period="6mo", interval="1d")
        if df is None or df.empty:
            print(f"[crypto_scalping_engine] {ticker}: yfinance returned no data, skipping")
            return None
    except Exception as yfinance_error:
        print(f"[crypto_scalping_engine] {ticker}: yfinance fetch failed ({yfinance_error}), skipping")
        return None

    # yfinance sometimes returns MultiIndex columns (e.g. ("Close", "BTC-USD"))
    # even for a single ticker -- flatten BEFORE the splice below, otherwise
    # df.loc[last_index, "High"] matches multiple columns and returns a
    # Series instead of a scalar, breaking the max()/min() comparisons.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    data_source = "yfinance_only"
    try:
        live_price = get_binance_live_price(ticker)
        last_index = df.index[-1]
        # Splice the live Binance price onto the most recent bar's Close,
        # widening High/Low if the live price has moved beyond them --
        # this is the "live-updating VALUES" half of the design.
        df.loc[last_index, "Close"] = live_price
        df.loc[last_index, "High"] = max(df.loc[last_index, "High"], live_price)
        df.loc[last_index, "Low"] = min(df.loc[last_index, "Low"], live_price)
        data_source = "binance_live_price"
    except Exception as binance_error:
        print(
            f"[crypto_scalping_engine] {ticker}: Binance live price fetch failed "
            f"({type(binance_error).__name__}: {binance_error}), using yfinance close as-is"
        )

    try:
        df = compute_technical_features(df)
    except Exception as feature_error:
        print(f"[crypto_scalping_engine] {ticker}: feature computation failed ({feature_error}), skipping")
        return None

    latest = df.iloc[-1]
    X_live = df[features].tail(1)

    probability_up = model.predict_proba(X_live)[0][1]
    confidence = round(probability_up * 100, 2)
    price = round(float(latest["Close"]), 6)

    if confidence >= BUY_CONFIDENCE * 100:
        signal = "BUY"
    elif confidence <= SELL_CONFIDENCE * 100:
        signal = "SELL"
    else:
        signal = "HOLD"

    return price, confidence, signal, data_source


def run_loop():
    print(
        f"[crypto_scalping_engine] starting -- {LOOP_INTERVAL_SECONDS}s cadence, "
        f"SHADOW MODE (logging only, no orders will ever be placed by this script)"
    )

    model = joblib.load(MODEL_PATH)
    features = joblib.load(FEATURES_PATH)
    tickers = get_crypto_tickers()

    print(f"[crypto_scalping_engine] tracking {len(tickers)} crypto tickers: {tickers}")

    while True:
        cycle_start = time.time()
        buy_count = 0
        sell_count = 0
        hold_count = 0
        binance_ok = 0
        fallback_used = 0

        for ticker in tickers:
            try:
                result = evaluate_ticker(ticker, model, features)
                if result is None:
                    continue
                price, confidence, signal, data_source = result
                log_decision(ticker, price, confidence, signal, data_source)

                if data_source == "binance_live_price":
                    binance_ok += 1
                else:
                    fallback_used += 1

                if signal == "BUY":
                    buy_count += 1
                elif signal == "SELL":
                    sell_count += 1
                else:
                    hold_count += 1
            except Exception:
                print(f"[crypto_scalping_engine] unexpected error evaluating {ticker}:")
                traceback.print_exc()

        elapsed = time.time() - cycle_start
        print(
            f"[crypto_scalping_engine] {datetime.now().isoformat(timespec='seconds')} "
            f"cycle done in {elapsed:.1f}s -- {buy_count} BUY / {sell_count} SELL / "
            f"{hold_count} HOLD -- {binance_ok} via Binance, {fallback_used} via "
            f"yfinance fallback -- shadow only, no orders placed"
        )

        sleep_time = max(0, LOOP_INTERVAL_SECONDS - elapsed)
        time.sleep(sleep_time)


if __name__ == "__main__":
    run_loop()


# ============================================================
# DEPLOYMENT -- runs as its OWN systemd service, alongside (not instead
# of) ordertrade-ai.service. On the droplet:
#
# 1. sudo nano /etc/systemd/system/ordertrade-crypto-scalper.service
#    Paste:
#
#    [Unit]
#    Description=OrderTrade AI crypto scalping engine (shadow mode)
#    After=network.target
#
#    [Service]
#    Type=simple
#    WorkingDirectory=/root/AI-Trading-Bot
#    ExecStart=/root/AI-Trading-Bot/venv/bin/python3 /root/AI-Trading-Bot/crypto_scalping_engine.py
#    Restart=always
#    RestartSec=10
#
#    [Install]
#    WantedBy=multi-user.target
#
# 2. sudo systemctl daemon-reload
# 3. sudo systemctl enable ordertrade-crypto-scalper
# 4. sudo systemctl start ordertrade-crypto-scalper
# 5. sudo systemctl status ordertrade-crypto-scalper
# 6. journalctl -u ordertrade-crypto-scalper -f   (watch it live)
# ============================================================
