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

FIX 2026-09-03 (post-launch-audit Moderate finding): key rotation.
Encryption now goes through cryptography's MultiFernet (see
_get_fernet() below) instead of a single bare Fernet instance --
SAAS_ENCRYPTION_KEY is always the CURRENT key (used for all new
encryption), and an optional SAAS_ENCRYPTION_KEY_PREVIOUS
(comma-separated) holds retired keys that are still accepted for
decrypting rows that haven't been migrated yet. Previously there was
no way to rotate this key at all -- changing SAAS_ENCRYPTION_KEY would
have instantly made every already-stored credential permanently
undecryptable, which meant the key could never actually be rotated in
practice (e.g. after a suspected leak) without forcing every user to
re-enter their broker credentials. See reencrypt_all_credentials()
below for the migration step that completes a rotation.
"""

import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from dotenv import load_dotenv

# Loaded here directly (not just relied on transitively via some other
# module) since this file reads SAAS_ENCRYPTION_KEY from the environment
# itself and shouldn't depend on import order elsewhere.
load_dotenv()

SAAS_DB_NAME = "saas_platform.db"

# FIX 2026-09-02 (post-launch-audit): moved here from saas_app.py's own
# module-level _ADMIN_EMAILS/_is_admin() so the SAME admin definition can
# be used by engines/saas_decision_engine.py's billing gate below --
# previously "is this user exempt from the billing gate" only existed in
# the Streamlit UI layer, which the background scheduler never runs
# through. saas_app.py's _is_admin() now delegates here instead of
# keeping its own separate copy, so there is exactly one definition of
# "admin" platform-wide. Empty by default (no ADMIN_EMAILS set means no
# one is admin), fail-closed rather than fail-open. Set in .env, e.g.
# ADMIN_EMAILS=you@example.com
_ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "").split(",")
    if e.strip()
}


def is_admin_email(email):
    return bool(email) and email.strip().lower() in _ADMIN_EMAILS


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

    # Migration for databases created before 2026-08-29 (phone/country
    # didn't exist yet). Both are plain optional profile fields -- no
    # SMS verification tied to phone, no billing/compliance logic tied
    # to country. NULL by default rather than empty string so "never
    # set" stays distinguishable from "set, then cleared" if that ever
    # matters later.
    try:
        conn.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE users ADD COLUMN country TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migration for databases created before 2026-09-03 (no login
    # brute-force protection -- post-launch-audit Moderate finding).
    # failed_login_attempts counts consecutive wrong-password attempts
    # for an EXISTING account only (there's no row to increment for an
    # unknown email, so this can't be used to enumerate accounts any
    # more than authenticate_user() already allows -- see its
    # docstring). locked_until is NULL until the threshold is hit, then
    # holds an ISO8601 UTC timestamp; both reset to their defaults on a
    # successful login or once the lock naturally expires.
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE users ADD COLUMN locked_until TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migration for databases created before 2026-09-07 (multi-language
    # UI support -- see engines/saas_i18n.py). NULL means "never chosen",
    # which saas_app.py treats as English (DEFAULT_LANGUAGE), not a
    # separate "unset" state that needs its own handling anywhere else.
    try:
        conn.execute("ALTER TABLE users ADD COLUMN language TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Migration for databases created before 2026-09-15 (card-optional
    # trial redesign -- see CARD-OPTIONAL TRIAL section below). Every
    # signup used to be forced through Stripe Checkout (card required)
    # before ever seeing the dashboard -- billing_status stayed 'none'
    # until that completed. trial_ends_at is this platform's OWN record
    # of when a card-optional trial runs out, independent of Stripe,
    # since Stripe knows nothing about a trial no subscription has been
    # created for yet. NULL for every pre-migration account (they either
    # already have a real Stripe subscription, in which case this column
    # is simply never consulted -- see billing_status precedence notes
    # below -- or they're stuck at billing_status='none' and will see
    # the same "start your trial" gate as before, now card-optional).
    try:
        conn.execute("ALTER TABLE users ADD COLUMN trial_ends_at TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    # Sent-once flag for the "your trial ends in 3 days" email -- separate
    # from trial_ends_at itself so expire_stale_trials() below can be
    # called as often as convenient (every scheduler tick) without ever
    # re-sending the reminder.
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN trial_reminder_sent INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass  # column already exists
    # Referral system (task requested 2026-09-15, see REFERRALS section
    # below). referral_code is this user's own shareable code -- assigned
    # once, at signup, never reused across accounts (UNIQUE). referred_by
    # records which code THIS account signed up with, if any -- write-once,
    # NULL if they signed up without a code or the code didn't match
    # anyone. Kept as two plain columns on users rather than a separate
    # join table: the relationship is exactly one referrer per account,
    # decided once at signup and never changed, so there's no many-to-many
    # shape here that would justify a separate table.
    try:
        conn.execute("ALTER TABLE users ADD COLUMN referral_code TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE users ADD COLUMN referred_by_code TEXT")
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
    # Added 2026-08-29 for the "change email" account setting. Deliberately
    # a separate table from email_verification_tokens even though the
    # shape is almost identical: this one carries new_email as its own
    # column (the address being proposed hasn't been written to users.email
    # yet -- see request_email_change()'s docstring for why), and mixing
    # the two would make it easy to accidentally verify the wrong thing.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_change_tokens (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            new_email TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    # Added 2026-09-01 to fix users being signed out every time they take
    # a full round trip to Stripe (Checkout or the Billing Portal) and
    # back. Streamlit's login state lives only in st.session_state, which
    # is tied to the browser tab's live WebSocket connection -- a full
    # top-level navigation away to checkout.stripe.com/billing.stripe.com
    # and back tears that connection down and silently wipes it, even
    # though the person never explicitly logged out. This table backs a
    # long-lived "remember me" browser cookie (see saas_app.py's
    # _ISSUE_SESSION_COOKIE/_read_session_cookie) that survives that trip:
    # an opaque random token, same pattern as the other token tables
    # above, rather than a signed/stateless cookie -- so a session can
    # still be individually revoked (logout deletes its row) without
    # needing a separate signing secret in the environment.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS login_sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    # FIX 2026-09-02 (post-launch-audit CRITICAL finding): see
    # acquire_execution_lock()/release_execution_lock() below for why
    # this exists -- one row per user_id currently mid-execution.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_locks (
            user_id TEXT PRIMARY KEY,
            acquired_at TEXT NOT NULL
        )
    """)
    # Added 2026-09-08 for the live-trading switch (real-money rollout):
    # every time a user's allow_live_trading flag changes, one row goes
    # here -- separate from user_settings itself, which only ever holds
    # CURRENT state, not history. This table is the audit trail proving
    # when/why real-money trading was turned on or off for a given
    # account. save_user_settings() deliberately cannot touch
    # allow_live_trading (see that function's docstring), so
    # set_live_trading_status() below is the only code path that writes
    # here, and it always updates user_settings and inserts this row in
    # the same transaction -- the two can never desync.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS live_trading_audit_log (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            allow_live_trading INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    return conn


