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
