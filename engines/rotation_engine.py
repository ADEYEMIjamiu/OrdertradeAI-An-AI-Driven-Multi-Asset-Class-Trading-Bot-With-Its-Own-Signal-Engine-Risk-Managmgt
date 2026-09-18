"""
engines/rotation_engine.py -- POSITION-CAP ROTATION (manual-approval-first)

Extracted out of app.py (post-launch item #118) with no logic changes --
every function below is a verbatim move from app.py's old
"POSITION-CAP ROTATION" section. See that extraction's task notes for why:
app.py had grown large enough that this self-contained decision-making
block (find a weak-vs-strong swap, then execute it) was worth pulling out
the same way signal_engine.py, performance_engine.py, and the rest of
engines/ already were.

Built 2026-08-06 at explicit user request, after repeatedly watching a
real, currently-strong BUY signal get skipped with "position limit
reached" while a much weaker position from earlier sat occupying that
asset class's slot. User-confirmed design (via AskUserQuestion):
  - Manual approval first -- this only ever SUGGESTS a swap on the
    dashboard with a Confirm button. Nothing here closes or opens a
    position on its own.
  - 20-point minimum Strategy Score gap between the candidate and the
    weakest held position before a swap is even suggested.
  - 24-hour cooldown -- a position isn't eligible to be rotated out
    until it's been held at least this long, so it has a real chance to
    work before being judged.

execute_alpaca_trades/execute_binance_trades/execute_etoro_trades still
live in app.py (extracting those is a separate, much larger task, out of
scope for #118) -- execute_rotation() below takes them as parameters
instead of importing them, both to avoid a circular import (app.py is
what imports this module) and because rotation only ever decides WHICH
two trades to submit; it never reimplements HOW to submit them, so
passing in "however app.py currently submits a trade" is the correct
shape either way.
"""

import streamlit as st
import pandas as pd
from datetime import datetime

from broker import get_open_positions, check_broker_connection
import binance_broker
import etoro_broker
from engines.order_manager import load_orders
from engines.asset_class_utils import filter_by_asset_class, _ETORO_ASSET_CLASS_TICKERS

ROTATION_MIN_SCORE_GAP = 20
ROTATION_COOLDOWN_HOURS = 24


def _get_position_opened_at(broker_name, ticker):
    """
    Best-effort lookup of when a currently-held position was opened, used
    only to enforce ROTATION_COOLDOWN_HOURS -- never relied on for
    anything trade-critical. None of the three brokers hand back a clean
    "position opened at" timestamp directly here (Alpaca's Position object
    doesn't carry one the way this code reads it, and the Binance/eToro
    positions used elsewhere in this file are plain dicts built fresh from
    their own APIs with no open-time field). This instead looks at the
    persistent order book (trade_journal.db, via engines.order_manager --
    the same store every BUY/SELL in this file already writes to) for the
    most recent FILLED BUY order matching this broker/ticker.

    Returns None if nothing is found, and callers treat "unknown" as "do
    not offer rotation for this position" rather than assuming it's safe
    to rotate out something whose open time can't actually be confirmed.
    """
    try:
        orders = load_orders(limit=500)
    except Exception:
        return None

    matches = [
        o for o in orders
        if o.get("broker") == broker_name
        and str(o.get("ticker", "")).upper().strip() == ticker.upper().strip()
        and o.get("side") == "BUY"
        and o.get("status") == "FILLED"
    ]
    if not matches:
        return None

    matches.sort(
        key=lambda o: o.get("filled_at") or o.get("updated_at") or o.get("created_at") or "",
        reverse=True,
    )
    timestamp_text = (
        matches[0].get("filled_at")
        or matches[0].get("updated_at")
        or matches[0].get("created_at")
    )
    if not timestamp_text:
        return None

    try:
        return datetime.fromisoformat(timestamp_text)
    except Exception:
        return None


