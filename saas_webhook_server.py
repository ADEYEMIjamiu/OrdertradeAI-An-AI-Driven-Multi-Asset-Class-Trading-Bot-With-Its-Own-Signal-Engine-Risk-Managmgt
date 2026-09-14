"""
Minimal standalone webhook receiver for Stripe billing events.

Deliberately a SEPARATE process/port from saas_app.py, not a route
bolted onto it -- Streamlit has no supported way to add a custom HTTP
route for server-to-server webhooks, so this is a small Starlette app
run under uvicorn on its own port (8503), proxied at
https://ordertradeai.com/webhooks/stripe by nginx (see
deploy/nginx-ordertradeai-com.conf). It shares the same
saas_platform.db as saas_app.py via engines/tenant_engine.py -- no
separate database, just a separate process for handling one narrow
job (verifying Stripe's webhook signature and reacting to the event).

Uses Starlette + uvicorn rather than adding Flask/FastAPI as new
dependencies -- both were already present transitively (Streamlit
itself depends on them), so this file is the only genuinely new
runtime dependency this feature adds beyond the `stripe` package.

Run directly for local testing:
    uvicorn saas_webhook_server:app --host 127.0.0.1 --port 8503
(see deploy/saas-webhook.service for the systemd unit that runs this
in production)

Every event handler below is deliberately tolerant of unknown/
untracked Stripe customers (a no-op, not an error) -- see
engines/tenant_engine.py's update_billing_status_by_customer()
docstring for why.
"""

import os
import time

import requests
import stripe
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

# Loaded explicitly here, and BEFORE importing telegram_notifier below
# (not just relied on transitively via tenant_engine's own load_dotenv()
# call) so both STRIPE_WEBHOOK_SECRET (this file's own
# _webhook_secret()) and TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID
# (telegram_notifier's module-level os.getenv() calls, evaluated once at
# import time) are guaranteed available regardless of import order.
# FIX 2026-09-14: telegram_notifier was originally imported above this
# load_dotenv() call -- harmless for Stripe (this file's own
# _webhook_secret() re-reads os.environ lazily on every call, after
# load_dotenv() had already run by request time), but telegram_notifier
# reads its two env vars once into module-level constants at import
# time, so importing it before load_dotenv() ran would have silently
# left both as None and every visit notification a permanent no-op.
load_dotenv()

import telegram_notifier
from engines import tenant_engine as tenant

# stripe-python moved SignatureVerificationError from stripe.error.* to
# a top-level stripe.* name in its v7 rewrite; requirements.txt
# deliberately doesn't pin an exact stripe version, so this resolves
# whichever location actually exists at install time rather than
# hardcoding one and risking an AttributeError on the other.
try:
    _SignatureVerificationError = stripe.SignatureVerificationError
except AttributeError:
    _SignatureVerificationError = stripe.error.SignatureVerificationError


def _webhook_secret():
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError(
            "STRIPE_WEBHOOK_SECRET is not set. Add it to .env -- the "
            "signing secret shown when the webhook endpoint was created "
            "in the Stripe dashboard (Developers -> Webhooks)."
        )
    return secret


async def stripe_webhook(request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, _webhook_secret())
    except (ValueError, _SignatureVerificationError):
        # Malformed payload or a signature that doesn't match -- never
        # trust the body without a valid signature, since anyone on the
        # internet can POST to this public URL.
        return PlainTextResponse("invalid payload or signature", status_code=400)

    event_type = event["type"]
    obj = event["data"]["object"]
    # Newer stripe-python releases stopped making StripeObject act like a
    # plain dict (a Session/Subscription/Invoice no longer supports
    # .get(...) directly -- it raises AttributeError telling you to call
    # .to_dict() first). Normalizing here once, defensively, means the
    # dispatch logic below can keep using plain .get(...) regardless of
    # which stripe-python version is actually installed (requirements.txt
    # deliberately doesn't pin one -- see this file's module docstring).
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()

    if event_type == "checkout.session.completed":
        user_id = obj.get("client_reference_id")
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")
        if user_id and customer_id:
            tenant.link_stripe_customer(user_id, customer_id, subscription_id)

    elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
        customer_id = obj.get("customer")
        subscription_id = obj.get("id")
        status = obj.get("status")  # trialing, active, past_due, canceled, unpaid, incomplete...
        if customer_id and status:
            tenant.update_billing_status_by_customer(customer_id, status, subscription_id)

    elif event_type == "customer.subscription.deleted":
        customer_id = obj.get("customer")
        if customer_id:
            tenant.update_billing_status_by_customer(customer_id, "canceled", obj.get("id"))

    elif event_type == "invoice.payment_failed":
        customer_id = obj.get("customer")
        if customer_id:
            tenant.update_billing_status_by_customer(customer_id, "past_due")

    # Every other event type is intentionally ignored -- this endpoint
    # only subscribes to the 4 events it actually handles (see the
    # webhook destination's configured event list in Stripe), but
    # returning 200 for anything unexpected that slips through is
    # still correct: Stripe retries on non-2xx, and there's nothing to
    # retry here.
    return PlainTextResponse("ok")


