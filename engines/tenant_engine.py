"""
Multi-tenant SaaS foundation: user accounts, per-user broker credentials,
and per-user settings.

Deliberately kept in its own database (SAAS_DB_NAME = "saas_platform.db"),
completely separate from trade_journal.db -- your own bot's live trading
history, orders, and rotation data are never touched by anything in this
file. This is purely additive scaffolding for the future multi-user
product; nothing in the existing single-owner app.py trading paths
imports or depends on this module yet.

Architecture decisions locked in 2026-08-25 (see conversation): bring-
your-own-broker custody model (each user connects their OWN Alpaca/
Binance/eToro API keys -- this platform never pools or custodies user
funds), one shared multi-tenant app with per-user data isolation (this
file), and paper/demo-only at launch (enforced at the settings layer,
see user_settings.allow_live_trading below -- defaults to False and is
NOT exposed to change via any UI built so far).

SECURITY NOTE: broker API keys/secrets are encrypted at rest using
Fernet symmetric encryption (cryptography package, already a dependency
via alpaca-py). The encryption key lives in the environment
(SAAS_ENCRYPTION_KEY), never in this database and never in git --
same pattern as every other secret in this project (see .env.example).
Losing that key means every stored credential becomes permanently
undecryptable (by design -- there is no backdoor). Passwords are hashed
with bcrypt, never stored or logged in plaintext, never encrypted
(hashing and encryption are different for a reason: passwords should
never be recoverable, even by us).
"""

import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

# Loaded here directly (not just relied on transitively via some other
# module) since this file reads SAAS_ENCRYPTION_KEY from the environment
# itself and shouldn't depend on import order elsewhere.
load_dotenv()

SAAS_DB_NAME = "saas_platform.db"


