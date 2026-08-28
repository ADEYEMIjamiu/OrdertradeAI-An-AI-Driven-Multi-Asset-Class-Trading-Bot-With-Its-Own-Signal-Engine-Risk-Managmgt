"""
Stripe Checkout + Billing Portal session creation for the SaaS
product's subscription (7-day free trial, then $39/month flat rate --
see the Price created in Stripe's OrderTrade AI account, referenced
here by STRIPE_PRICE_ID).

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
"""

import os

import stripe

_TRIAL_DAYS = 7


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


def create_checkout_session(user_id, email, base_url):
    """
    Creates a Stripe Checkout Session for a new subscription with a
    7-day trial, and returns the hosted checkout URL to redirect the
    user to. client_reference_id carries our own user_id through to
    the checkout.session.completed webhook -- that's how
    saas_webhook_server.py maps the completed session back to the
    right account (Stripe's own customer/subscription IDs don't exist
    yet at the point this function runs).
    """
    _configure()
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=email,
        client_reference_id=user_id,
        line_items=[{"price": _price_id(), "quantity": 1}],
        subscription_data={"trial_period_days": _TRIAL_DAYS},
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
