"""
Read-only aggregation for the platform admin view (saas_app.py's Admin
Panel, gated by ADMIN_EMAILS -- see that file). Everything here is
built on top of existing per-user functions (tenant_engine,
saas_broker_factory) -- no new write paths, no new credential access
beyond what those modules already expose. This file only summarizes.

Deliberately does NOT attempt to sum unrealized P&L into one platform-
wide dollar figure -- users hold different brokers in different
currencies/leverage models (eToro CFDs especially, see
saas_broker_factory.py's own docstring on why eToro P&L is left as
None rather than guessed at), and adding them together would produce a
number that looks precise but means nothing real. Exposure here means
position COUNTS, not a blended dollar total.
"""

from engines import tenant_engine as tenant
from engines import saas_broker_factory


def get_admin_user_summary():
    """
    One row per user account for the admin Users table: email, joined
    date, active/paused status, and which brokers they've connected
    (names only -- never credentials). Connected-broker lookup is one
    extra query per user; fine at the current early-access scale, not
    meant to be run at high frequency (this is a manual admin-view
    action, not part of the automated decision loop).
    """
    users = tenant.list_all_users_admin_view()
    for u in users:
        try:
            connected = tenant.list_connected_brokers(u["user_id"])
        except Exception:
            connected = []
        u["connected_brokers"] = [c["broker"] for c in connected]
    return users


def get_platform_exposure_summary():
    """
    Iterates every ACTIVE user's connected brokers and counts open
    positions -- total platform-wide, and broken down per broker.
    Wrapped per-user/per-broker in try/except so one user's broken
    broker connection (expired keys, broker outage) can't blank out the
    whole summary for everyone else; that user's contribution is just
    silently 0 for that broker, same "never crash the caller" pattern
    saas_broker_factory.py already uses throughout.
    """
    per_broker_counts = {"ALPACA": 0, "BINANCE": 0, "ETORO": 0}
    total_users_with_positions = 0

    for user_id in tenant.list_active_users():
        try:
            connected = tenant.list_connected_brokers(user_id)
        except Exception:
            continue

        user_has_any_position = False
        for c in connected:
            broker = c["broker"]
            try:
                positions = saas_broker_factory.get_user_open_positions(user_id, broker)
            except Exception:
                positions = []
            if positions:
                user_has_any_position = True
            per_broker_counts[broker] = per_broker_counts.get(broker, 0) + len(positions)

        if user_has_any_position:
            total_users_with_positions += 1

    return {
        "total_open_positions": sum(per_broker_counts.values()),
        "per_broker": per_broker_counts,
        "users_with_open_positions": total_users_with_positions,
    }