def _get_connection():
    conn = sqlite3.connect(SAAS_DB_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_broker_credentials (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            broker TEXT NOT NULL,
            environment TEXT NOT NULL,
            api_key_encrypted TEXT,
            api_secret_encrypted TEXT,
            extra_encrypted TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, broker),
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id TEXT PRIMARY KEY,
            max_position_size REAL NOT NULL DEFAULT 0.20,
            enabled_asset_classes TEXT NOT NULL DEFAULT '[]',
            allow_live_trading INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    # Migration for databases created before 2026-08-26 (trading_paused
    # didn't exist yet) -- CREATE TABLE IF NOT EXISTS above is a no-op on
    # an existing table, so this ADD COLUMN is the only way an already-
    # running install picks up the new column. Wrapped in try/except:
    # SQLite has no "ADD COLUMN IF NOT EXISTS", and re-running this on a
    # database that already has the column would otherwise raise
    # OperationalError on every single connection.
    try:
        conn.execute(
            "ALTER TABLE user_settings ADD COLUMN trading_paused INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migration for databases created before 2026-08-28 (email_verified
    # didn't exist yet). New signups default to 0 (unverified) --
    # deliberately NOT auto-verified on signup, otherwise the whole
    # point of a verification email is defeated. Existing accounts
    # created before this migration are left at 0 too rather than
    # silently marked verified -- there's no way to know if those
    # emails were ever actually confirmed as reachable.
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migration for databases created before 2026-08-28 (Stripe billing
    # didn't exist yet). billing_status defaults to 'none' -- a brand
    # new account hasn't started a subscription until they complete
    # Stripe Checkout (see engines/billing_engine.py + the webhook
    # handler in saas_webhook_server.py, which is what actually flips
    # this to 'trialing'/'active'/'past_due'/'canceled'). Never set
    # directly by any code path other than that webhook -- this column
    # exists to mirror what Stripe says is true, not to be a second
    # source of truth someone could accidentally desync.
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN stripe_customer_id TEXT"
        )
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN stripe_subscription_id TEXT"
        )
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN billing_status TEXT NOT NULL DEFAULT 'none'"
        )
    except sqlite3.OperationalError:
        pass  # column already exists

    conn.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_verification_tokens (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    return conn


# ============================================================
# ENCRYPTION -- broker credentials only. Never used for passwords.
# ============================================================

def _get_fernet():
    """
    Loads SAAS_ENCRYPTION_KEY from the environment. Raises a clear error
    rather than silently falling back to some default key -- a default/
    hardcoded encryption key would defeat the entire point of encrypting
    other users' broker secrets in the first place.
    """
    key = os.environ.get("SAAS_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "SAAS_ENCRYPTION_KEY is not set. Generate one with "
            "`python3 -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and add it to "
            ".env (never commit it, never reuse it across environments)."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext):
    if plaintext is None:
        return None
    return _get_fernet().encrypt(str(plaintext).encode()).decode()


def decrypt_secret(ciphertext):
    if ciphertext is None:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # Wrong/rotated key, or corrupted data -- never guess, never
        # return a partial/garbled secret to a broker API call.
        raise RuntimeError(
            "Could not decrypt stored credential -- SAAS_ENCRYPTION_KEY "
            "may have changed since this was saved. Re-enter broker "
            "credentials for this user."
        )


# ============================================================
# USERS
# ============================================================

def create_user(email, password):
    """
    Creates a new user account. Returns the new user_id, or None if the
    email is already registered (case-insensitive -- emails are
    normalized to lowercase before the uniqueness check and storage).
    """
    email = str(email).strip().lower()
    conn = _get_connection()
    try:
        existing = conn.execute(
            "SELECT user_id FROM users WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            return None

        user_id = str(uuid.uuid4())
        password_hash = bcrypt.hashpw(
            password.encode(), bcrypt.gensalt()
        ).decode()
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            "INSERT INTO users (user_id, email, password_hash, created_at, is_active) "
            "VALUES (?, ?, ?, ?, 1)",
            (user_id, email, password_hash, now),
        )
        # Every new user starts with paper/demo-only enforced -- see
        # allow_live_trading default and module docstring.
        conn.execute(
            "INSERT INTO user_settings (user_id, max_position_size, enabled_asset_classes, "
            "allow_live_trading, created_at, updated_at) VALUES (?, 0.20, '[]', 0, ?, ?)",
            (user_id, now, now),
        )
        conn.commit()
        return user_id
    finally:
        conn.close()


def authenticate_user(email, password):
    """
    Returns the user_id if email/password match an active account,
    otherwise None. Deliberately returns the same "None" for both
    "no such email" and "wrong password" -- distinguishing the two in
    the response would let an attacker enumerate registered emails.
    """
    email = str(email).strip().lower()
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, password_hash, is_active FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if not row:
            return None
        user_id, password_hash, is_active = row
        if not is_active:
            return None
        if bcrypt.checkpw(password.encode(), password_hash.encode()):
            return user_id
        return None
    finally:
        conn.close()


def get_user(user_id):
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, email, created_at, is_active, email_verified FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "user_id": row[0],
            "email": row[1],
            "created_at": row[2],
            "is_active": bool(row[3]),
            "email_verified": bool(row[4]),
        }
    finally:
        conn.close()


# ============================================================
# BROKER CREDENTIALS (encrypted at rest)
# ============================================================

