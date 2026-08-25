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
import sqlite3
import uuid
from datetime import datetime, timezone

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
            "SELECT user_id, email, created_at, is_active FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "user_id": row[0],
            "email": row[1],
            "created_at": row[2],
            "is_active": bool(row[3]),
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
            "SELECT max_position_size, enabled_asset_classes, allow_live_trading "
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
        }
    finally:
        conn.close()


def save_user_settings(user_id, max_position_size=None, enabled_asset_classes=None):
    """
    Updates a user's own settings. allow_live_trading is deliberately
    NOT a parameter here -- flipping paper/demo to real money is not
    meant to be a self-service settings toggle at this stage (see
    module docstring: paper/demo-only at launch). If/when that changes,
    it should be its own explicit, audited action, not folded into a
    general settings update.
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
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            "UPDATE user_settings SET max_position_size = ?, enabled_asset_classes = ?, "
            "updated_at = ? WHERE user_id = ?",
            (new_max_position_size, new_enabled_classes, now, user_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()
