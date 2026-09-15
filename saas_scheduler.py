"""
Background scheduler for the multi-tenant SaaS AI decision loop.

Everything built so far in the SaaS product (saas_decision_engine.py,
saas_exit_engine.py, saas_reconcile_engine.py) only runs when a signed-
in user clicks Preview/Execute in saas_app.py -- Streamlit has no way to
run code on its own, so a position between visits gets zero protection
and no new trade ever happens unless someone is actively at the
dashboard. This script is the missing piece: a plain, non-Streamlit
Python entry point meant to be invoked on a timer (systemd timer / cron
-- see saas-scheduler.service + saas-scheduler.timer alongside this
file) that runs the full decision loop for every active user, once per
tick, with NO human confirmation step in between.

THIS IS A REAL SAFETY-MODEL CHANGE, not just "more code": every previous
piece of the SaaS execution stack was built manual-confirm-first
(Preview shows what would happen, a separate Execute click with an
explicit checkbox is required to actually place an order). This script
calls run_decision_loop_for_user(user_id, dry_run=False) directly --
real orders get placed with no per-tick human review. That is the whole
point of a scheduler, but it means the safety rails that matter now are
the ones that don't require a human watching: each user's own
trading_paused toggle (Settings, in saas_app.py) and the platform-wide
saas_emergency_stop.py flag (operator-only, no dashboard UI yet -- see
that file's docstring for how to activate it). BOTH are checked before
any order gets placed; make sure you understand how to flip either one
before running this unattended.

CADENCE: deliberately mirrors the single-owner bot's own app.py
autorefresh exactly -- one uniform tick (recommended: every 5 minutes,
matching st_autorefresh(interval=300000) there) covering every asset
class together, no market-hours-aware splitting. That's a known,
already-proven cadence from months of live single-owner operation, not
a new untested design. Splitting cadence by asset class (crypto more
frequent since it trades 24/7, stocks throttled to market hours) is a
reasonable future improvement once there's enough real multi-user load
to justify the added complexity -- premature right now with essentially
one real user.

DEPLOYMENT: this only becomes genuinely "unattended" running on an
always-on host (the droplet), as a systemd timer -- see
saas-scheduler.service/saas-scheduler.timer alongside this file for
ready-to-install units. Running it manually or via a laptop cron job is
fine for testing the mechanics, but a laptop that sleeps or gets closed
isn't real unattended operation. Deploying saas_app.py + this scheduler
to the droplet is a separate decision/step from writing this file --
this project has never put any SaaS code on the droplet yet (only the
single-owner app.py runs there today).

Logs to stdout only (same convention as app.py -- captured by
journalctl once this runs under systemd), one line per tick summary
plus one line per noteworthy per-user result (bought/sold/submitted/
reconciled/error). Silent ticks (nothing noteworthy for any user) still
log a one-line "tick complete" summary so a healthy-but-quiet scheduler
is distinguishable from one that stopped running entirely.
"""

from datetime import datetime

from engines import tenant_engine as tenant
from engines import saas_decision_engine
from engines import saas_emergency_stop
from engines import saas_scheduler_heartbeat as heartbeat
from engines import email_engine

# ADDED 2026-09-15 (card-optional trial redesign, Phase 2): base URL for
# the "Add Payment Method" link in the trial-ending reminder email --
# same hardcoded value saas_app.py itself uses for every other outbound
# link (checkout/portal return URLs, verification/reset links -- see its
# APP_URL/BASE_URL comment for why this is hardcoded rather than read
# from a request header), duplicated here since this script runs
# standalone, outside Streamlit.
APP_URL = "https://ordertradeai.com/app"

# Actions worth a log line -- "skipped"/"rejected"/"would_buy"/
# "would_sell" are either routine (nothing happened) or shouldn't occur
# at all here (would_buy/would_sell only appear when dry_run=True, which
# this script never passes) -- logging every "rejected: weak RR" for
# every ticker every 5 minutes would drown the real signal in noise.
_NOTEWORTHY_ACTIONS = {"bought", "sold", "submitted", "reconciled", "error"}


def _log(message):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {message}")


