"""
Stripe Checkout + Billing Portal session creation for the SaaS
product's subscription ($39/month flat rate -- see the Price created
in Stripe's OrderTrade AI account, referenced here by STRIPE_PRICE_ID).

Deliberately thin: this file only ever creates short-lived Stripe
Checkout/Portal sessions and hands back a URL for saas_app.py to send
the user to via st.link_button. It never reads or writes
saas_platform.db directly, and it never decides what a user's billing
status IS -- that's engines/tenant_engine.py's job, updated only by
saas_webhook_server.py in reaction to real Stripe webhook events. This
file's job ends the moment Stripe's own hosted page takes over.

Uses a SEPARATE Stripe account from any other project on purpose
(created 2026-08-28), same reasoning as the separate Resend account:
OrderTrade AI is a plain SaaS subscription, categorically different
risk profile from money-transmission business lines, and mixing them
under one Stripe account risks the SaaS billing getting swept into
scrutiny/holds that have nothing to do with it.

REDESIGNED 2026-09-15 (card-optional trial): the 14-day free trial used
to live entirely in Stripe -- every Checkout Session this file created
included subscription_data.trial_period_days=14, so a card was required
up front (Checkout always collects a payment method unless told
otherwise) even though nothing would be charged for 2 weeks. Real
prospects were bouncing at that step before ever trying the product.
The trial is now granted directly in engines/tenant_engine.py at
signup (see create_user()/TRIAL_LENGTH_DAYS there) with NO Stripe
interaction at all -- this file's create_checkout_session() is only
ever called once a user actively chooses to add a payment method,
either proactively during their trial or because it already ran out
(see saas_app.py's render_billing_gate() and its new "add payment
method" section). Which case it is changes what gets passed to Stripe:
- Trial still running: trial_end is set to the user's own
  trial_ends_at (tenant_engine.get_billing_info()) converted to a Unix
  timestamp, so adding a card early locks in the plan but does NOT
  start billing any sooner than the free trial they were already
  promised -- Stripe won't charge until that same original date.
- Trial already expired (or trial_end isn't safely in the future --
  see _MIN_TRIAL_END_LEAD_SECONDS below): no trial at all, billing
  starts immediately on completing Checkout, matching "your free trial
  is over, pay to keep trading."
"""

import os
from datetime import datetime, timezone

import stripe

# Stripe requires subscription_data.trial_end to be meaningfully in the
# future, not just "not yet passed" -- a timestamp a few seconds out
# risks a race between this request being built and Stripe processing
# it. This buffer is deliberately generous (well under Stripe's own
# actual minimum) so "add a card with 10 minutes left on your trial"
# still safely falls through to the no-trial/charge-now path below
# rather than risking a Checkout Session creation error.
_MIN_TRIAL_END_LEAD_SECONDS = 3600


def _configure():
    """
    Sets stripe.api_key from the environment on every call rather than
    once at import time -- cheap, and avoids a stale/missing key
    silently persisting across a long-running process if .env is ever
    reloaded or the module is imported before the environment is fully
    populated (mirrors the lazy-lookup pattern in
    engines/email_engine.py's _send_email()).
    """
    api_key = os.environ.get("STRIPE_SECRET_KEY")
    if not api_key:
        raise RuntimeError(
            "STRIPE_SECRET_KEY is not set. Add it to .env from the Stripe "
            "dashboard (Developers -> API keys) -- never commit it, never "
            "reuse it across unrelated Stripe accounts."
        )
    stripe.api_key = api_key


def _price_id():
    price_id = os.environ.get("STRIPE_PRICE_ID")
    if not price_id:
        raise RuntimeError(
            "STRIPE_PRICE_ID is not set. Add it to .env -- the Price ID "
            "for the OrderTrade AI Subscription product in Stripe."
        )
    return price_id


def create_checkout_session(user_id, email, base_url, trial_ends_at=None):
    """
    Creates a Stripe Checkout Session for a new subscription and returns
    the hosted checkout URL to redirect the user to. client_reference_id
    carries our own user_id through to the checkout.session.completed
    webhook -- that's how saas_webhook_server.py maps the completed
    session back to the right account (Stripe's own customer/
    subscription IDs don't exist yet at the point this function runs).

    trial_ends_at (added 2026-09-15, card-optional trial redesign): an
    ISO8601 timestamp string -- normally tenant.get_billing_info(user_id)
    ["trial_ends_at"], this platform's own record of when a user's
    card-optional trial runs out (see tenant_engine.py's BILLING section
    docstring). When provided and safely in the future (more than
    _MIN_TRIAL_END_LEAD_SECONDS away), the resulting Stripe subscription
    won't start charging until that same date -- adding a card early
    doesn't cost a user any of the free trial they were already
    promised. When omitted, or already in the past, or too close to
    "now" to safely hand Stripe a future timestamp, the subscription
    starts immediately with NO trial -- the correct behavior for a user
    whose trial has already run out and is paying to restore access.
    """
    _configure()

    subscription_data = {}
    if trial_ends_at:
        try:
            trial_end_dt = datetime.fromisoformat(trial_ends_at)
            lead_seconds = (trial_end_dt - datetime.now(timezone.utc)).total_seconds()
            if lead_seconds >= _MIN_TRIAL_END_LEAD_SECONDS:
                subscription_data["trial_end"] = int(trial_end_dt.timestamp())
        except (TypeError, ValueError):
            pass  # malformed -- fall through to no-trial, charge-now

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=email,
        client_reference_id=user_id,
        line_items=[{"price": _price_id(), "quantity": 1}],
        subscription_data=subscription_data,
        success_url=f"{base_url}/?billing=success",
        cancel_url=f"{base_url}/?billing=cancelled",
    )
    return session.url


def create_billing_portal_session(stripe_customer_id, base_url):
    """
    Creates a Stripe Billing Portal session for an existing customer --
    lets them update their card, view invoices, or cancel, all on
    Stripe's own hosted page. Returns the URL to redirect to.
    """
    _configure()
    session = stripe.billing_portal.Session.create(
        customer=stripe_customer_id,
        return_url=f"{base_url}/",
    )
    return session.url
