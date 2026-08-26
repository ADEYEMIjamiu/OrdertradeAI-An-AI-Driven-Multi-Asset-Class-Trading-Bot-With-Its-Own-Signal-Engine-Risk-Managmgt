from datetime import datetime, timezone
import threading
import time

import pandas as pd
import yfinance as yf


def _download_with_hard_deadline(download_kwargs: dict, hard_deadline_seconds: float = 20.0):
    """
    Runs yf.download() in a background thread and stops waiting on it
    after hard_deadline_seconds, regardless of what yfinance is doing
    internally.

    Discovered 2026-08-26: yf.download()'s own `timeout` kwarg only
    bounds a single HTTP call -- it does NOT bound Yahoo's rate-limit
    retry/backoff behaviour inside yfinance itself, which can run well
    past that. Confirmed live: one saas_scheduler.py tick took 6m45s
    wall-clock time against only 23.7s of actual CPU time (i.e. almost
    all of it spent waiting), while every other tick that run took under
    10 seconds total. The `timeout` kwarg passed in download_kwargs
    below is kept as a first line of defense, but this thread-based
    external deadline is what actually guarantees the caller never
    blocks longer than hard_deadline_seconds, independent of yfinance's
    internal behaviour.

    The worker thread is daemon=True and deliberately never joined
    without a timeout -- if yfinance really is stuck, this function
    returns control to the caller anyway and the orphaned thread is
    killed automatically when the process exits (this matters for
    saas_scheduler.py, a one-shot script that must be able to exit
    cleanly every tick even if a download never returns).
    """
    result: dict = {}

    def _worker():
        try:
            result["df"] = yf.download(**download_kwargs)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=hard_deadline_seconds)

    if thread.is_alive():
        raise TimeoutError(
            f"yf.download did not return within {hard_deadline_seconds}s "
            "(hard deadline hit -- Yahoo likely rate-limiting or stalled)."
        )

    if "error" in result:
        raise result["error"]

    return result.get("df")


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
    external data provider (yfinance).

    Share-class tickers are a real gotcha here: Alpaca's own API requires
    the dot notation (confirmed on Alpaca's forum -- "BRK.B" is correct
    for their data/trading API), but yfinance uses a hyphen instead
    ("BRK-B") -- passing "BRK.B" to yfinance returns nothing (empty/None
    across the board) rather than an error, which is exactly what showed
    up as an ERROR row for BRK.B on 2026-07-30 right after adding it.
    asset_universe.py's ticker list is the single source of truth used
    for BOTH the yfinance data fetch and the Alpaca order (see
    execute_alpaca_trades in app.py, which uses row["Ticker"] as-is) --
    so rather than keep two different ticker strings in sync everywhere,
    this converts dots to hyphens ONLY for the yfinance-facing call here.
    Alpaca execution elsewhere in the codebase still sees the original
    "BRK.B" from asset_universe.py, since this function is data-fetch-only.
    """
    if symbol is None:
        raise ValueError("Market-data symbol cannot be None.")

    clean_symbol = str(symbol).strip().upper()

    if clean_symbol.startswith("$"):
        clean_symbol = clean_symbol[1:]

    if not clean_symbol:
        raise ValueError("Market-data symbol cannot be empty.")

    # Share-class dot notation (BRK.B, BF.B, etc.) -- yfinance-specific,
    # does not affect forex (=X) or commodities (=F) tickers since those
    # never contain a dot.
    clean_symbol = clean_symbol.replace(".", "-")

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
                # yfinance already defaults this to 10s, but passed
                # explicitly as a first line of defense. NOTE: this alone
                # does NOT reliably bound a stalled call -- Yahoo's
                # rate-limit retry/backoff behaviour inside yfinance can
                # run well past this. The real bound is the external
                # thread-join hard deadline in _download_with_hard_deadline()
                # above -- see its docstring for the live incident this
                # was built to fix (2026-08-26, one saas_scheduler tick
                # took 6m45s wall-clock vs 23.7s CPU time despite this
                # default already being in place).
                "timeout": 15,
            }

            if start is not None:
                download_kwargs["start"] = start

            if end is not None:
                download_kwargs["end"] = end

            if start is None and end is None and period is not None:
                download_kwargs["period"] = period

            df = _download_with_hard_deadline(download_kwargs, hard_deadline_seconds=20.0)

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