def run_one_tick():
    """Runs the decision loop for every active user, once. Safe to call
    repeatedly (e.g. from a systemd timer) -- never raises out to the
    caller; a crash for one user is logged and the rest continue."""
    # ADDED 2026-09-15 (card-optional trial redesign, Phase 3 enforcement):
    # this is the ONLY place tenant.expire_stale_trials() ever gets
    # called -- without it, a user's locally-tracked trial_ends_at could
    # pass with billing_status still stuck on 'trialing' forever, since
    # nothing else in the codebase re-checks it. Flipping expired users to
    # 'trial_expired' here is what makes both existing billing gates
    # (saas_app.py's render_dashboard(), which blocks the whole dashboard,
    # and saas_decision_engine.py's own gate a few lines below, which
    # blocks only new BUYs while leaving exit protection running) actually
    # take effect -- neither of those gates' own logic needed to change
    # for this; they already treat anything other than 'trialing'/'active'
    # as not-current. Runs even during a platform-wide emergency stop
    # (checked next) since billing status is a bookkeeping concern
    # unrelated to whether trading itself is currently allowed.
    try:
        expired_user_ids = tenant.expire_stale_trials()
        if expired_user_ids:
            _log(
                f"Trial expired for {len(expired_user_ids)} user(s): "
                f"{', '.join(expired_user_ids)}"
            )
    except Exception as e:
        _log(f"Could not check for expired trials this tick: {e}")

    # ADDED 2026-09-15 (card-optional trial redesign, Phase 2): the "3
    # days left" reminder. get_users_needing_trial_reminder() only
    # returns users who haven't been reminded yet (trial_reminder_sent=0)
    # and still have no Stripe subscription, so this is safe to run every
    # tick without spamming anyone -- mark_trial_reminder_sent() is only
    # called after send_trial_ending_reminder() succeeds, so a delivery
    # failure leaves the user eligible to be picked up and retried on the
    # next tick instead of silently never being reminded.
    try:
        for candidate in tenant.get_users_needing_trial_reminder(days_before=3):
            days_left = tenant.get_trial_days_remaining(candidate["user_id"])
            if days_left is None:
                continue
            try:
                email_engine.send_trial_ending_reminder(
                    candidate["email"], days_left, APP_URL
                )
                tenant.mark_trial_reminder_sent(candidate["user_id"])
                _log(f"user={candidate['user_id']} trial reminder sent ({days_left}d left).")
            except Exception as e:
                _log(f"user={candidate['user_id']} trial reminder FAILED, will retry next tick: {e}")
    except Exception as e:
        _log(f"Could not check for trial reminders this tick: {e}")

    if saas_emergency_stop.is_stopped():
        reason = saas_emergency_stop.get_reason()
        _log(f"Platform-wide stop is active{f' ({reason})' if reason else ''} -- skipping this tick entirely.")
        # Still a heartbeat: the scheduler process is alive and correctly
        # honoring an intentional operator stop, not dead -- the watchdog
        # must not mistake this for a failure. See saas_scheduler_
        # heartbeat.py's module docstring.
        heartbeat.write_heartbeat()
        return

    try:
        user_ids = tenant.list_active_users()
    except Exception as e:
        _log(f"Could not load active users -- skipping this tick: {e}")
        # Deliberately NOT writing a heartbeat here -- an active_users
        # load failure means the scheduler can't do its job even though
        # the process itself is technically alive, and a persistent DB
        # problem is exactly the kind of silent failure this dead-man's
        # switch should eventually surface too.
        return

    _log(f"Starting tick for {len(user_ids)} active user(s).")

    for user_id in user_ids:
        try:
            results = saas_decision_engine.run_decision_loop_for_user(user_id, dry_run=False)
        except Exception as e:
            _log(f"user={user_id} CRASHED, skipping to next user: {e}")
            continue

        for r in results:
            if r.get("action") in _NOTEWORTHY_ACTIONS:
                ticker = r.get("ticker") or "-"
                _log(f"user={user_id} [{r['action']}] {ticker}: {r.get('message', '')}")

    heartbeat.write_heartbeat()
    _log("Tick complete.")


if __name__ == "__main__":
    run_one_tick()
