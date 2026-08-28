"""
Transactional email sending for the SaaS product (password reset,
signup verification) via Resend's REST API. Deliberately calls
Resend's plain HTTPS endpoint with `requests` rather than adding their
`resend` SDK as a new dependency -- the API surface used here (one POST
per email) is small enough that a dedicated package isn't worth it.

RESEND_API_KEY lives in the environment (see .env.example), same
pattern as every other secret in this project -- never in git, never
logged. FROM_ADDRESS uses the ordertradeai.com domain verified in
Resend on 2026-08-28 (a SEPARATE Resend account/team from any other
project's, deliberately, so billing/usage never mixes between
unrelated businesses).

Every function here raises on failure rather than swallowing errors --
a password reset or verification email that silently fails to send
would strand a user with no way to know why nothing arrived. Callers
(saas_app.py) catch these and show a clear error rather than a false
"check your email" success message.
"""

import os

import requests

RESEND_API_URL = "https://api.resend.com/emails"
FROM_ADDRESS = "OrderTrade AI <noreply@ordertradeai.com>"
_REQUEST_TIMEOUT_SECONDS = 10


def _send_email(to_email, subject, html_body):
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError(
            "RESEND_API_KEY is not set. Generate one in the Resend dashboard "
            "(API keys) and add it to .env -- never commit it, never reuse "
            "it across unrelated projects."
        )
    response = requests.post(
        RESEND_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": FROM_ADDRESS,
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        },
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def send_password_reset_email(to_email, reset_url):
    html = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>Reset your OrderTrade AI password</h2>
        <p>Click the button below to choose a new password. This link expires in 1 hour.</p>
        <p style="margin: 24px 0;">
            <a href="{reset_url}" style="background: #10b981; color: #fff; padding: 12px 24px;
               text-decoration: none; border-radius: 6px; display: inline-block;">
               Reset Password
            </a>
        </p>
        <p style="color: #666; font-size: 13px;">
            If you didn't request this, you can safely ignore this email --
            your password will not be changed.
        </p>
    </div>
    """
    return _send_email(to_email, "Reset your OrderTrade AI password", html)


def send_verification_email(to_email, verify_url):
    html = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>Verify your email for OrderTrade AI</h2>
        <p>Click the button below to confirm this is your email address. This link expires in 3 days.</p>
        <p style="margin: 24px 0;">
            <a href="{verify_url}" style="background: #10b981; color: #fff; padding: 12px 24px;
               text-decoration: none; border-radius: 6px; display: inline-block;">
               Verify Email
            </a>
        </p>
        <p style="color: #666; font-size: 13px;">
            If you didn't sign up for OrderTrade AI, you can safely ignore this email.
        </p>
    </div>
    """
    return _send_email(to_email, "Verify your email for OrderTrade AI", html)


def send_email_change_confirmation(to_email, confirm_url):
    """
    Sent to the NEW address someone entered in Account Settings -- see
    tenant_engine.request_email_change()'s docstring for why the swap
    only takes effect once this link is clicked (proves the new address
    is actually reachable by the account owner, and gives a mistyped
    address zero effect rather than locking anyone out).
    """
    html = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>Confirm your new email for OrderTrade AI</h2>
        <p>Someone requested to change the email on an OrderTrade AI account to this
           address. Click the button below to confirm the change. This link expires in 1 hour.</p>
        <p style="margin: 24px 0;">
            <a href="{confirm_url}" style="background: #10b981; color: #fff; padding: 12px 24px;
               text-decoration: none; border-radius: 6px; display: inline-block;">
               Confirm New Email
            </a>
        </p>
        <p style="color: #666; font-size: 13px;">
            If you didn't request this, you can safely ignore this email -- the
            account's email address will not change.
        </p>
    </div>
    """
    return _send_email(to_email, "Confirm your new email for OrderTrade AI", html)