# ============================================================
# EXECUTION LOCK (added 2026-09-02, post-launch-audit CRITICAL finding):
# saas_scheduler.py's background tick and saas_app.py's manual "Execute
# These Trades" button both end up calling engines/saas_decision_engine.py's
# run_decision_loop_for_user(user_id, dry_run=False) for the SAME user_id
# from two ENTIRELY SEPARATE OS processes (the scheduler and the
# Streamlit dashboard), with no coordination between them. If both land
# on the same user around the same moment, both can pass the position-
# cap/cooldown checks before either writes an order -- a real duplicated
# position with doubled exposure, found live in the pre-launch audit.
#
# This is a CROSS-PROCESS lock -- an in-memory threading.Lock would not
# help here at all, since the scheduler and the dashboard are different
# processes. Implemented as a row in this same SQLite DB, which both
# processes already share: the row's PRIMARY KEY constraint is the real
# atomic gate (a second INSERT for the same user_id fails immediately,
# guaranteed by SQLite itself, not by any check-then-act logic in this
# Python code). Self-healing against a crashed lock holder (a Streamlit
# worker killed mid-execution, a scheduler tick OOM-killed, etc.) via a
# staleness timeout, rather than needing a separate cleanup process to
# ever run -- see _EXECUTION_LOCK_STALE_AFTER_SECONDS.
#
# run_decision_loop_for_user() acquires this for its ENTIRE run (both
# dry_run=True and dry_run=False) rather than only around the actual
# order-placement calls -- simpler to reason about ("only one call in
# flight per user_id, period") than trying to lock just the minimal
# critical section, and the position-cap/cooldown checks that create the
# race are spread across the function, not confined to one spot.
# ============================================================

_EXECUTION_LOCK_STALE_AFTER_SECONDS = 600  # generous vs. any single
# user's realistic run duration (seconds, per this project's own
# scheduler-timing findings) -- long enough that a genuinely-still-
# running execution is never falsely preempted by a concurrent caller,
# short enough that a crashed holder doesn't block a user indefinitely.


