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

import stripe
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from engines import tenant_engine as tenant

# Loaded explicitly here (not just relied on transitively via
# tenant_engine's own load_dotenv() call) so STRIPE_WEBHOOK_SECRET is
# guaranteed available to this file's own _webhook_secret() regardless
# of import order.
load_dotenv()

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


routes = [Route("/webhooks/stripe", stripe_webhook, methods=["POST"])]
app = Starlette(routes=routes)