def save_broker_credentials(user_id, broker, environment, api_key=None, api_secret=None, extra=None):
    """
    Insert or replace this user's credentials for one broker. `extra` is
    an optional third secret some brokers need beyond key/secret (e.g.
    eToro's separate user_key) -- stored encrypted the same way.
    """
    conn = _get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        cred_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO user_broker_credentials
                (id, user_id, broker, environment, api_key_encrypted,
                 api_secret_encrypted, extra_encrypted, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, broker) DO UPDATE SET
                environment = excluded.environment,
                api_key_encrypted = excluded.api_key_encrypted,
                api_secret_encrypted = excluded.api_secret_encrypted,
                extra_encrypted = excluded.extra_encrypted,
                updated_at = excluded.updated_at
            """,
            (
                cred_id, user_id, broker.upper(), environment,
                encrypt_secret(api_key), encrypt_secret(api_secret),
                encrypt_secret(extra), now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_broker_credentials(user_id, broker):
    """
    Returns {"environment", "api_key", "api_secret", "extra"} decrypted,
    or None if this user has no saved credentials for this broker.
    """
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT environment, api_key_encrypted, api_secret_encrypted, extra_encrypted "
            "FROM user_broker_credentials WHERE user_id = ? AND broker = ?",
            (user_id, broker.upper()),
        ).fetchone()
        if not row:
            return None
        environment, api_key_enc, api_secret_enc, extra_enc = row
        return {
            "environment": environment,
            "api_key": decrypt_secret(api_key_enc),
            "api_secret": decrypt_secret(api_secret_enc),
            "extra": decrypt_secret(extra_enc),
        }
    finally:
        conn.close()


def list_all_users_admin_view():
    """
    Every user account (active or deactivated), joined with their
    trading_paused flag, for the platform admin view
    (engines/saas_admin_engine.py). Deliberately separate from
    list_active_users() below -- that one is a plain list of user_ids
    for the scheduler to iterate; this one is a richer, read-only
    summary meant for a human to look at, and intentionally includes
    inactive accounts too (an admin should be able to see the whole
    user base, not just who's currently live). Never includes broker
    credentials or any encrypted/hashed field -- just account metadata.
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT u.user_id, u.email, u.created_at, u.is_active,
                   COALESCE(s.trading_paused, 0), u.billing_status
            FROM users u
            LEFT JOIN user_settings s ON u.user_id = s.user_id
            ORDER BY u.created_at DESC
            """
        ).fetchall()
        return [
            {
                "user_id": r[0],
                "email": r[1],
                "created_at": r[2],
                "is_active": bool(r[3]),
                "trading_paused": bool(r[4]),
                "billing_status": r[5],
            }
            for r in rows
        ]
    finally:
        conn.close()


def list_active_users():
    """
    All active user_ids (is_active=1), for the background scheduler
    (saas_scheduler.py) to iterate over -- one decision-loop run per
    user, per tick. Deliberately doesn't filter by trading_paused here;
    saas_decision_engine.run_decision_loop_for_user() already checks
    that per user (and still needs to run for a paused user anyway, so
    exit protection on their existing positions keeps working).
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT user_id FROM users WHERE is_active = 1"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def list_connected_brokers(user_id):
    """Broker names this user has saved credentials for, no secrets included."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT broker, environment, updated_at FROM user_broker_credentials WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return [
            {"broker": r[0], "environment": r[1], "updated_at": r[2]}
            for r in rows
        ]
    finally:
        conn.close()


# ============================================================
# USER SETTINGS
# ============================================================

def get_user_settings(user_id):
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT max_position_size, enabled_asset_classes, allow_live_trading, trading_paused "
            "FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        import json
        return {
            "max_position_size": row[0],
            "enabled_asset_classes": json.loads(row[1]),
            "allow_live_trading": bool(row[2]),
            "trading_paused": bool(row[3]),
        }
    finally:
        conn.close()


def save_user_settings(
    user_id, max_position_size=None, enabled_asset_classes=None, trading_paused=None
):
    """
    Updates a user's own settings. allow_live_trading is deliberately
    NOT a parameter here -- flipping paper/demo to real money is not
    meant to be a self-service settings toggle at this stage (see
    module docstring: paper/demo-only at launch). If/when that changes,
    it should be its own explicit, audited action, not folded into a
    general settings update.

    trading_paused (added 2026-08-26) is this user's own kill switch --
    when True, engines/saas_decision_engine.py skips evaluating any new
    BUY signals for this user entirely, while still running exit
    protection (stop-loss/take-profit/time-exit) on positions they
    already hold. Mirrors the single-owner bot's EXECUTION_KILL_SWITCH
    semantics (blocks new entries, never blocks protective exits) --
    see that constant's usage in app.py for why exits are deliberately
    exempt.
    """
    import json
    conn = _get_connection()
    try:
        existing = get_user_settings(user_id)
        if existing is None:
            return False

        new_max_position_size = (
            max_position_size if max_position_size is not None
            else existing["max_position_size"]
        )
        new_enabled_classes = (
            json.dumps(enabled_asset_classes) if enabled_asset_classes is not None
            else json.dumps(existing["enabled_asset_classes"])
        )
        new_trading_paused = (
            int(bool(trading_paused)) if trading_paused is not None
            else int(existing["trading_paused"])
        )
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            "UPDATE user_settings SET max_position_size = ?, enabled_asset_classes = ?, "
            "trading_paused = ?, updated_at = ? WHERE user_id = ?",
            (new_max_position_size, new_enabled_classes, new_trading_paused, now, user_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ============================================================
# PASSWORD RESET (added 2026-08-28, engines/email_engine.py sends the
# actual email via Resend; this file only manages the tokens)
# ============================================================

_RESET_TOKEN_LIFETIME = timedelta(hours=1)
_VERIFY_TOKEN_LIFETIME = timedelta(days=3)


def create_password_reset_token(email):
    """
    Creates a one-hour, single-use reset token for this email and
    returns it, or returns None if no active account matches. The
    caller (saas_app.py) MUST show the same "if that email is
    registered, we've sent a link" message either way -- returning
    None vs a token here is a signal for internal logic only, never
    for the UI to branch its message on. Branching the visible message
    would let anyone probe which emails are registered (account
    enumeration), which the login flow already deliberately avoids
    (see authenticate_user()'s docstring for the same reasoning).
    """
    email = str(email).strip().lower()
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT user_id FROM users WHERE email = ? AND is_active = 1", (email,)
        ).fetchone()
        if not row:
            return None
        user_id = row[0]
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + _RESET_TOKEN_LIFETIME
        conn.execute(
            "INSERT INTO password_reset_tokens (token, user_id, created_at, expires_at, used) "
            "VALUES (?, ?, ?, ?, 0)",
            (token, user_id, now.isoformat(), expires_at.isoformat()),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def _get_valid_token_user_id(table, token):
    """Shared lookup for both token tables -- not used across process
    boundaries in a security-sensitive way, just avoids duplicating the
    same expiry/used-check logic twice."""
    conn = _get_connection()
    try:
        row = conn.execute(
            f"SELECT user_id, expires_at, used FROM {table} WHERE token = ?", (token,)
        ).fetchone()
        if not row:
            return None
        user_id, expires_at, used = row
        if used:
            return None
        if datetime.now(timezone.utc) > datetime.fromisoformat(expires_at):
            return None
        return user_id
    finally:
        conn.close()


def verify_password_reset_token(token):
    """Returns the user_id this token belongs to if it's valid and unused, else None.
    Does NOT mark it used -- reset_password() does that atomically with the actual
    password change, so a token that's merely been looked at (e.g. the reset page
    loading to show the form) doesn't get burned before the user submits."""
    return _get_valid_token_user_id("password_reset_tokens", token)


def reset_password(token, new_password):
    """Consumes the token and sets the new password in one step. Returns
    False (and changes nothing) if the token is missing, expired, or
    already used."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, expires_at, used FROM password_reset_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return False
        user_id, expires_at, used = row
        if used or datetime.now(timezone.utc) > datetime.fromisoformat(expires_at):
            return False

        password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE user_id = ?", (password_hash, user_id)
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used = 1 WHERE token = ?", (token,)
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ============================================================
# EMAIL VERIFICATION (added 2026-08-28)
# ============================================================

def create_email_verification_token(user_id):
    """
    Three-day, single-use token. Called both right after signup and
    from a "Resend verification email" button, so this can be called
    repeatedly for the same user -- each call is a fresh independent
    token; old unused ones for the same user are simply left to expire
    on their own rather than being explicitly revoked (not worth the
    extra query, and a stale reset link failing silently is fine).
    """
    conn = _get_connection()
    try:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + _VERIFY_TOKEN_LIFETIME
        conn.execute(
            "INSERT INTO email_verification_tokens (token, user_id, created_at, expires_at, used) "
            "VALUES (?, ?, ?, ?, 0)",
            (token, user_id, now.isoformat(), expires_at.isoformat()),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def verify_email_token(token):
    """Consumes the token and marks the account's email_verified=1.
    Returns False (changes nothing) if missing/expired/already used."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, expires_at, used FROM email_verification_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return False
        user_id, expires_at, used = row
        if used or datetime.now(timezone.utc) > datetime.fromisoformat(expires_at):
            return False

        conn.execute(
            "UPDATE users SET email_verified = 1 WHERE user_id = ?", (user_id,)
        )
        conn.execute(
            "UPDATE email_verification_tokens SET used = 1 WHERE token = ?", (token,)
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ============================================================
# BILLING (added 2026-08-28, engines/billing_engine.py creates Stripe
# Checkout/Portal sessions; saas_webhook_server.py -- a separate
# process, see that file's docstring for why -- is the ONLY thing that
# calls the two update functions below, driven entirely by Stripe
# webhook events. Nothing in saas_app.py ever sets billing_status
# directly: Stripe is the single source of truth for whether someone
# is actually paying, and this table just mirrors it.
# ============================================================

def link_stripe_customer(user_id, stripe_customer_id, stripe_subscription_id):
    """
    Called from the webhook handler when checkout.session.completed
    fires -- ties this user's account to the Stripe customer/
    subscription Stripe just created, and marks them 'trialing'
    immediately (every Checkout Session this platform creates includes
    a 7-day trial, so a just-completed session is trialing by
    definition). The subscription's own customer.subscription.updated
    webhook -- which Stripe sends around the same time -- is what
    keeps this in sync from here on as the subscription's real status
    changes (active, past_due, canceled, ...).
    """
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE users SET stripe_customer_id = ?, stripe_subscription_id = ?, "
            "billing_status = 'trialing' WHERE user_id = ?",
            (stripe_customer_id, stripe_subscription_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_billing_status_by_customer(stripe_customer_id, status, stripe_subscription_id=None):
    """
    Called from the webhook handler for subscription lifecycle events
    (customer.subscription.updated/deleted, invoice.payment_failed),
    which reference the Stripe customer rather than our own user_id --
    looks the user up by their previously-stored stripe_customer_id.
    A no-op (not an error) if no user matches: Stripe can send events
    for customers/subscriptions this platform doesn't recognize (test
    events, a customer created directly in the Stripe dashboard, etc),
    and silently ignoring those is correct, not a bug to surface.
    """
    conn = _get_connection()
    try:
        if stripe_subscription_id:
            conn.execute(
                "UPDATE users SET billing_status = ?, stripe_subscription_id = ? "
                "WHERE stripe_customer_id = ?",
                (status, stripe_subscription_id, stripe_customer_id),
            )
        else:
            conn.execute(
                "UPDATE users SET billing_status = ? WHERE stripe_customer_id = ?",
                (status, stripe_customer_id),
            )
        conn.commit()
    finally:
        conn.close()


def get_billing_info(user_id):
    """Returns {"billing_status", "stripe_customer_id", "stripe_subscription_id"}
    for this user, or None if the user doesn't exist. billing_status is
    'none' until they complete Stripe Checkout at least once."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT billing_status, stripe_customer_id, stripe_subscription_id "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "billing_status": row[0],
            "stripe_customer_id": row[1],
            "stripe_subscription_id": row[2],
        }
    finally:
        conn.close()