def acquire_execution_lock(user_id):
    """
    Attempts to acquire this user's execution lock. Returns True if
    acquired -- the caller now owns it and MUST call
    release_execution_lock(user_id) in a finally block, no matter how
    the run ends. Returns False if someone else already holds a
    non-stale lock for this user_id -- the caller must skip this
    execution entirely (not proceed, not retry inline) -- see the
    module section docstring above for why this exists.
    """
    conn = _get_connection()
    try:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()

        def _try_insert():
            try:
                conn.execute(
                    "INSERT INTO execution_locks (user_id, acquired_at) VALUES (?, ?)",
                    (user_id, now),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

        if _try_insert():
            return True

        # A lock row already exists. If it's stale (a previous holder
        # crashed without releasing it), reclaim it -- but only if it's
        # STILL the exact stale row we just read (the DELETE's WHERE
        # clause guards against a second process racing to reclaim the
        # same stale lock at the same instant; only one DELETE can
        # actually remove a row with a matching acquired_at).
        row = conn.execute(
            "SELECT acquired_at FROM execution_locks WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            # Released between our failed INSERT and this SELECT -- one retry.
            return _try_insert()

        age_seconds = (now_dt - datetime.fromisoformat(row[0])).total_seconds()
        if age_seconds < _EXECUTION_LOCK_STALE_AFTER_SECONDS:
            return False

        cur = conn.execute(
            "DELETE FROM execution_locks WHERE user_id = ? AND acquired_at = ?",
            (user_id, row[0]),
        )
        if cur.rowcount == 0:
            # Someone else already reclaimed/released/refreshed it first.
            return False
        conn.commit()
        return _try_insert()
    finally:
        conn.close()


def release_execution_lock(user_id):
    """Always safe to call even if the lock was never actually held by
    this caller (e.g. acquire_execution_lock() returned False) -- a
    plain DELETE, not an error if no matching row exists."""
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM execution_locks WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# ============================================================
# ENCRYPTION -- broker credentials only. Never used for passwords.
# ============================================================

def _get_fernet():
    """
    Loads SAAS_ENCRYPTION_KEY (the CURRENT key -- used for all new
    encryption) plus the optional, comma-separated
    SAAS_ENCRYPTION_KEY_PREVIOUS (retired keys still accepted when
    decrypting rows that predate the most recent rotation), and
    returns a MultiFernet over all of them. Raises a clear error rather
    than silently falling back to some default key -- a default/
    hardcoded encryption key would defeat the entire point of
    encrypting other users' broker secrets in the first place.

    HOW TO ROTATE SAAS_ENCRYPTION_KEY:
      1. Generate a new key (same command as below).
      2. Move the CURRENT SAAS_ENCRYPTION_KEY value into
         SAAS_ENCRYPTION_KEY_PREVIOUS (comma-separate if it already has
         older keys from a prior rotation you haven't cleaned up yet).
      3. Set SAAS_ENCRYPTION_KEY to the new key.
      4. Restart every process that imports this module (ordertrade-ai,
         saas-app, saas-scheduler, saas-webhook).
      5. Run reencrypt_all_credentials() (below) once, live -- this
         re-writes every stored credential using the new current key.
      6. Once that completes with zero failures, SAAS_ENCRYPTION_KEY_PREVIOUS
         can be cleared (the old key is no longer needed by anything).
    """
    primary_key = os.environ.get("SAAS_ENCRYPTION_KEY")
    if not primary_key:
        raise RuntimeError(
            "SAAS_ENCRYPTION_KEY is not set. Generate one with "
            "`python3 -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and add it to "
            ".env (never commit it, never reuse it across environments)."
        )
    previous_keys = [
        k.strip()
        for k in os.environ.get("SAAS_ENCRYPTION_KEY_PREVIOUS", "").split(",")
        if k.strip()
    ]
    all_keys = [primary_key] + previous_keys
    try:
        return MultiFernet(
            [Fernet(k.encode() if isinstance(k, str) else k) for k in all_keys]
        )
    except ValueError as e:
        raise RuntimeError(
            f"Invalid Fernet key in SAAS_ENCRYPTION_KEY or "
            f"SAAS_ENCRYPTION_KEY_PREVIOUS: {e}"
        )


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
        # Doesn't match SAAS_ENCRYPTION_KEY or any key listed in
        # SAAS_ENCRYPTION_KEY_PREVIOUS, or corrupted data -- never
        # guess, never return a partial/garbled secret to a broker API
        # call.
        raise RuntimeError(
            "Could not decrypt stored credential -- it may have been "
            "encrypted with a key no longer in SAAS_ENCRYPTION_KEY or "
            "SAAS_ENCRYPTION_KEY_PREVIOUS. Re-enter broker credentials "
            "for this user, or add the missing key back to "
            "SAAS_ENCRYPTION_KEY_PREVIOUS temporarily and run "
            "reencrypt_all_credentials()."
        )


def reencrypt_all_credentials():
    """
    Key-rotation migration helper: re-encrypts every stored broker
    credential field (api_key_encrypted, api_secret_encrypted,
    extra_encrypted) onto the CURRENT SAAS_ENCRYPTION_KEY. Decryption
    tries every key in SAAS_ENCRYPTION_KEY + SAAS_ENCRYPTION_KEY_PREVIOUS
    (via _get_fernet()'s MultiFernet), so this works whether a given
    row is still on an old key or already on the current one -- run it
    once after rotating (see _get_fernet()'s docstring for the full
    steps) and every row ends up back on a single key.

    Safe to run more than once -- a row already on the current key is
    just re-encrypted with the same key again (fresh ciphertext, same
    plaintext). A row that fails to decrypt with ANY known key is left
    completely untouched (never corrupted, never silently dropped) and
    counted in the returned failed total instead.

    Returns (migrated_count, failed_count).
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT id, api_key_encrypted, api_secret_encrypted, extra_encrypted "
            "FROM user_broker_credentials"
        ).fetchall()
        migrated = 0
        failed = 0
        for row_id, api_key_enc, api_secret_enc, extra_enc in rows:
            try:
                new_api_key = (
                    encrypt_secret(decrypt_secret(api_key_enc)) if api_key_enc else None
                )
                new_api_secret = (
                    encrypt_secret(decrypt_secret(api_secret_enc)) if api_secret_enc else None
                )
                new_extra = (
                    encrypt_secret(decrypt_secret(extra_enc)) if extra_enc else None
                )
            except RuntimeError as e:
                failed += 1
                print(f"   Row {row_id}: could not decrypt with any known key -- "
                      f"left untouched. ({e})")
                continue
            conn.execute(
                "UPDATE user_broker_credentials SET api_key_encrypted = ?, "
                "api_secret_encrypted = ?, extra_encrypted = ? WHERE id = ?",
                (new_api_key, new_api_secret, new_extra, row_id),
            )
            migrated += 1
        conn.commit()
        return migrated, failed
    finally:
        conn.close()


# ============================================================
# USERS
# ============================================================

def create_user(email, password, phone=None, country=None, referred_by_code=None):
    """
    Creates a new user account. Returns the new user_id, or None if the
    email is already registered (case-insensitive -- emails are
    normalized to lowercase before the uniqueness check and storage).

    phone and country (added 2026-08-29) are both optional, plain
    profile fields -- no SMS verification tied to phone, no billing/
    compliance logic tied to country. Blank strings are stored as NULL
    rather than "" so an unset field reads the same whether it came
    from signup or an old pre-migration account.

    FIX 2026-09-15 (card-optional trial redesign): every new account now
    starts billing_status='trialing' with trial_ends_at = now + TRIAL_
    LENGTH_DAYS, set directly here -- no Stripe Checkout involved at all.
    Previously billing_status defaulted to 'none' and the very next thing
    a new user saw was render_billing_gate() demanding a card before they
    could see the dashboard (see saas_app.py's render_dashboard() gate),
    which is exactly the signup friction real prospective users pushed
    back on. render_dashboard()'s gate already treats 'trialing' as full
    access, so simply granting it here -- with no stripe_customer_id/
    stripe_subscription_id yet -- is enough to drop a brand-new user
    straight into the working product. See expire_stale_trials() below
    for what happens when trial_ends_at passes with no card added, and
    engines/billing_engine.py for how a user who DOES want to add a card
    early gets a Checkout session that honors whatever's left of this
    same trial_ends_at rather than resetting the clock.

    ADDED 2026-09-15 (referral system): every account gets its OWN
    referral_code generated here, whether or not they arrived via one --
    everyone has something to share from day one, not just people who
    were themselves referred. referred_by_code (optional) is whatever
    code the SIGNUP FORM'S referral field held -- looked up via
    get_user_by_referral_code() below; an unrecognized/blank code is
    silently treated as "no referral" rather than blocking signup over a
    typo. A valid code does two things: this new account's own trial
    starts at TRIAL_LENGTH_DAYS + REFERRAL_BONUS_DAYS instead of the
    plain length, and apply_referral_bonus() credits the REFERRER the
    same bonus days on their own trial (see that function for what
    happens if the referrer has already converted to a paying
    subscriber, or already let their trial expire).
    """
    email = str(email).strip().lower()
    phone = phone.strip() if phone and phone.strip() else None
    country = country.strip() if country and country.strip() else None
    conn = _get_connection()
    try:
        existing = conn.execute(
            "SELECT user_id FROM users WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            return None

        referrer_user_id = None
        referred_by_code = str(referred_by_code).strip().upper() if referred_by_code else None
        if referred_by_code:
            referrer_row = conn.execute(
                "SELECT user_id FROM users WHERE referral_code = ?", (referred_by_code,)
            ).fetchone()
            if referrer_row:
                referrer_user_id = referrer_row[0]
            else:
                # Unrecognized code -- don't fail signup over a typo or a
                # stale/shared link; just don't apply a bonus either side.
                referred_by_code = None

        user_id = str(uuid.uuid4())
        password_hash = bcrypt.hashpw(
            password.encode(), bcrypt.gensalt()
        ).decode()
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        trial_days = TRIAL_LENGTH_DAYS + (REFERRAL_BONUS_DAYS if referrer_user_id else 0)
        trial_ends_at = (now_dt + timedelta(days=trial_days)).isoformat()
        own_referral_code = _generate_unique_referral_code(conn)

        conn.execute(
            "INSERT INTO users (user_id, email, password_hash, created_at, is_active, "
            "phone, country, billing_status, trial_ends_at, referral_code, referred_by_code) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, 'trialing', ?, ?, ?)",
            (
                user_id, email, password_hash, now, phone, country, trial_ends_at,
                own_referral_code, referred_by_code,
            ),
        )
        # Every new user starts with paper/demo-only enforced -- see
        # allow_live_trading default and module docstring.
        conn.execute(
            "INSERT INTO user_settings (user_id, max_position_size, enabled_asset_classes, "
            "allow_live_trading, created_at, updated_at) VALUES (?, 0.20, '[]', 0, ?, ?)",
            (user_id, now, now),
        )
        conn.commit()

        if referrer_user_id:
            # Deliberately AFTER commit and on the same connection is fine
            # here -- apply_referral_bonus() opens its own connection and
            # is safe to fail independently of the signup itself (a
            # referral-credit bug should never be able to block someone
            # from creating an account).
            try:
                apply_referral_bonus(referrer_user_id)
            except Exception:
                print(f"[referrals] apply_referral_bonus failed for referrer={referrer_user_id}:")
                import traceback
                traceback.print_exc()

        return user_id
    finally:
        conn.close()


# Brute-force lockout params for authenticate_user() below (post-launch-
# audit Moderate finding: login had no rate-limiting/lockout at all).
_LOGIN_LOCKOUT_THRESHOLD = 5
_LOGIN_LOCKOUT_MINUTES = 15


def authenticate_user(email, password):
    """
    Returns the user_id if email/password match an active, non-locked-
    out account, otherwise None. Deliberately returns the same "None"
    for "no such email", "wrong password", AND "account temporarily
    locked out" -- distinguishing any of these in the response would
    let an attacker enumerate registered emails (this is the same
    anti-enumeration reasoning as before, just extended to cover the
    new lockout state too).

    Brute-force protection: after _LOGIN_LOCKOUT_THRESHOLD consecutive
    wrong-password attempts against an EXISTING account, that account
    is locked for _LOGIN_LOCKOUT_MINUTES minutes -- further attempts
    return None even with the correct password until the lock expires.
    Failed-attempt tracking only happens for accounts that exist
    (there's no row to increment for an unknown email), so an attacker
    probing random emails never triggers a lock either way.
    """
    email = str(email).strip().lower()
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, password_hash, is_active, failed_login_attempts, "
            "locked_until FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if not row:
            return None
        user_id, password_hash, is_active, failed_attempts, locked_until = row
        if not is_active:
            return None

        now = datetime.now(timezone.utc)
        if locked_until:
            locked_until_dt = datetime.fromisoformat(locked_until)
            if now < locked_until_dt:
                return None  # still locked out -- don't even check the password
            # Lock has expired: give the account a fresh attempt window
            # rather than leaving a stale counter hanging around.
            failed_attempts = 0
            conn.execute(
                "UPDATE users SET failed_login_attempts = 0, locked_until = NULL "
                "WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()

        if bcrypt.checkpw(password.encode(), password_hash.encode()):
            if failed_attempts:
                conn.execute(
                    "UPDATE users SET failed_login_attempts = 0, locked_until = NULL "
                    "WHERE user_id = ?",
                    (user_id,),
                )
                conn.commit()
            return user_id

        # Wrong password -- bump the counter, lock if threshold reached.
        failed_attempts += 1
        if failed_attempts >= _LOGIN_LOCKOUT_THRESHOLD:
            lock_until = (
                now + timedelta(minutes=_LOGIN_LOCKOUT_MINUTES)
            ).isoformat()
            conn.execute(
                "UPDATE users SET failed_login_attempts = ?, locked_until = ? "
                "WHERE user_id = ?",
                (failed_attempts, lock_until, user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET failed_login_attempts = ? WHERE user_id = ?",
                (failed_attempts, user_id),
            )
        conn.commit()
        return None
    finally:
        conn.close()


def get_user(user_id):
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, email, created_at, is_active, email_verified, phone, country, language "
            "FROM users WHERE user_id = ?",
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
            "phone": row[5],
            "country": row[6],
            "language": row[7],
        }
    finally:
        conn.close()


def set_user_language(user_id, language):
    """
    Updates the display-language preference for an existing account.
    `language` must be one of engines.saas_i18n.SUPPORTED_LANGUAGES'
    keys -- validated by the caller (saas_app.py), not here, to keep
    this module from needing to import the UI-layer i18n module.
    """
    conn = _get_connection()
    try:
        cur = conn.execute(
            "UPDATE users SET language = ? WHERE user_id = ?",
            (language, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_profile_fields(user_id, phone=None, country=None):
    """
    Updates phone and/or country for an existing account. Pass None for
    a field to leave it unchanged (not to clear it) -- to explicitly
    clear a field, pass an empty string, which is normalized to NULL
    same as create_user() does. Both fields are plain profile data with
    no verification or compliance logic attached (see module notes on
    the 2026-08-29 migration above _get_connection()).
    """
    conn = _get_connection()
    try:
        existing = conn.execute(
            "SELECT phone, country FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not existing:
            return False
        new_phone = (
            (phone.strip() or None) if phone is not None else existing[0]
        )
        new_country = (
            (country.strip() or None) if country is not None else existing[1]
        )
        conn.execute(
            "UPDATE users SET phone = ?, country = ? WHERE user_id = ?",
            (new_phone, new_country, user_id),
        )
        conn.commit()
        return True
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


def delete_broker_credentials(user_id, broker):
    """
    Removes this user's saved credentials for one broker entirely.

    Added 2026-09-03 alongside the credential-validation-at-save-time
    fix (post-launch-audit Moderate finding) -- saas_app.py's save flow
    now test-connects immediately after saving, and calls this to roll
    back to "not connected" if that user never had working credentials
    for this broker before (i.e. this was their first attempt and it
    failed) rather than leaving known-bad credentials sitting in the
    database where the scheduler would keep retrying them every tick.
    """
    conn = _get_connection()
    try:
        conn.execute(
            "DELETE FROM user_broker_credentials WHERE user_id = ? AND broker = ?",
            (user_id, broker.upper()),
        )
        conn.commit()
    finally:
        conn.close()


def update_broker_environment(user_id, broker, environment):
    """
    Updates ONLY the stored environment label for an existing credential
    row -- added 2026-09-08 (task #305) for mt_broker.py, which cannot
    know whether a connected MT4/5 account is demo or real until it
    actually connects and reads MetaApi's own account_information.type
    (there is no user-chosen environment flag for MT4/5 the way there is
    for Alpaca/Binance/eToro -- see mt_broker.py's module docstring LIVE
    TRADING GATE section). Deliberately does NOT touch api_key_encrypted/
    api_secret_encrypted/extra_encrypted -- unlike save_broker_credentials()
    above, which would need those re-supplied or it overwrites them with
    None. This is purely a display-label correction; it is NEVER
    consulted for the actual live/demo execution decision (mt_broker.py
    re-derives that fresh from MetaApi on every single trade attempt, not
    from this stored value) -- a stale or not-yet-corrected label here
    can never cause a real order to be misrouted, only a momentarily
    inaccurate status line in the UI. No-op if this user has no saved
    credential row for this broker yet.
    """
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE user_broker_credentials SET environment = ?, updated_at = ? "
            "WHERE user_id = ? AND broker = ?",
            (environment, datetime.now(timezone.utc).isoformat(), user_id, broker.upper()),
        )
        conn.commit()
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


def set_live_trading_status(user_id, allow_live_trading, reason=""):
    """
    The ONLY code path allowed to change user_settings.allow_live_trading
    -- see save_user_settings()'s docstring just above for why that
    function deliberately excludes this column as a parameter. Updates
    user_settings and inserts a row into live_trading_audit_log in the
    SAME transaction, so the audit trail can never desync from the
    actual flag (either both commit or neither does).

    Called from exactly one place: the explicit "Switch to live
    trading" / "Switch back to demo" confirmation flow in saas_app.py,
    after the user has stepped through the risk-acknowledgment
    checklist and typed the confirmation phrase -- never from a plain
    settings form, never automatically.

    Flipping this to True does NOT by itself let any order reach a real
    broker account. That is gated separately, broker-by-broker, by the
    hardcoded paper=True / testnet / demo constants in
    engines/saas_broker_factory.py (see that file's SAFETY docstring) --
    left deliberately independent of this flag as defense-in-depth, so
    this function turning on account-level permission and a broker
    function actually routing to real money are always two separate,
    individually-reviewed changes.
    """
    conn = _get_connection()
    try:
        existing = get_user_settings(user_id)
        if existing is None:
            return False
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE user_settings SET allow_live_trading = ?, updated_at = ? WHERE user_id = ?",
            (int(bool(allow_live_trading)), now, user_id),
        )
        conn.execute(
            "INSERT INTO live_trading_audit_log (id, user_id, allow_live_trading, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, int(bool(allow_live_trading)), reason, now),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_live_trading_enabled_since(user_id):
    """
    Returns the datetime this user's account most recently switched
    live trading ON, or None if they are not currently live (either
    they've never enabled it, or their latest audit-log entry is an
    OFF/revert). Added 2026-09-09 (task #306) for saas_decision_
    engine.py's live-trading position-size probation -- a temporary,
    tighter position-size ceiling for the first few days after a user
    goes live, on top of (never instead of) their own configured
    max_position_size, so an undiscovered bug or an unlucky first
    signal has a smaller blast radius while a user is still building
    confidence in real-money execution. Read-only, never touches
    allow_live_trading or the audit log itself.

    NOTE: live_trading_audit_log.created_at is written by set_live_
    trading_status() using an offset-AWARE datetime.now(timezone.utc)
    -- unlike most other timestamps in this codebase (see saas_order_
    manager.py's naive datetime.now()). Returned as-is (aware); callers
    must compare against datetime.now(timezone.utc), not a naive
    datetime.now() -- see the "can't compare offset-naive and
    offset-aware datetimes" incident in task #310 for exactly what
    goes wrong if that's not respected.
    """
    log = get_live_trading_audit_log(user_id)  # newest first
    if not log or not log[0]["allow_live_trading"]:
        return None
    try:
        return datetime.fromisoformat(log[0]["created_at"])
    except (TypeError, ValueError):
        return None


def get_live_trading_audit_log(user_id):
    """
    This user's live-trading on/off history, newest first. Read-only --
    never called from the switch flow itself, only for display (account
    settings, and the admin view in saas_app.py's admin panel).
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT allow_live_trading, reason, created_at FROM live_trading_audit_log "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [
            {"allow_live_trading": bool(r[0]), "reason": r[1], "created_at": r[2]}
            for r in rows
        ]
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
    already used.

    FIX 2026-09-03 (post-launch-audit Moderate finding): also revokes
    every existing login_sessions row for this account, so a password
    reset actually forces re-authentication everywhere -- previously an
    attacker who'd already stolen a session cookie (or an old device
    still logged in) would stay logged in indefinitely even after the
    legitimate owner reset the password. This is the whole point of a
    password reset as an incident-response tool, not just a convenience
    feature. Also clears any lockout state (see authenticate_user()'s
    _LOGIN_LOCKOUT_THRESHOLD) -- a successful reset proves ownership, so
    there's no reason to leave the account locked out afterward.
    """
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
            "UPDATE users SET password_hash = ?, failed_login_attempts = 0, "
            "locked_until = NULL WHERE user_id = ?",
            (password_hash, user_id),
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used = 1 WHERE token = ?", (token,)
        )
        conn.execute("DELETE FROM login_sessions WHERE user_id = ?", (user_id,))
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
# CHANGE EMAIL (added 2026-08-29). Deliberately a request/confirm split
# rather than updating users.email immediately: the new address hasn't
# been proven reachable yet, and this account's whole login identity is
# its email (see users.email's UNIQUE NOT NULL, and authenticate_user()
# above). Writing an unverified address straight into users.email would
# risk locking the account owner out if they mistyped it -- old email
# stays the login identity, and Stripe's own customer_email on file,
# until the new one is actually confirmed by clicking the emailed link.
# ============================================================

_EMAIL_CHANGE_TOKEN_LIFETIME = timedelta(hours=1)


def request_email_change(user_id, new_email):
    """
    Creates a one-hour, single-use token pairing this user with a
    proposed new email, and returns it -- or returns None if that
    email is already registered to a DIFFERENT active account (still
    case-insensitive, same normalization as create_user()). Doesn't
    touch users.email at all; that only happens in
    confirm_email_change() once the link is actually clicked.
    """
    new_email = str(new_email).strip().lower()
    conn = _get_connection()
    try:
        existing = conn.execute(
            "SELECT user_id FROM users WHERE email = ?", (new_email,)
        ).fetchone()
        if existing and existing[0] != user_id:
            return None

        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + _EMAIL_CHANGE_TOKEN_LIFETIME
        conn.execute(
            "INSERT INTO email_change_tokens (token, user_id, new_email, created_at, expires_at, used) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (token, user_id, new_email, now.isoformat(), expires_at.isoformat()),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def confirm_email_change(token):
    """
    Consumes the token and, if still valid, updates the account's
    email to the proposed new_email and marks it verified (clicking a
    link sent TO that address is itself proof of reachability, same
    reasoning as the ordinary signup verification flow). Returns the
    new email on success so the caller can show it, or None if the
    token is missing/expired/already used, or if the target email got
    claimed by someone else in the meantime (re-checked here, not just
    at request time).
    """
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, new_email, expires_at, used FROM email_change_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None
        user_id, new_email, expires_at, used = row
        if used or datetime.now(timezone.utc) > datetime.fromisoformat(expires_at):
            return None

        conflict = conn.execute(
            "SELECT user_id FROM users WHERE email = ?", (new_email,)
        ).fetchone()
        if conflict and conflict[0] != user_id:
            return None

        conn.execute(
            "UPDATE users SET email = ?, email_verified = 1 WHERE user_id = ?",
            (new_email, user_id),
        )
        conn.execute(
            "UPDATE email_change_tokens SET used = 1 WHERE token = ?", (token,)
        )
        conn.commit()
        return new_email
    finally:
        conn.close()


# ============================================================
# PERSISTENT LOGIN SESSIONS (added 2026-09-01) -- see login_sessions'
# table comment above in _get_connection() for the full "why". Backs a
# long-lived browser cookie so a full-page trip to Stripe and back
# doesn't sign the user out.
# ============================================================

_LOGIN_SESSION_LIFETIME = timedelta(days=30)


def create_login_session(user_id):
    """
    Issues a new 30-day session token for this user and returns it.
    Called at actual login (password or persisted-cookie restore).
    Each call is a fresh independent token -- logging in from a second
    browser doesn't invalidate the first one, same as most SaaS
    products' "signed in on 2 devices" behavior.
    """
    conn = _get_connection()
    try:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + _LOGIN_SESSION_LIFETIME
        conn.execute(
            "INSERT INTO login_sessions (token, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (token, user_id, now.isoformat(), expires_at.isoformat()),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def get_user_id_for_login_session(token):
    """Returns the user_id this session token belongs to, or None if the
    token is missing/expired/was revoked (logged out). Unlike the other
    token tables, this one is deliberately NOT single-use -- it has to
    keep working across every page load for 30 days, not just once."""
    if not token:
        return None
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, expires_at FROM login_sessions WHERE token = ?", (token,)
        ).fetchone()
        if not row:
            return None
        user_id, expires_at = row
        if datetime.now(timezone.utc) > datetime.fromisoformat(expires_at):
            return None
        return user_id
    finally:
        conn.close()


def delete_login_session(token):
    """Revokes a single session token -- called on explicit Log Out so
    the browser's cookie (even if it lingers client-side) can't silently
    log the person back in afterward."""
    if not token:
        return
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM login_sessions WHERE token = ?", (token,))
        conn.commit()
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
#
# REDESIGNED 2026-09-15 (card-optional trial): that "Stripe is the
# single source of truth" rule now has one deliberate exception --
# billing_status='trialing' with stripe_subscription_id still NULL
# means this platform granted the trial itself (see create_user()),
# and trial_ends_at (this file's own column, never Stripe's) is what
# that state's expiry is measured against. The moment a real Stripe
# subscription exists (stripe_subscription_id is set), Stripe goes
# back to being the only thing that changes billing_status, exactly as
# before -- trial_ends_at simply stops being consulted from that point
# on (see expire_stale_trials()'s WHERE clause below, which explicitly
# excludes any user with a stripe_subscription_id already on file).
# ============================================================

TRIAL_LENGTH_DAYS = 14

# Referral bonus (added 2026-09-15, see REFERRALS section below) -- days
# credited to BOTH sides of a referral: a new signup who used someone's
# code gets TRIAL_LENGTH_DAYS + REFERRAL_BONUS_DAYS (16 total right now)
# instead of the plain 14, and the referrer gets REFERRAL_BONUS_DAYS
# added to their own trial_ends_at via apply_referral_bonus(). Kept as
# its own constant rather than folded into TRIAL_LENGTH_DAYS math inline
# everywhere it's used, so the "how much is a referral worth" answer
# lives in exactly one place.
REFERRAL_BONUS_DAYS = 2


def link_stripe_customer(user_id, stripe_customer_id, stripe_subscription_id):
    """
    Called from the webhook handler when checkout.session.completed
    fires -- ties this user's account to the Stripe customer/
    subscription Stripe just created.

    FIX 2026-09-15: this used to also hardcode billing_status='trialing'
    here, on the assumption that every Checkout Session this platform
    creates includes a trial, so a just-completed session was trialing
    by definition. That assumption no longer holds -- billing_engine.
    create_checkout_session() now sometimes creates a card-collection-
    only session with NO Stripe-side trial at all (a user adding a card
    after their card-optional trial already expired should be charged
    immediately, not handed a second trial), which would land as
    'active' or 'incomplete', not 'trialing'. Deliberately leaves
    billing_status untouched here rather than guessing -- the
    customer.subscription.created webhook Stripe sends within the same
    round-trip (see saas_webhook_server.py's dispatch table, which
    already handles that event type) sets the real status moments
    later via update_billing_status_by_customer() below. Leaving this
    user's PRE-existing billing_status in place for that brief gap
    (still 'trialing' from their card-optional trial, or 'trial_expired'
    if they were already locked out) is harmless either way -- both
    read as "not yet a confirmed paying subscriber", which is exactly
    what's still true until the next webhook lands.
    """
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE users SET stripe_customer_id = ?, stripe_subscription_id = ? "
            "WHERE user_id = ?",
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
    """Returns {"billing_status", "stripe_customer_id", "stripe_subscription_id",
    "trial_ends_at"} for this user, or None if the user doesn't exist.
    billing_status is 'none' for pre-2026-09-15 accounts that never
    started a trial or Checkout; every account created since then starts
    'trialing' with trial_ends_at set (see create_user()). trial_ends_at
    is only meaningful while stripe_subscription_id is still NULL -- see
    this file's BILLING section docstring above link_stripe_customer()."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT billing_status, stripe_customer_id, stripe_subscription_id, trial_ends_at "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "billing_status": row[0],
            "stripe_customer_id": row[1],
            "stripe_subscription_id": row[2],
            "trial_ends_at": row[3],
        }
    finally:
        conn.close()


def get_trial_days_remaining(user_id):
    """
    Returns the number of whole days left in this user's CARD-OPTIONAL
    trial (rounded up, so "a few hours left" still reads as 1, not 0),
    or None if there's nothing meaningful to show -- no trial_ends_at at
    all (pre-migration account), or they already have a real Stripe
    subscription (stripe_subscription_id set), in which case Stripe's
    own status is what matters, not this platform's trial clock. Never
    negative -- a trial that's already expired returns 0, not a
    negative number that would read strangely in a UI banner.
    """
    billing = get_billing_info(user_id)
    if not billing or not billing.get("trial_ends_at") or billing.get("stripe_subscription_id"):
        return None
    try:
        trial_ends_at = datetime.fromisoformat(billing["trial_ends_at"])
    except (TypeError, ValueError):
        return None
    remaining = trial_ends_at - datetime.now(timezone.utc)
    if remaining.total_seconds() <= 0:
        return 0
    # Ceiling division on whole days -- "23 hours left" should still say
    # "1 day left", not "0 days left", right up until it actually expires.
    import math
    return math.ceil(remaining.total_seconds() / 86400)


def expire_stale_trials():
    """
    Finds every user whose card-optional trial (billing_status='trialing',
    stripe_subscription_id still NULL -- see this file's BILLING section
    docstring) has passed trial_ends_at with no card ever added, and
    flips them to billing_status='trial_expired'.

    'trial_expired' is a value neither engines/saas_decision_engine.py's
    billing gate nor saas_app.py's render_dashboard() gate has ever
    special-cased -- both already block anything outside ('trialing',
    'active') from opening new positions / seeing the dashboard, and
    both already leave exit protection on existing positions completely
    unaffected (see saas_decision_engine.py's own comment on why exit
    protection runs before the billing check). Introducing a new status
    string here needed zero changes to either gate -- only
    render_billing_gate()'s copy (a past_due/canceled/trial_expired
    three-way branch, see saas_app.py) needed to learn the new word so
    it shows the right message and the right Checkout button (no trial
    this time -- see billing_engine.create_checkout_session()).

    Meant to be called once per scheduler tick (see saas_scheduler.py) --
    cheap (one indexed-ish WHERE clause), idempotent, and safe to call
    as often as convenient. Returns the list of user_ids just expired,
    for the caller's own log line.
    """
    conn = _get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        rows = conn.execute(
            "SELECT user_id FROM users WHERE billing_status = 'trialing' "
            "AND stripe_subscription_id IS NULL AND trial_ends_at IS NOT NULL "
            "AND trial_ends_at <= ?",
            (now,),
        ).fetchall()
        expired_user_ids = [r[0] for r in rows]
        if expired_user_ids:
            conn.executemany(
                "UPDATE users SET billing_status = 'trial_expired' WHERE user_id = ?",
                [(uid,) for uid in expired_user_ids],
            )
            conn.commit()
        return expired_user_ids
    finally:
        conn.close()


def get_users_needing_trial_reminder(days_before=3):
    """
    Finds every user whose card-optional trial ends within the next
    `days_before` days, has never been sent the reminder (trial_reminder_
    sent=0), and hasn't already added a card (stripe_subscription_id
    still NULL -- someone who already paid doesn't need a "your trial is
    ending" nudge). Returns [{"user_id", "email", "trial_ends_at"}, ...]
    so the caller (saas_scheduler.py, via engines/email_engine.py) can
    send the email and then call mark_trial_reminder_sent() per user --
    deliberately two separate steps rather than this function marking
    them itself, so a caller whose email send fails can simply not call
    mark_trial_reminder_sent() and the user is picked up again next tick
    instead of silently never being reminded.
    """
    conn = _get_connection()
    try:
        now_dt = datetime.now(timezone.utc)
        cutoff = (now_dt + timedelta(days=days_before)).isoformat()
        rows = conn.execute(
            "SELECT user_id, email, trial_ends_at FROM users "
            "WHERE billing_status = 'trialing' AND stripe_subscription_id IS NULL "
            "AND trial_reminder_sent = 0 AND trial_ends_at IS NOT NULL "
            "AND trial_ends_at <= ? AND trial_ends_at > ?",
            (cutoff, now_dt.isoformat()),
        ).fetchall()
        return [{"user_id": r[0], "email": r[1], "trial_ends_at": r[2]} for r in rows]
    finally:
        conn.close()


def mark_trial_reminder_sent(user_id):
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE users SET trial_reminder_sent = 1 WHERE user_id = ?", (user_id,)
        )
        conn.commit()
    finally:
        conn.close()


# ============================================================
# REFERRALS (added 2026-09-15). Every account has exactly one referral_
# code (its own, generated once at signup in create_user()) and at most
# one referred_by_code (write-once, set only at signup, NULL if they
# signed up without one or typed one that didn't match anyone). See
# create_user()'s docstring for how a code is applied at signup time,
# and REFERRAL_BONUS_DAYS above for how many days a referral is worth.
# Both sides get the same bonus: the new signup's own trial already
# includes it (folded into trial_ends_at at INSERT time in create_user()
# -- nothing more to do for them), and apply_referral_bonus() below is
# what credits the REFERRER, called once from inside create_user()
# right after the new account is committed.
# ============================================================

# Alphabet deliberately excludes visually-ambiguous characters (0/O,
# 1/I/L) -- these codes are meant to be read off a screen and typed or
# spoken aloud when someone shares theirs with a friend, not just
# copy-pasted.
_REFERRAL_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_REFERRAL_CODE_LENGTH = 8


def _generate_unique_referral_code(conn):
    """
    Generates an 8-character referral code and confirms no existing user
    already has it, retrying on the (extremely unlikely, ~32^8 possible
    codes) chance of a collision. Takes the caller's own connection
    rather than opening one of its own -- called from inside create_
    user()'s transaction, before that row is inserted, so it needs to see
    the same connection's view of the table.
    """
    for _ in range(10):
        code = "".join(secrets.choice(_REFERRAL_CODE_ALPHABET) for _ in range(_REFERRAL_CODE_LENGTH))
        existing = conn.execute(
            "SELECT 1 FROM users WHERE referral_code = ?", (code,)
        ).fetchone()
        if not existing:
            return code
    # Astronomically unlikely to ever be reached (would require ~10
    # consecutive collisions in a 32-character-alphabet 8-char space) --
    # raising rather than silently returning a possibly-duplicate code,
    # since referral_code is relied on as a unique lookup key elsewhere.
    raise RuntimeError("Could not generate a unique referral code after 10 attempts.")


def apply_referral_bonus(referrer_user_id, days=None):
    """
    Credits `days` (default REFERRAL_BONUS_DAYS) of extra trial time to
    the REFERRER's account -- called once from create_user() when a new
    signup used their code. Extends from max(their current trial_ends_at,
    now) rather than just adding to whatever's on file, so this always
    reads as "N more days from today" even if their trial had already
    expired -- which also REVIVES a 'trial_expired' account back to
    'trialing' (and clears trial_reminder_sent, so the 3-day-before
    reminder can fire again for the new, later end date) rather than
    silently extending a date nobody can see anymore behind a lockout
    screen that never re-checks it.

    A no-op if the referrer already has a real Stripe subscription
    (stripe_subscription_id set) -- they've already converted, Stripe
    governs their billing now, and trial_ends_at isn't consulted for
    them anywhere in this file (see this file's BILLING section
    docstring). Returns True if a bonus was actually applied, False if
    skipped (paying already, or the referrer_user_id doesn't exist).
    """
    days = days if days is not None else REFERRAL_BONUS_DAYS
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT billing_status, stripe_subscription_id, trial_ends_at "
            "FROM users WHERE user_id = ?",
            (referrer_user_id,),
        ).fetchone()
        if not row:
            return False
        billing_status, stripe_subscription_id, trial_ends_at = row
        if stripe_subscription_id:
            return False  # already a paying subscriber -- nothing to extend

        now_dt = datetime.now(timezone.utc)
        try:
            current_end = datetime.fromisoformat(trial_ends_at) if trial_ends_at else now_dt
        except (TypeError, ValueError):
            current_end = now_dt
        base = max(current_end, now_dt)
        new_end = (base + timedelta(days=days)).isoformat()

        new_status = "trialing" if billing_status == "trial_expired" else billing_status
        conn.execute(
            "UPDATE users SET trial_ends_at = ?, billing_status = ?, trial_reminder_sent = 0 "
            "WHERE user_id = ?",
            (new_end, new_status, referrer_user_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_referral_info(user_id):
    """
    Returns {"referral_code", "referred_by_code", "referral_count"} for
    this user, or None if they don't exist -- referral_count is a live
    COUNT of other accounts whose referred_by_code matches this user's
    own code, computed on read rather than stored/incremented anywhere,
    so it can never drift out of sync with the users table it's counting.

    Backfills referral_code on the fly for any account that doesn't have
    one yet -- every account created since this feature shipped gets one
    at signup (see create_user()), but accounts created before that still
    have NULL here. Generating it lazily on first read, rather than a
    one-time migration script touching every existing row, means this
    stays correct even for rows added between deploys with no extra step
    to remember to run.
    """
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT referral_code, referred_by_code FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        referral_code, referred_by_code = row
        if not referral_code:
            referral_code = _generate_unique_referral_code(conn)
            conn.execute(
                "UPDATE users SET referral_code = ? WHERE user_id = ?",
                (referral_code, user_id),
            )
            conn.commit()
        count_row = conn.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by_code = ?", (referral_code,)
        ).fetchone()
        return {
            "referral_code": referral_code,
            "referred_by_code": referred_by_code,
            "referral_count": count_row[0] if count_row else 0,
        }
    finally:
        conn.close()
