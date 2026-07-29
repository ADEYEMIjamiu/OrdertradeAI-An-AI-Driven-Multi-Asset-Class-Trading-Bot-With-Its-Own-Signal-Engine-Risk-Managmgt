from datetime import datetime, timezone
import time

import pandas as pd
import yfinance as yf


# ============================================================
# MARKET DATA HEALTH STATE
# ============================================================

_market_data_health = {
    "status": "UNKNOWN",
    "last_success": None,
    "last_failure": None,
    "last_symbol": None,
    "last_error": None,
    "consecutive_failures": 0,
}


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_symbol(symbol: str) -> str:
    """
    Normalise a market-data symbol before sending it to the
    external data provider.
    """
    if symbol is None:
        raise ValueError("Market-data symbol cannot be None.")

    clean_symbol = str(symbol).strip().upper()

    if clean_symbol.startswith("$"):
        clean_symbol = clean_symbol[1:]

    if not clean_symbol:
        raise ValueError("Market-data symbol cannot be empty.")

    return clean_symbol


def _validate_market_data(
    df: pd.DataFrame,
    symbol: str,
) -> tuple[bool, str]:
    """
    Validate a returned market-data DataFrame before any trading
    engine is allowed to use it.
    """
    if df is None:
        return False, f"{symbol}: provider returned None."

    if not isinstance(df, pd.DataFrame):
        return False, f"{symbol}: provider returned an invalid data type."

    if df.empty:
        return False, f"{symbol}: provider returned an empty DataFrame."

    if "Close" not in df.columns:
        return False, f"{symbol}: Close column is missing."

    close_data = df["Close"]

    if isinstance(close_data, pd.DataFrame):
        if close_data.empty:
            return False, f"{symbol}: Close data is empty."

        close_data = close_data.iloc[:, 0]

    valid_close_count = close_data.dropna().shape[0]

    if valid_close_count < 2:
        return False, f"{symbol}: insufficient valid closing-price data."

    return True, "VALID"


def _record_success(symbol: str) -> None:
    _market_data_health["status"] = "HEALTHY"
    _market_data_health["last_success"] = _utc_timestamp()
    _market_data_health["last_symbol"] = symbol
    _market_data_health["last_error"] = None
    _market_data_health["consecutive_failures"] = 0


def _record_failure(
    symbol: str,
    error_message: str,
) -> None:
    _market_data_health["status"] = "WARNING"
    _market_data_health["last_failure"] = _utc_timestamp()
    _market_data_health["last_symbol"] = symbol
    _market_data_health["last_error"] = error_message
    _market_data_health["consecutive_failures"] += 1


# ============================================================
# PUBLIC MARKET DATA API
# ============================================================

def get_market_data(
    symbol: str,
    period: str | None = "6mo",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
    auto_adjust: bool = True,
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> pd.DataFrame:
    """
    Retrieve and validate market data.

    The engine retries transient failures and returns an empty
    DataFrame if reliable market data cannot be obtained.

    Trading engines must treat an empty DataFrame as DATA
    UNAVAILABLE and must not create a BUY or SELL decision from it.
    """
    clean_symbol = _normalise_symbol(symbol)

    last_error = (
        f"{clean_symbol}: market data unavailable."
    )

    for attempt in range(1, max_retries + 1):
        try:
            download_kwargs = {
                "tickers": clean_symbol,
                "interval": interval,
                "auto_adjust": auto_adjust,
                "progress": False,
                "threads": False,
            }

            if start is not None:
                download_kwargs["start"] = start

            if end is not None:
                download_kwargs["end"] = end

            if start is None and end is None and period is not None:
                download_kwargs["period"] = period

            df = yf.download(**download_kwargs)

            valid, validation_message = _validate_market_data(
                df,
                clean_symbol,
            )

            if valid:
                _record_success(clean_symbol)
                return df

            last_error = validation_message

        except Exception as exc:
            last_error = (
                f"{clean_symbol}: market-data request failed: {exc}"
            )

        if attempt < max_retries:
            time.sleep(retry_delay)

    _record_failure(
        clean_symbol,
        last_error,
    )

    return pd.DataFrame()


def get_market_data_health() -> dict:
    """
    Return a copy of the current market-data health state.
    """
    return dict(_market_data_health)