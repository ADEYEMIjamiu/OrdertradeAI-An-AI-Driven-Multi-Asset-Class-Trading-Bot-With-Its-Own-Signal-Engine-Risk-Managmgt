"""
Position Lifecycle Engine -- decides whether an open position should have
its protective stop moved to break-even, have part of it closed to bank
real profit, or be force-closed after sitting open too long without
resolving naturally via stop-loss/take-profit/trailing-stop.

Built 2026-08-22 per explicit user request: the bot's core purpose is to
make profit, not just avoid a loss over a long, exposed hold -- a
position that reaches meaningful profit and then gives it all back on
the way to a still-unreached TAKE_PROFIT target has protected nothing.
Traced live on the eToro USDJPY position that prompted this: bought the
8th, only +0.83% of the way to its +5% target two weeks later, sitting
completely exposed to a full reversal the whole time. This adds three
layers on top of the existing stop-loss/take-profit/trailing-stop:

1. BREAK-EVEN STOP: once a position moves BREAKEVEN_STOP_TRIGGER_PERCENT
   into profit, the effective stop floor becomes entry price (0%)
   instead of the original stop-loss level -- the position can no
   longer close for a net loss from that point on.
2. PARTIAL PROFIT-TAKING: once a position reaches
   PARTIAL_PROFIT_TRIGGER_PERCENT, PARTIAL_PROFIT_TAKE_FRACTION of it is
   closed to bank real, realized profit, rather than leaving the whole
   position exposed all the way to the full take-profit target.
3. TIME-BASED EXIT: if a position has been open longer than
   MAX_HOLD_DAYS and is at or above break-even, it's closed outright
   rather than left to ride indefinitely. Never forces an early exit
   while still at a loss -- the stop-loss continues to protect it
   exactly as before; this only forces a RESOLUTION once the position
   is already flat-or-better and time has run out on reaching the full
   target naturally.

Deliberately pure decision logic plus its own tiny persisted state (which
tickers have already had break-even activated / partial profit taken,
and when each position was first seen) -- no broker calls here. Callers
(app.py's apply_risk_management/apply_crypto_risk_management) own the
actual SELL execution and journal writes, matching the engines-decide/
app.py-executes separation used everywhere else in this project.
"""

import json
from datetime import datetime

from config import (
    BREAKEVEN_STOP_TRIGGER_PERCENT,
    PARTIAL_PROFIT_TRIGGER_PERCENT,
    PARTIAL_PROFIT_TAKE_FRACTION,
    MAX_HOLD_DAYS,
)

_STATE_FILE = "position_lifecycle_state.json"

_DEFAULT_ENTRY = {
    "breakeven_active": False,
    "partial_taken": False,
    "opened_at": None,  # filled in on first sight, see get_position_state()
}


def _load_state():
    try:
        with open(_STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state):
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[position_lifecycle_engine] could not persist state: {e}")


def get_position_state(key):
    """
    `key` should be broker/asset-class-namespaced, e.g. "US_STOCKS:AAPL"
    or "CRYPTO:BTC-USD", so stocks and crypto tickers can never collide
    in the same state file.

    Creates a fresh entry (opened_at = now) the first time a key is
    seen -- this is the position-lifecycle-tracking equivalent of "when
    did we start watching this position," which for a position that was
    already open before this feature existed is "now," not its real
    original entry date. That's a deliberate, acceptable simplification:
    worst case a pre-existing position's MAX_HOLD_DAYS clock starts a
    few days later than its true entry, not earlier -- never forces a
    premature exit.
    """
    state = _load_state()
    if key not in state:
        state[key] = dict(_DEFAULT_ENTRY)
        state[key]["opened_at"] = datetime.now().isoformat()
        _save_state(state)
    return state[key]


def update_position_state(key, **updates):
    state = _load_state()
    if key not in state:
        state[key] = dict(_DEFAULT_ENTRY)
        state[key]["opened_at"] = datetime.now().isoformat()
    state[key].update(updates)
    _save_state(state)


def clear_position_state(key):
    """Call this once a position is fully closed, so a future re-entry
    on the same ticker starts fresh rather than inheriting old flags."""
    state = _load_state()
    if key in state:
        del state[key]
        _save_state(state)


def should_activate_breakeven(change_percent, breakeven_active):
    """One-way ratchet -- once active, never re-evaluated as False."""
    if breakeven_active:
        return False
    return change_percent >= BREAKEVEN_STOP_TRIGGER_PERCENT


def effective_stop_loss_percent(base_stop_loss_percent, breakeven_active):
    """
    base_stop_loss_percent is negative (e.g. -3.0 for a 3% stop-loss).
    Once break-even is active, the floor tightens to 0.0 (entry price)
    -- this only ever tightens the stop, never loosens it.
    """
    if breakeven_active:
        return max(base_stop_loss_percent, 0.0)
    return base_stop_loss_percent


def should_take_partial_profit(change_percent, partial_taken):
    """Only ever fires once per position -- partial_taken is a one-way flag."""
    if partial_taken:
        return False
    return change_percent >= PARTIAL_PROFIT_TRIGGER_PERCENT


def partial_profit_quantity(total_qty):
    return total_qty * PARTIAL_PROFIT_TAKE_FRACTION


def should_time_exit(opened_at, change_percent):
    """
    True only once the position has been open at least MAX_HOLD_DAYS
    AND is at or above break-even (change_percent >= 0). Never forces
    an exit while still at a loss -- that stays the stop-loss's job.
    """
    if opened_at is None:
        return False
    try:
        opened_at_dt = (
            opened_at if isinstance(opened_at, datetime)
            else datetime.fromisoformat(opened_at)
        )
    except (TypeError, ValueError):
        return False

    days_open = (datetime.now() - opened_at_dt).total_seconds() / 86400
    if days_open < MAX_HOLD_DAYS:
        return False

    return change_percent >= 0
