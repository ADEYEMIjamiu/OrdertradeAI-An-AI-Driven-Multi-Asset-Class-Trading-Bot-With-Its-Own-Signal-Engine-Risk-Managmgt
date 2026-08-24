"""
Simple file-based emergency stop switch for OrderTrade AI.

Deliberately NOT implemented with st.session_state: session_state is
scoped per-browser-session, so a stop triggered from one tab/device
would be invisible to the auto-trading loop running in a different
session, and would also be forgotten across a server/process restart.
A flag file on disk is checked by every execution path regardless of
which session (or none) is driving it, and survives restarts.
"""
import os
from datetime import datetime

_FLAG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "emergency_stop.flag"
)


def is_stopped() -> bool:
    return os.path.exists(_FLAG_PATH)


def activate(reason: str = "") -> None:
    with open(_FLAG_PATH, "w") as f:
        f.write(f"Stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        if reason:
            f.write(reason)


def deactivate() -> None:
    if os.path.exists(_FLAG_PATH):
        os.remove(_FLAG_PATH)


def stopped_since() -> str:
    if not is_stopped():
        return ""
    try:
        with open(_FLAG_PATH) as f:
            return f.readline().strip()
    except OSError:
        return ""


def get_reason() -> str:
    """
    Returns the reason string passed to activate() (the second line of
    the flag file), or "" if not stopped or no reason was given.

    Added 2026-08-24 so app.py's kill-switch sync logic (see
    sync_kill_switch_with_emergency_stop() there) can tell apart an
    emergency stop that IT activated (config.py's EXECUTION_KILL_SWITCH)
    from one a person activated manually via the dashboard button --
    without this, turning EXECUTION_KILL_SWITCH back to False could
    silently undo a manual stop, or a manual deactivate click could
    silently leave the kill switch's own intent unenforced.
    """
    if not is_stopped():
        return ""
    try:
        with open(_FLAG_PATH) as f:
            f.readline()  # skip the "Stopped at ..." line
            return f.readline().strip()
    except OSError:
        return ""