def _get_held_positions_for_rotation(asset_class, etoro_positions_by_symbol):
    """
    Returns currently-held positions for one asset class in a common
    shape rotation logic can compare across all four asset classes:
        {"ticker": project ticker, "broker": broker name, "identifier":
         whatever the matching close_* call needs -- qty for
         Alpaca/Binance, position_id for eToro}.
    `etoro_positions_by_symbol` is passed in rather than fetched here so
    FOREX and COMMODITIES share a single eToro network call per rotation
    check instead of doing two.
    """
    held = []

    if asset_class == "US_STOCKS":
        try:
            for position in get_open_positions():
                held.append({
                    "ticker": str(position.symbol).upper().strip(),
                    "broker": "alpaca",
                    "identifier": float(position.qty),
                })
        except Exception:
            pass

    elif asset_class == "CRYPTO":
        try:
            for position in binance_broker.get_positions():
                held.append({
                    "ticker": str(position["symbol"]).upper().strip(),
                    "broker": "binance",
                    "identifier": float(position["qty"]),
                })
        except Exception:
            pass

    elif asset_class in ("FOREX", "COMMODITIES", "INDICES"):
        for project_ticker in _ETORO_ASSET_CLASS_TICKERS.get(asset_class, []):
            etoro_symbol = etoro_broker.resolve_project_ticker(project_ticker)
            position = etoro_positions_by_symbol.get(etoro_symbol)
            if position is not None:
                held.append({
                    "ticker": project_ticker,
                    "broker": "etoro",
                    "identifier": position["position_id"],
                })

    return held


def find_rotation_candidates(market_df, buy_signals):
    """
    For each asset class, compare the CURRENT Strategy Score of the
    weakest currently-held position against the CURRENT Strategy Score of
    the strongest not-yet-held approved BUY candidate in the same asset
    class. Both scores are read fresh from THIS pass's market_df -- not
    whatever a position happened to score when it was originally bought
    -- since a position that scored well a week ago can easily be
    outscored by conditions today. Returns a list of suggestion dicts;
    see the module comment above this function for the full design.
    """
    candidates = []

    if buy_signals is None or buy_signals.empty:
        return candidates
    if "Strategy Score" not in market_df.columns or "Strategy Score" not in buy_signals.columns:
        return candidates

    try:
        etoro_positions_by_symbol = {p["symbol"]: p for p in etoro_broker.get_positions()}
    except Exception:
        etoro_positions_by_symbol = {}

    for asset_class in ["US_STOCKS", "CRYPTO", "FOREX", "COMMODITIES", "INDICES"]:
        held = _get_held_positions_for_rotation(asset_class, etoro_positions_by_symbol)
        if not held:
            continue

        class_buy_signals = filter_by_asset_class(buy_signals, asset_class)
        if class_buy_signals is None or class_buy_signals.empty:
            continue

        held_tickers = {h["ticker"] for h in held}
        open_candidates = class_buy_signals[
            ~class_buy_signals["Ticker"].astype(str).str.upper().str.strip().isin(held_tickers)
        ]
        if open_candidates.empty:
            continue

        best_candidate_row = open_candidates.loc[open_candidates["Strategy Score"].idxmax()]

        scored_held = []
        for position in held:
            match = market_df.loc[
                market_df["Ticker"].astype(str).str.upper().str.strip() == position["ticker"]
            ]
            if match.empty:
                continue
            scored_held.append({**position, "score": float(match.iloc[0]["Strategy Score"])})

        if not scored_held:
            continue

        weakest = min(scored_held, key=lambda p: p["score"])
        candidate_score = float(best_candidate_row["Strategy Score"])
        gap = candidate_score - weakest["score"]

        if gap < ROTATION_MIN_SCORE_GAP:
            continue

        opened_at = _get_position_opened_at(weakest["broker"], weakest["ticker"])
        if opened_at is None:
            # Can't confirm how long it's been held -- don't guess.
            continue

        hours_held = (datetime.now() - opened_at).total_seconds() / 3600
        if hours_held < ROTATION_COOLDOWN_HOURS:
            continue

        candidates.append({
            "asset_class": asset_class,
            "weak_ticker": weakest["ticker"],
            "weak_broker": weakest["broker"],
            "weak_score": weakest["score"],
            "hours_held": hours_held,
            "candidate_ticker": str(best_candidate_row["Ticker"]).upper().strip(),
            "candidate_score": candidate_score,
            "candidate_row": best_candidate_row,
            "gap": gap,
        })

    return candidates