# FEATURE 2026-09-14: personal-use visit notifications for
# ordertradeai.com, requested so the owner can see (in the same
# Telegram chat that already gets trade-fill alerts) when someone
# actually visits the site and roughly where from -- a simple pulse
# check on traffic/growth without needing to log into Google Analytics.
#
# Deliberately fires from landing/index.html's loadGA() rather than
# unconditionally on every page load -- that function only ever runs
# after a visitor grants cookie consent (see that file's own comments),
# so this stays behind the exact same gate the site's cookie banner
# already promises visitors ("we use Google Analytics... only after
# you accept"). Sending a location-derived Telegram alert for every
# visitor regardless of consent would go further than what's disclosed
# there. If the consent gate is ever relaxed, this should move with it.
#
# In-memory-only debounce and dedup -- no database table for this,
# since it's a personal notification, not billing/auth data that must
# survive a restart. Losing state on a service restart just means a
# possible one-off duplicate ping, not anything that matters.
_last_notified_by_ip = {}
_VISIT_DEBOUNCE_SECONDS = 30 * 60

# Common crawler/monitoring substrings -- not exhaustive, just enough
# to filter the noisiest, most common bots out of a personal traffic
# pulse-check so it stays meaningful rather than mostly search-engine
# indexing hits.
_BOT_USER_AGENT_SUBSTRINGS = (
    "bot", "spider", "crawl", "slurp", "curl", "wget", "python-requests",
    "facebookexternalhit", "pingdom", "uptimerobot", "headlesschrome",
)


def _client_ip(request):
    # nginx's /track/visit location explicitly sets X-Forwarded-For to
    # $remote_addr (see deploy/nginx-ordertradeai-com.conf) since none
    # of this app's other routes needed the real visitor IP before now
    # -- request.client.host alone would just be nginx's own loopback
    # address (127.0.0.1), not the actual visitor.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _geolocate(ip):
    """
    Best-effort city/country/region lookup via ip-api.com's free tier
    (no API key, ~45 requests/minute -- comfortably enough for a
    personal-traffic notification, not a high-volume analytics
    pipeline). Returns None on any failure or for private/local IPs
    (ip-api.com itself reports "status": "fail" for those, e.g. testing
    against 127.0.0.1) rather than raising -- a broken or slow
    geolocation lookup must never be the reason a visitor's own request
    to the site hangs or errors.
    """
    try:
        response = requests.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "status,country,regionName,city,query"},
            timeout=3,
        )
        data = response.json()
        if data.get("status") != "success":
            return None
        city = data.get("city") or ""
        region = data.get("regionName") or ""
        country = data.get("country") or ""
        parts = [p for p in (city, region, country) if p]
        return ", ".join(parts) if parts else None
    except Exception:
        return None


async def track_visit(request):
    ip = _client_ip(request)
    user_agent = request.headers.get("user-agent", "").lower()

    if any(marker in user_agent for marker in _BOT_USER_AGENT_SUBSTRINGS):
        return PlainTextResponse("ok")

    now = time.monotonic()
    last = _last_notified_by_ip.get(ip)
    if last is not None and (now - last) < _VISIT_DEBOUNCE_SECONDS:
        return PlainTextResponse("ok")
    _last_notified_by_ip[ip] = now

    try:
        body = await request.json()
    except Exception:
        body = {}
    page = str(body.get("page") or "/")[:200]  # cap length -- client-supplied

    location = _geolocate(ip)
    location_text = location or f"unknown location ({ip})"

    telegram_notifier.send_telegram_message(
        f"\U0001F440 Site visit: {page}\n"
        f"From: {location_text}"
    )

    return PlainTextResponse("ok")


routes = [
    Route("/webhooks/stripe", stripe_webhook, methods=["POST"]),
    Route("/track/visit", track_visit, methods=["POST"]),
]
app = Starlette(routes=routes)
