"""
Platform-wide emergency stop for the multi-tenant SaaS product.

Deliberately a SEPARATE flag file from the single-owner bot's own
emergency_stop.py -- that one is checked by app.py's own execution paths
and is meant to be one person's personal kill switch. Coupling this
SaaS's every-user trading to that same file would mean stopping your own
single-owner bot also silently stops every SaaS user's trading (or vice
versa), which is exactly the kind of incidental coupling
saas_broker_factory.py's docstring already explicitly warns against for
config.py's EXECUTION_KILL_SWITCH. This file exists so there is a
correct, SaaS-only equivalent instead of either reusing that one
unsafely or having no platform-wide switch at all.

This is the OPERATOR-level lever -- for "something is systemically wrong
across the whole SaaS, stop every user's new trading right now" (e.g. a
bad model file, a broken broker integration, a discovered bug in the
decision loop itself). It is NOT meant to replace each user's own
trading_paused setting (engines/tenant_engine.py's save_user_settings()),
which is that user's own per-account kill switch for their own trading
only. There is no admin UI for this yet -- activate/deactivate by
running a one-line script on the server (see each function's docstring).

Same semantics as the single-owner bot's EXECUTION_KILL_SWITCH and each
user's own trading_paused: blocks new BUY evaluation only.
engines/saas_exit_engine.py (stop-loss/take-profit/time-exit) is
deliberately NOT gated by this -- a platform-wide halt should never trap
someone in a position that would otherwise have closed protectively.
"""

import os
from datetime import datetime

_FLAG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "saas_emergency_stop.flag"
)


def is_stopped() -> bool:
    return os.path.exists(_FLAG_PATH)


def activate(reason: str = "") -> None:
    """
    e.g. from a shell on the server:
        python3 -c "from engines import saas_emergency_stop as es; es.activate('reason here')"
    """
    with open(_FLAG_PATH, "w") as f:
        f.write(f"SaaS-wide stop activated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        if reason:
            f.write(reason)


def deactivate() -> None:
    """
    python3 -c "from engines import saas_emergency_stop as es; es.deactivate()"
    """
    if os.path.exists(_FLAG_PATH):
        os.remove(_FLAG_PATH)


def get_reason() -> str:
    if not is_stopped():
        return ""
    try:
        with open(_FLAG_PATH) as f:
            f.readline()  # skip the "SaaS-wide stop activated at ..." line
            return f.readline().strip()
    except OSError:
        return ""