def _rotation_position_still_open(asset_class, ticker):
    """
    Re-check directly with the broker whether a position is still open,
    used by execute_rotation() between its close and open legs.

    2026-08-08: execute_rotation() fired both legs unconditionally --
    close the weak position, then open the replacement -- with nothing
    checking that the close actually worked in between. For Alpaca this
    happened to be caught accidentally (a queued-but-unfilled sell order
    trips the broker health WARNING gate, which then blocks the buy), but
    that protection is Alpaca-specific: get_broker_state_health() only
    looks at Alpaca positions/orders. eToro has no equivalent gate, so if
    an eToro close ever failed or didn't confirm (market closed, timeout,
    anything), the buy leg would still have fired right after it --
    opening a new leveraged position without ever having closed the old
    one, and quietly breaching the asset class's position cap in the
    process. This checks reality directly with the broker instead of
    trusting that "no exception was raised" means "the position is
    actually gone" -- covering all three brokers the same way, and
    failing safe (treats the position as still open, so it blocks the
    buy) if the broker can't even be reached to check.
    """
    ticker = str(ticker).upper().strip()

    try:
        if asset_class == "US_STOCKS":
            for position in get_open_positions():
                if str(position.symbol).upper().strip() == ticker:
                    return True
            return False

        elif asset_class == "CRYPTO":
            for position in binance_broker.get_positions():
                if str(position["symbol"]).upper().strip() == ticker:
                    return True
            return False

        else:
            return etoro_broker.find_position_by_symbol(ticker) is not None

    except Exception:
        # Broker unreachable -- can't confirm the close actually
        # happened, so fail safe and assume it's still open rather than
        # risk opening a second position on top of an unconfirmed close.
        return True


def execute_rotation(
    candidate,
    market_df,
    execute_alpaca_trades,
    execute_binance_trades,
    execute_etoro_trades,
):
    """
    Closes the weak position and opens the suggested replacement, by
    reusing the exact same execute_alpaca_trades/execute_binance_trades/
    execute_etoro_trades functions every other trade in app.py already
    goes through -- same risk checks, same order-journal logging, same
    Telegram notifications, same error handling. Rotation only decides
    WHICH two trades to submit; it never reimplements HOW to submit them.
    Called only from the "Confirm Rotation" button in app.py -- per the
    user's explicit "manual approval first" choice, nothing upstream of
    that click can trigger this.

    execute_alpaca_trades/execute_binance_trades/execute_etoro_trades are
    passed in by the caller (app.py) rather than imported here, since
    those three functions still live in app.py itself and app.py is what
    imports this module -- importing them back would be circular. See
    this module's docstring for the full reasoning.
    """
    asset_class = candidate["asset_class"]

    # 2026-08-08: A live rotation fired on a Saturday. The SELL leg was
    # accepted by Alpaca but queued (US market closed, can't fill until
    # next open) rather than executing immediately. Because that queued
    # order and the still-open position existed at the same time, the
    # broker health check (see get_broker_state_health -- it treats an
    # open position with an active order on it as a conflict) flipped to
    # WARNING, which silently blocked the BUY leg from ever being
    # attempted -- leaving the swap half-done with no clear explanation.
    # Rotation assumes the SELL clears before the BUY fires in the same
    # pass, so for US_STOCKS it only makes sense while the market is
    # actually open. Gate it here instead of letting it fail confusingly
    # downstream.
    if asset_class == "US_STOCKS":
        try:
            stock_broker_health = check_broker_connection()
            market_is_open = bool(stock_broker_health.get("market_open", False))
        except Exception:
            stock_broker_health = {}
            market_is_open = False

        if not market_is_open:
            next_open = stock_broker_health.get("next_market_open", "the next session")
            st.session_state.trade_messages.append(
                f"🔄 Rotation not attempted for {candidate['weak_ticker']} -> "
                f"{candidate['candidate_ticker']}: the US stock market is "
                f"closed. Selling now would only queue the order, and the "
                f"buy leg would then be blocked by the broker's own "
                f"position/order safety check -- so nothing was submitted "
                f"to avoid a half-completed swap. Next market open: "
                f"{next_open}. Try again once the market reopens."
            )
            return

    weak_row = market_df.loc[
        market_df["Ticker"].astype(str).str.upper().str.strip() == candidate["weak_ticker"]
    ].copy()
    if weak_row.empty:
        st.session_state.trade_messages.append(
            f"Rotation failed: could not find current market data for "
            f"{candidate['weak_ticker']} to close it."
        )
        return
    weak_row["Signal"] = "SELL"

    candidate_row_df = pd.DataFrame([candidate["candidate_row"]])
    empty_df = pd.DataFrame()

    # Close leg only, for now -- the open leg is gated below on actually
    # confirming this worked, not just on it not having raised.
    if asset_class == "US_STOCKS":
        execute_alpaca_trades(empty_df, weak_row)
    elif asset_class == "CRYPTO":
        execute_binance_trades(empty_df, weak_row)
    else:
        execute_etoro_trades(empty_df, weak_row)

    if _rotation_position_still_open(asset_class, candidate["weak_ticker"]):
        st.session_state.trade_messages.append(
            f"🔄 Rotation stopped after attempting to close "
            f"{candidate['weak_ticker']}: it still shows as an open "
            f"position with the broker, so {candidate['candidate_ticker']} "
            f"was NOT opened -- avoiding a double position on top of an "
            f"unconfirmed close. Check the broker/Order Book for why the "
            f"close didn't complete; if it's just delayed, try the "
            f"rotation again once it clears."
        )
        return

    if asset_class == "US_STOCKS":
        execute_alpaca_trades(candidate_row_df, empty_df)
    elif asset_class == "CRYPTO":
        execute_binance_trades(candidate_row_df, empty_df)
    else:
        execute_etoro_trades(candidate_row_df, empty_df)

    # 2026-08-11: This final message used to fire unconditionally right
    # after the buy leg was attempted, regardless of whether it actually
    # succeeded. A live rotation showed exactly that gap -- AAPL closed
    # cleanly, but the WMT buy was skipped by the daily trade limit
    # (a real, separate safety gate, working as intended), and yet this
    # message still declared "Rotation executed: closed AAPL... to open
    # WMT..." -- claiming success on a swap that was only half done. Same
    # principle as the close-leg check above: confirm reality with the
    # broker before declaring success, instead of trusting that "no
    # exception was raised" means the buy went through.
    if _rotation_position_still_open(asset_class, candidate["candidate_ticker"]):
        st.session_state.trade_messages.append(
            f"🔄 Rotation executed: closed {candidate['weak_ticker']} "
            f"(score {candidate['weak_score']:.1f}, held {candidate['hours_held']:.1f}h) "
            f"to open {candidate['candidate_ticker']} (score {candidate['candidate_score']:.1f})."
        )
    else:
        st.session_state.trade_messages.append(
            f"⚠️ Rotation partially completed: {candidate['weak_ticker']} "
            f"was closed, but {candidate['candidate_ticker']} was NOT "
            f"opened -- see the buy message above for why (e.g. daily "
            f"trade limit, insufficient cash, broker rejection). You're "
            f"now holding cash instead of {candidate['candidate_ticker']}; "
            f"try the rotation again once the blocking condition clears, "
            f"or place that buy manually."
        )
