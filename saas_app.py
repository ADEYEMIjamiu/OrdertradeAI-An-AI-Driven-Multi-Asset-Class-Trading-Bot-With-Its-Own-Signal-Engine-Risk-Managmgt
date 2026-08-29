"""
Multi-user SaaS entry point -- login/signup, broker-credential connection,
and per-user settings, built on engines/tenant_engine.py.

Deliberately a SEPARATE Streamlit entry point from app.py, not a change
bolted onto it. app.py is your own single-owner live trading dashboard
(already running in production on the droplet) -- this file is the new
multi-user product being built out. Keeping them separate means nothing
here can break your own bot, and this can be deployed/iterated on
independently (its own systemd service + port, whenever that's ready)
without touching the service currently running.

Run locally to try it: streamlit run saas_app.py

Scope locked 2026-08-25 (see conversation): bring-your-own-broker
custody (users connect their OWN broker API keys below -- this platform
never holds or pools anyone's funds), paper/demo-only at launch
(enforced -- there is no UI control anywhere in this file to turn on
live trading; that is intentional, not an oversight).

UPDATED 2026-08-26: the per-user AI decision loop is now wired in below
(render_trading_run(), backed by engines/saas_decision_engine.py) --
Preview generates signals and shows what would be bought without
placing anything, Execute actually places the orders after an explicit
confirmation checkbox. Covers all four asset classes now (eToro
follow-up landed same day, see saas_broker_factory.py's docstring for
what's still rougher about eToro specifically -- no trailing-lock
ratchet, no exit-engine coverage). Still BUY-side only -- no automated
selling beyond the stop-loss/take-profit/hard-time-exit protection
already wired in. A background scheduler (saas_scheduler.py, a systemd
timer) now also exists separately from this manual dashboard flow --
see that file's own docstring for its scope and safety-model
implications. See saas_decision_engine.py's module docstring for the
full scope and the gaps that are still open (no portfolio-level
exposure cap) before this should be trusted beyond supervised testing.
"""

import os
import traceback

import pandas as pd
import streamlit as st

from engines import tenant_engine as tenant
from engines import saas_broker_factory
from engines import saas_decision_engine
from engines import saas_emergency_stop
from engines import saas_admin_engine
from engines import email_engine
from engines import billing_engine

# Public product domain -- used to build the links inside password-reset
# and verification emails. Deliberately a plain constant, not derived
# from the incoming request's Host header: this app is only ever meant
# to be reached at this one domain (see deploy/nginx-ordertradeai-com.conf),
# and trusting a request header for this would let anyone who spoofs
# Host construct a reset link pointing at a domain they control.
BASE_URL = "https://ordertradeai.com"

# Added when the marketing landing page took over the domain root
# (ordertradeai.com/): this Streamlit app now lives at /app instead of
# root, so every link this file builds (password reset, email
# verification, Stripe checkout/portal return URLs) needs the /app
# prefix or it sends people to the landing page instead of back into
# the product. Streamlit itself is started with
# --server.baseUrlPath=app to match (see deploy/saas-app.service).
APP_URL = f"{BASE_URL}/app"

# Platform admin gate -- comma-separated list of emails in the
# environment (never hardcoded in source, never a database flag a bug
# could accidentally flip). Empty by default: no ADMIN_EMAILS set means
# no one sees the Admin Panel tab at all, fail-closed rather than
# fail-open. Set in .env, e.g. ADMIN_EMAILS=you@example.com
_ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "").split(",")
    if e.strip()
}


def _is_admin(email):
    return bool(email) and email.strip().lower() in _ADMIN_EMAILS


# Plain profile field, added 2026-08-29 alongside phone -- see
# tenant_engine.py's 2026-08-29 migration notes. No billing/compliance
# logic reads this list; it exists only so the signup and account
# settings dropdowns aren't free-text. "Prefer not to say" is the
# default so nobody is forced to pick one to finish signing up.
COUNTRY_OPTIONS = [
    "Prefer not to say",
    "United States", "United Kingdom", "Canada", "Australia", "New Zealand",
    "Ireland", "Nigeria", "Ghana", "South Africa", "Kenya", "Egypt",
    "Germany", "France", "Spain", "Italy", "Portugal", "Netherlands",
    "Belgium", "Switzerland", "Austria", "Sweden", "Norway", "Denmark",
    "Finland", "Poland", "Czechia", "Greece", "Romania", "Hungary",
    "Ukraine", "Turkey", "Israel", "United Arab Emirates", "Saudi Arabia",
    "India", "Pakistan", "Bangladesh", "China", "Japan", "South Korea",
    "Singapore", "Malaysia", "Indonesia", "Philippines", "Thailand",
    "Vietnam", "Hong Kong", "Taiwan", "Mexico", "Brazil", "Argentina",
    "Chile", "Colombia", "Peru", "Other",
]

st.set_page_config(
    page_title="OrderTrade AI -- Sign In",
    page_icon="📈",
    layout="centered",
)

# ============================================================
# SESSION STATE
# ============================================================
if "saas_user_id" not in st.session_state:
    st.session_state.saas_user_id = None
if "saas_user_email" not in st.session_state:
    st.session_state.saas_user_email = None


def _log_in(user_id, email):
    st.session_state.saas_user_id = user_id
    st.session_state.saas_user_email = email


def _log_out():
    st.session_state.saas_user_id = None
    st.session_state.saas_user_email = None


# ============================================================
# LOGGED-OUT VIEW: LOGIN / SIGN UP
# ============================================================
def render_auth_screen():
    st.caption(f"[← Back to ordertradeai.com]({BASE_URL}/)")
    st.title("📈 OrderTrade AI")
    st.caption(
        "Early access -- paper/demo trading only. Connect your own "
        "broker accounts; we never hold or trade your funds directly."
    )

    login_tab, signup_tab = st.tabs(["Log In", "Sign Up"])

    with login_tab:
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Log In", use_container_width=True)

        if submitted:
            if not email or not password:
                st.error("Enter both email and password.")
            else:
                user_id = tenant.authenticate_user(email, password)
                if user_id is None:
                    st.error("Invalid email or password.")
                else:
                    _log_in(user_id, email.strip().lower())
                    st.rerun()

        with st.expander("Forgot password?"):
            with st.form("forgot_password_form"):
                forgot_email = st.text_input("Email", key="forgot_password_email")
                forgot_submitted = st.form_submit_button("Send reset link")

            if forgot_submitted:
                if not forgot_email:
                    st.error("Enter your email.")
                else:
                    # Deliberately the SAME message regardless of whether
                    # this email is actually registered -- branching the
                    # visible outcome would let anyone probe which emails
                    # have accounts (see create_password_reset_token()'s
                    # docstring for the same reasoning applied server-side).
                    reset_token = tenant.create_password_reset_token(forgot_email)
                    if reset_token is not None:
                        try:
                            reset_url = f"{APP_URL}/?reset_token={reset_token}"
                            email_engine.send_password_reset_email(forgot_email, reset_url)
                        except Exception:
                            # Swallowed deliberately -- surfacing a send
                            # failure here would itself leak whether the
                            # email was registered (only registered emails
                            # reach this branch at all).
                            pass
                    st.success(
                        "If that email is registered, a password reset link has been sent."
                    )

    with signup_tab:
        with st.form("signup_form"):
            new_email = st.text_input("Email", key="signup_email")
            new_password = st.text_input("Password", type="password", key="signup_password")
            confirm_password = st.text_input(
                "Confirm password", type="password", key="signup_confirm"
            )
            new_phone = st.text_input(
                "Phone number (optional)", key="signup_phone"
            )
            new_country = st.selectbox(
                "Country (optional)", options=COUNTRY_OPTIONS, key="signup_country"
            )
            agreed_to_terms = st.checkbox(
                "I agree to the [Terms of Service](/app/?page=terms) and "
                "[Privacy Policy](/app/?page=privacy)",
                key="signup_agree_terms",
            )
            signup_submitted = st.form_submit_button("Create Account", use_container_width=True)

        if signup_submitted:
            if not new_email or not new_password:
                st.error("Enter both email and password.")
            elif len(new_password) < 8:
                st.error("Password must be at least 8 characters.")
            elif new_password != confirm_password:
                st.error("Passwords don't match.")
            elif not agreed_to_terms:
                st.error("You must agree to the Terms of Service and Privacy Policy to create an account.")
            else:
                user_id = tenant.create_user(
                    new_email,
                    new_password,
                    phone=new_phone,
                    country=(
                        new_country if new_country != "Prefer not to say" else None
                    ),
                )
                if user_id is None:
                    st.error("An account with this email already exists. Try logging in instead.")
                else:
                    # Log in immediately (don't gate account access on
                    # verification -- that would strand a user with a
                    # broken/slow mail delivery). The dashboard shows a
                    # persistent banner with a resend option until
                    # email_verified flips to True. See render_dashboard().
                    try:
                        verify_token = tenant.create_email_verification_token(user_id)
                        verify_url = f"{APP_URL}/?verify_token={verify_token}"
                        email_engine.send_verification_email(new_email, verify_url)
                        st.success("Account created. Check your email to verify your address.")
                    except Exception:
                        st.success("Account created.")
                        st.warning(
                            "We couldn't send a verification email right now -- "
                            "you can resend it from your dashboard."
                        )
                    _log_in(user_id, new_email.strip().lower())
                    st.rerun()

    st.caption("[Terms of Service](/app/?page=terms) · [Privacy Policy](/app/?page=privacy)")


# ============================================================
# LOGGED-IN VIEW: DASHBOARD (broker connections + settings)
# ============================================================
_BROKER_FIELDS = {
    "ALPACA": {
        "label": "Alpaca (US Stocks -- Paper)",
        "environment": "paper",
        "key_label": "API Key ID",
        "secret_label": "Secret Key",
        "has_extra": False,
    },
    "BINANCE": {
        "label": "Binance (Crypto -- Testnet)",
        "environment": "testnet",
        "key_label": "API Key",
        "secret_label": "Secret Key",
        "has_extra": False,
    },
    "ETORO": {
        "label": "eToro (Forex/Commodities -- Demo)",
        "environment": "demo",
        "key_label": "API Key",
        "secret_label": "User Key",
        "has_extra": False,
    },
}


def render_broker_connections(user_id):
    st.subheader("Broker Connections")
    st.caption(
        "Your API keys are encrypted before they're stored and are only "
        "ever decrypted to place trades on your own connected account."
    )

    connected = {c["broker"]: c for c in tenant.list_connected_brokers(user_id)}

    for broker_code, meta in _BROKER_FIELDS.items():
        status = connected.get(broker_code)
        status_text = (
            f"✅ Connected ({status['environment']}, updated {status['updated_at'][:10]})"
            if status else "Not connected"
        )

        with st.expander(f"{meta['label']} -- {status_text}"):
            with st.form(f"broker_form_{broker_code}"):
                api_key = st.text_input(meta["key_label"], type="password", key=f"{broker_code}_key")
                api_secret = st.text_input(meta["secret_label"], type="password", key=f"{broker_code}_secret")
                save_clicked = st.form_submit_button("Save credentials")

            if save_clicked:
                if not api_key or not api_secret:
                    st.error("Both fields are required.")
                else:
                    tenant.save_broker_credentials(
                        user_id,
                        broker=broker_code,
                        environment=meta["environment"],
                        api_key=api_key,
                        api_secret=api_secret,
                    )
                    st.success(f"{meta['label']} credentials saved.")
                    st.rerun()

            if status:
                if st.button("Test Connection", key=f"test_{broker_code}"):
                    result = saas_broker_factory.check_user_broker_connection(user_id, broker_code)
                    if result.get("connected"):
                        st.success(
                            f"Connected -- cash: ${result.get('cash', 0):,.2f}, "
                            f"equity: ${result.get('equity', result.get('cash', 0)):,.2f}"
                        )
                    else:
                        st.error(f"Connection failed: {result.get('error')}")


def render_settings(user_id):
    st.subheader("Trading Settings")

    settings = tenant.get_user_settings(user_id)
    if settings is None:
        st.error("Could not load settings for this account.")
        return

    st.info(
        "🔒 Paper/demo trading only during early access -- this cannot "
        "be changed from this page."
    )

    # Deliberately its own immediate-effect toggle, outside the settings
    # form below -- a kill switch shouldn't require also touching (or
    # accidentally changing) position size / asset class settings to
    # take effect, and shouldn't wait on a form submit either. Blocks
    # new BUY evaluation only; stop-loss/take-profit/time-based exits on
    # positions you already hold keep running even while paused -- see
    # engines/saas_decision_engine.py.
    is_paused = settings.get("trading_paused", False)
    if is_paused:
        st.error("⏸ Your AI trading is PAUSED -- no new positions will be opened. Existing positions still get exit protection.")
    pause_label = "Resume my AI trading" if is_paused else "Pause my AI trading"
    if st.button(pause_label, key="toggle_trading_paused"):
        tenant.save_user_settings(user_id, trading_paused=not is_paused)
        st.rerun()

    # A visual break before the settings form below -- this button takes
    # effect immediately (see save_user_settings() above), it isn't part
    # of the form, and without a divider the two read as one control.
    st.divider()

    with st.form("settings_form"):
        max_position_size = st.slider(
            "Max position size (% of account per trade)",
            min_value=5, max_value=50,
            value=int(settings["max_position_size"] * 100),
            step=5,
        )
        enabled_classes = st.multiselect(
            "Asset classes to trade",
            options=["US_STOCKS", "CRYPTO", "FOREX", "COMMODITIES"],
            default=settings["enabled_asset_classes"],
        )
        save_settings_clicked = st.form_submit_button("Save settings")

    if save_settings_clicked:
        tenant.save_user_settings(
            user_id,
            max_position_size=max_position_size / 100,
            enabled_asset_classes=enabled_classes,
        )
        st.success("Settings saved.")
        st.rerun()


def render_account_settings(user):
    st.subheader("Account Settings")

    with st.expander("Profile"):
        with st.form("profile_form"):
            phone = st.text_input(
                "Phone number (optional)", value=user.get("phone") or "", key="profile_phone"
            )
            current_country = user.get("country") or "Prefer not to say"
            country_index = (
                COUNTRY_OPTIONS.index(current_country)
                if current_country in COUNTRY_OPTIONS else 0
            )
            country = st.selectbox(
                "Country (optional)", options=COUNTRY_OPTIONS,
                index=country_index, key="profile_country",
            )
            profile_saved = st.form_submit_button("Save profile")

        if profile_saved:
            tenant.update_profile_fields(
                user["user_id"],
                phone=phone,
                country=(country if country != "Prefer not to say" else ""),
            )
            st.success("Profile updated.")
            st.rerun()

    with st.expander("Change email"):
        st.caption(
            f"Current email: {user['email']}. Changing it sends a confirmation "
            "link to the NEW address -- your current email keeps working until "
            "you click that link."
        )
        with st.form("change_email_form"):
            new_email = st.text_input("New email", key="change_email_new")
            current_password = st.text_input(
                "Current password", type="password", key="change_email_password"
            )
            change_submitted = st.form_submit_button("Send confirmation link")

        if change_submitted:
            if not new_email or not current_password:
                st.error("Enter your new email and current password.")
            elif tenant.authenticate_user(user["email"], current_password) is None:
                st.error("Current password is incorrect.")
            elif new_email.strip().lower() == user["email"]:
                st.error("That's already your current email.")
            else:
                token = tenant.request_email_change(user["user_id"], new_email)
                if token is None:
                    st.error("That email is already in use by another account.")
                else:
                    try:
                        confirm_url = f"{APP_URL}/?change_email_token={token}"
                        email_engine.send_email_change_confirmation(new_email, confirm_url)
                        st.success(
                            f"Confirmation link sent to {new_email.strip().lower()}. "
                            "Click it to finish changing your email."
                        )
                    except Exception:
                        print("[account] send_email_change_confirmation failed:")
                        traceback.print_exc()
                        st.error(
                            "Couldn't send the confirmation email right now -- "
                            "try again shortly."
                        )


def render_trading_run(user_id):
    st.subheader("AI Trading")
    st.caption(
        "Checks your existing positions for stop-loss/take-profit/max-"
        "hold-time exits, then runs the AI signal engine across your "
        "enabled asset classes (US Stocks via Alpaca, Crypto via Binance, "
        "Forex/Commodities via eToro), sizes any approved BUY against "
        "your real connected-broker balance, and shows you exactly what "
        "it would do. Nothing is ever bought or sold without you "
        "clicking Execute separately below. Exit protection (stop-loss/"
        "take-profit/a hard max-hold-time, no partial profit-taking) "
        "currently covers US Stocks and Crypto only -- an eToro position "
        "relies on the fixed stop-loss/take-profit set on the broker "
        "side at trade-open, same as everywhere else in this project, "
        "just without this dashboard's own exit-protection pass checking "
        "on it in between visits."
    )

    preview_clicked = st.button("Preview AI Signals", key="preview_signals")
    if preview_clicked:
        with st.spinner("Generating signals and checking your account..."):
            st.session_state.saas_preview_results = (
                saas_decision_engine.run_decision_loop_for_user(user_id, dry_run=True)
            )
        st.session_state.saas_preview_ran_for = user_id

    results = st.session_state.get("saas_preview_results")
    if results is not None and st.session_state.get("saas_preview_ran_for") == user_id:
        df = pd.DataFrame(results)
        st.dataframe(df, use_container_width=True)

        buy_candidates = [r for r in results if r["action"] == "would_buy"]
        sell_candidates = [r for r in results if r["action"] == "would_sell"]
        if buy_candidates or sell_candidates:
            parts = []
            if buy_candidates:
                parts.append(f"{len(buy_candidates)} BUY(s)")
            if sell_candidates:
                parts.append(f"{len(sell_candidates)} SELL(s) (exit protection)")
            st.warning(
                f"{' and '.join(parts)} would be placed with real "
                f"(paper/testnet) orders on your connected broker account."
            )
            confirm = st.checkbox(
                "I understand this will place real paper/testnet orders "
                "on my connected broker account.",
                key="saas_execute_confirm",
            )
            if st.button("Execute These Trades", disabled=not confirm, key="execute_trades"):
                with st.spinner("Placing orders..."):
                    # Re-runs the full loop live rather than replaying the
                    # preview -- prices/approval can genuinely change in the
                    # seconds between Preview and this click, and re-running
                    # for real is the only way to size/execute off current
                    # data instead of a possibly-stale preview.
                    live_results = saas_decision_engine.run_decision_loop_for_user(
                        user_id, dry_run=False
                    )
                st.session_state.saas_preview_results = None
                st.session_state.pop("saas_execute_confirm", None)
                st.dataframe(pd.DataFrame(live_results), use_container_width=True)
                bought = [r for r in live_results if r["action"] == "bought"]
                sold = [r for r in live_results if r["action"] == "sold"]
                pending = [r for r in live_results if r["action"] == "submitted"]
                reconciled = [r for r in live_results if r["action"] == "reconciled"]
                failed = [r for r in live_results if r["action"] == "error"]
                if bought:
                    st.success(f"Bought {len(bought)} position(s).")
                if sold:
                    st.success(f"Sold {len(sold)} position(s) (exit protection).")
                if reconciled:
                    st.info(f"{len(reconciled)} previously-pending order(s) confirmed -- see table above.")
                if pending:
                    st.warning(
                        f"{len(pending)} order(s) submitted to your broker but not yet "
                        f"confirmed filled -- this will be caught up automatically the "
                        f"next time you click Preview or Execute."
                    )
                if failed:
                    st.error(f"{len(failed)} order(s) failed -- see table above.")
        else:
            st.info("No approved BUY candidates or exit triggers right now.")


def render_open_positions(user_id):
    st.subheader("My Positions")
    st.caption(
        "Your currently open positions across all connected brokers, read "
        "live from each broker's own account (Alpaca) or reconciled "
        "against your real wallet/portfolio balance (Binance/eToro) -- "
        "not just this dashboard's order history, so a position that's "
        "actually already closed on the broker side won't show up here "
        "as a ghost row."
    )

    connected_brokers = [c["broker"] for c in tenant.list_connected_brokers(user_id)]
    if not connected_brokers:
        st.info("Connect a broker above to see your positions here.")
        return

    all_positions = []
    for broker_code in connected_brokers:
        try:
            positions = saas_broker_factory.get_user_open_positions(user_id, broker_code)
        except Exception:
            positions = []
        for p in positions:
            all_positions.append({"Broker": _BROKER_FIELDS[broker_code]["label"].split(" (")[0], **p})

    if not all_positions:
        st.info("No open positions right now.")
        return

    df = pd.DataFrame(all_positions)
    df = df.rename(columns={
        "ticker": "Ticker",
        "quantity": "Quantity",
        "entry_price": "Entry Price",
        "current_price": "Current Price",
        "unrealized_pnl": "Unrealized PnL ($)",
        "unrealized_pnl_pct": "Unrealized PnL (%)",
        "stop_loss": "Stop Loss",
        "take_profit": "Take Profit",
    })
    column_order = ["Broker", "Ticker", "Quantity", "Entry Price", "Current Price",
                     "Unrealized PnL ($)", "Unrealized PnL (%)", "Stop Loss", "Take Profit"]
    df = df[[c for c in column_order if c in df.columns]]
    st.dataframe(df, use_container_width=True, hide_index=True)

    if "eToro" in df["Broker"].values:
        st.caption(
            "Note: eToro Unrealized PnL ($) is intentionally left blank -- "
            "the quantity/margin figures available here aren't enough to "
            "compute a correct leveraged CFD dollar P&L. Unrealized PnL "
            "(%) (raw price change) is shown instead; check the eToro app "
            "for exact P&L."
        )


# ============================================================
# ADMIN PANEL (only rendered for emails in ADMIN_EMAILS)
# ============================================================
def render_admin_panel():
    st.subheader("🛡️ Platform Kill Switch")
    st.caption(
        "Blocks new BUY evaluation for EVERY user on the platform at "
        "once -- for a systemic issue (bad model, broken broker "
        "integration, a bug in the decision loop itself), not a "
        "response to one user's problem. Each user's stop-loss/take-"
        "profit/max-hold-time exit protection keeps running on their "
        "existing positions even while this is active -- a platform-"
        "wide halt should never trap anyone in a position that would "
        "otherwise have closed protectively. This is separate from "
        "your own single-owner bot's kill switch and from each user's "
        "individual pause toggle."
    )
    is_stopped = saas_emergency_stop.is_stopped()
    if is_stopped:
        reason = saas_emergency_stop.get_reason()
        st.error(f"⏸ SaaS-wide trading is STOPPED.{f' Reason: {reason}' if reason else ''}")
        if st.button("Resume platform-wide trading", key="admin_resume"):
            saas_emergency_stop.deactivate()
            st.rerun()
    else:
        st.success("✅ Platform is running normally.")
        with st.form("admin_stop_form"):
            reason = st.text_input("Reason (shown to you when reviewing this later)", key="admin_stop_reason")
            stop_clicked = st.form_submit_button("🛑 Stop ALL trading platform-wide", use_container_width=True)
        if stop_clicked:
            saas_emergency_stop.activate(reason)
            st.rerun()

    st.divider()

    st.subheader("📊 Aggregate Exposure")
    st.caption(
        "Open position COUNTS across every active user's connected "
        "brokers -- deliberately not a blended dollar total. Different "
        "users hold different brokers under different currencies/"
        "leverage (eToro CFDs especially), so summing dollar P&L across "
        "all of them would look precise while meaning nothing real."
    )
    with st.spinner("Reading positions across all users' connected brokers..."):
        exposure = saas_admin_engine.get_platform_exposure_summary()
    col1, col2, col3 = st.columns(3)
    col1.metric("Total open positions", exposure["total_open_positions"])
    col2.metric("Users with open positions", exposure["users_with_open_positions"])
    col3.metric(
        "Busiest broker",
        max(exposure["per_broker"], key=exposure["per_broker"].get)
        if exposure["total_open_positions"] else "--",
    )
    st.dataframe(
        pd.DataFrame([
            {"Broker": b, "Open Positions": c} for b, c in exposure["per_broker"].items()
        ]),
        use_container_width=True, hide_index=True,
    )

    st.divider()

    st.subheader("👥 Users")
    users = saas_admin_engine.get_admin_user_summary()
    if not users:
        st.info("No users yet.")
        return
    df = pd.DataFrame(users)
    df["connected_brokers"] = df["connected_brokers"].apply(lambda b: ", ".join(b) if b else "--")
    df = df.rename(columns={
        "email": "Email",
        "created_at": "Joined",
        "is_active": "Active",
        "trading_paused": "Paused (self)",
        "connected_brokers": "Connected Brokers",
        "billing_status": "Billing",
    })
    df = df[["Email", "Joined", "Active", "Paused (self)", "Billing", "Connected Brokers"]]
    st.dataframe(df, use_container_width=True, hide_index=True)


# ============================================================
# BILLING GATE -- shown instead of the normal dashboard whenever a
# non-admin user's billing_status isn't 'trialing' or 'active'. Admins
# (ADMIN_EMAILS) are deliberately exempt -- the platform owner
# shouldn't be able to lock themselves out by their own billing bugs,
# an unpaid test invoice, or simply never having run Checkout on their
# own account. Every real user still goes through this every time
# their status isn't currently good, driven entirely by
# tenant.get_billing_info() -- which only ever changes via the Stripe
# webhook handler in saas_webhook_server.py, never from anything in
# this file.
# ============================================================
def render_billing_gate(user):
    st.title("📈 OrderTrade AI")
    st.caption(f"Signed in as {user['email']}")

    if st.query_params.get("billing") == "success":
        st.info(
            "Payment info received. If your access doesn't unlock within "
            "a few seconds, refresh this page -- Stripe's confirmation "
            "can take a moment to arrive."
        )

    billing = tenant.get_billing_info(user["user_id"]) or {}
    status = billing.get("billing_status", "none")

    if status in ("past_due", "canceled"):
        reason = "a recent payment failed" if status == "past_due" else "it was canceled"
        st.error(
            f"⚠️ Your subscription needs attention ({reason}). "
            "Update your billing to keep using OrderTrade AI."
        )
        if billing.get("stripe_customer_id"):
            try:
                portal_url = billing_engine.create_billing_portal_session(
                    billing["stripe_customer_id"], APP_URL
                )
                st.link_button("Manage Billing", portal_url, use_container_width=True)
            except Exception:
                # Logged (not just shown to the user) so failures are
                # actually diagnosable via journalctl -- a bare "except
                # Exception: st.warning(...)" with no logging call was the
                # root cause of a hard-to-debug Checkout failure earlier.
                print("[billing] create_billing_portal_session failed:")
                traceback.print_exc()
                st.warning("Couldn't load the billing portal right now -- try again shortly.")
    else:
        st.subheader("Start your 7-day free trial")
        st.write(
            "Full access to OrderTrade AI for 7 days, then **$39/month**. "
            "Cancel anytime from your billing settings -- you won't be "
            "charged if you cancel during the trial."
        )
        try:
            checkout_url = billing_engine.create_checkout_session(
                user["user_id"], user["email"], APP_URL
            )
            st.link_button("Start Free Trial", checkout_url, use_container_width=True)
        except Exception:
            print("[billing] create_checkout_session failed:")
            traceback.print_exc()
            st.error("Couldn't start checkout right now -- try again shortly.")

    st.divider()
    st.caption("[Terms of Service](/app/?page=terms) · [Privacy Policy](/app/?page=privacy)")
    if st.button("Log Out", key="billing_gate_logout"):
        _log_out()
        st.rerun()


def render_dashboard():
    user = tenant.get_user(st.session_state.saas_user_id)
    if user is None:
        # Account no longer exists / DB reset -- fail safe back to login.
        _log_out()
        st.rerun()
        return

    is_admin_user = _is_admin(user["email"])
    billing = tenant.get_billing_info(user["user_id"]) or {}
    if not is_admin_user and billing.get("billing_status") not in ("trialing", "active"):
        render_billing_gate(user)
        return

    header_col, billing_col, logout_col = st.columns([3, 1, 1])
    with header_col:
        st.title("📈 OrderTrade AI")
        st.caption(f"Signed in as {user['email']}")
    with billing_col:
        st.write("")
        if billing.get("stripe_customer_id"):
            # UPDATED 2026-08-29: still creates the Stripe Billing Portal
            # session lazily, only when clicked -- NOT on every dashboard
            # render, which would otherwise hit Stripe's API on every
            # single rerun (Streamlit reruns the whole script on every
            # widget interaction, and this dashboard also has autorefresh
            # -- see that feature's own notes elsewhere in this file).
            # What changed is the second click: instead of surfacing a
            # separate "Open Billing Portal" link-button the user then
            # had to click again, an instant meta-refresh takes them
            # straight to Stripe the moment the session is created.
            if st.button("Manage Billing", use_container_width=True):
                try:
                    portal_url = billing_engine.create_billing_portal_session(
                        billing["stripe_customer_id"], APP_URL
                    )
                    st.markdown(
                        f'<meta http-equiv="refresh" content="0; url={portal_url}">',
                        unsafe_allow_html=True,
                    )
                    st.caption("Opening the billing portal...")
                except Exception:
                    print("[billing] create_billing_portal_session failed:")
                    traceback.print_exc()
                    st.error("Couldn't load the billing portal right now -- try again shortly.")
    with logout_col:
        st.write("")
        if st.button("Log Out", use_container_width=True):
            _log_out()
            st.rerun()

    if not user["email_verified"]:
        banner_col, button_col = st.columns([4, 1])
        with banner_col:
            st.warning("📧 Please verify your email address.")
        with button_col:
            st.write("")
            if st.button("Resend email", key="resend_verification"):
                try:
                    verify_token = tenant.create_email_verification_token(user["user_id"])
                    verify_url = f"{APP_URL}/?verify_token={verify_token}"
                    email_engine.send_verification_email(user["email"], verify_url)
                    st.success("Verification email sent.")
                except Exception:
                    st.error("Couldn't send the email right now -- try again shortly.")

    if _is_admin(user["email"]):
        my_tab, admin_tab = st.tabs(["My Dashboard", "🛡️ Admin Panel"])
        with my_tab:
            render_broker_connections(user["user_id"])
            st.divider()
            render_open_positions(user["user_id"])
            st.divider()
            render_settings(user["user_id"])
            st.divider()
            render_trading_run(user["user_id"])
            st.divider()
            render_account_settings(user)
        with admin_tab:
            render_admin_panel()
    else:
        render_broker_connections(user["user_id"])
        st.divider()
        render_open_positions(user["user_id"])
        st.divider()
        render_settings(user["user_id"])
        st.divider()
        render_trading_run(user["user_id"])
        st.divider()
        render_account_settings(user)

    st.divider()
    st.caption("[Terms of Service](/app/?page=terms) · [Privacy Policy](/app/?page=privacy)")


# ============================================================
# PASSWORD RESET / EMAIL VERIFICATION LANDING SCREENS
# Reached via the links inside the emails sent above -- ordertradeai.com/
# ?reset_token=... or ?verify_token=.... Checked BEFORE the normal
# logged-in/logged-out branch below so these work whether or not the
# person clicking the link happens to already be signed in on this
# browser.
# ============================================================
def render_password_reset_screen(token):
    st.title("📈 OrderTrade AI")
    st.subheader("Reset your password")

    user_id = tenant.verify_password_reset_token(token)
    if user_id is None:
        st.error("This reset link is invalid or has expired. Request a new one from the Log In page.")
        if st.button("Back to Log In"):
            st.query_params.clear()
            st.rerun()
        return

    with st.form("password_reset_form"):
        new_password = st.text_input("New password", type="password", key="reset_new_password")
        confirm = st.text_input("Confirm new password", type="password", key="reset_confirm_password")
        submitted = st.form_submit_button("Reset Password", use_container_width=True)

    if submitted:
        if len(new_password) < 8:
            st.error("Password must be at least 8 characters.")
        elif new_password != confirm:
            st.error("Passwords don't match.")
        else:
            ok = tenant.reset_password(token, new_password)
            if ok:
                st.success("Password updated. You can now log in with your new password.")
                if st.button("Go to Log In"):
                    st.query_params.clear()
                    st.rerun()
            else:
                st.error("This link was already used or has expired. Request a new one from the Log In page.")


def render_email_verification_screen(token):
    st.title("📈 OrderTrade AI")
    ok = tenant.verify_email_token(token)
    if ok:
        st.success("✅ Email verified. Thanks!")
    else:
        st.error("This verification link is invalid or has expired. You can resend one from your dashboard.")
    if st.button("Continue"):
        st.query_params.clear()
        st.rerun()


def render_email_change_screen(token):
    st.title("📈 OrderTrade AI")
    new_email = tenant.confirm_email_change(token)
    if new_email:
        st.success(f"✅ Email updated to {new_email}. Use this address to log in from now on.")
        # Keep an already-active session in sync so the header/caption
        # don't keep showing the old address for the rest of this visit --
        # doesn't touch anyone else's session, just this browser's.
        if st.session_state.saas_user_id:
            st.session_state.saas_user_email = new_email
    else:
        st.error(
            "This confirmation link is invalid, expired, or was already used. "
            "You can request a new one from Account Settings."
        )
    if st.button("Continue"):
        st.query_params.clear()
        st.rerun()


# ============================================================
# LEGAL PAGES -- Terms of Service / Privacy Policy
# Reached via ordertradeai.com/?page=terms or ?page=privacy, same
# query-param routing pattern as the reset/verify screens above.
# Linked from the auth screen footer and from the required signup
# consent checkbox. First-draft boilerplate written 2026-08-28 --
# NOT reviewed by a lawyer. Treat this as a placeholder that lets the
# platform launch billing (Stripe expects a published policy link) and
# have SOMETHING that governs the relationship, not as something to
# rely on if a real dispute ever comes up. Get actual legal review
# before that matters.
# ============================================================
_LEGAL_LAST_UPDATED = "August 28, 2026"

_TERMS_MD = f"""
*Last updated: {_LEGAL_LAST_UPDATED}*

**This is a draft. It has not been reviewed by a lawyer. It is provided
to give the platform a published Terms of Service while in early
access, not as a substitute for real legal advice.**

### 1. Acceptance of terms

By creating an account or using OrderTrade AI ("the Service"), you
agree to these Terms of Service ("Terms"). If you don't agree, don't
use the Service.

### 2. What the Service does

OrderTrade AI generates AI-assisted trade signals and, at your
explicit request, places orders through broker accounts that **you**
connect using your own API credentials. The Service never holds,
custodies, or has independent access to your funds -- every order is
placed directly against your own connected broker account, and you
must separately confirm ("Execute") before any live order is sent.

**During early access, the Service is paper/demo trading only.**
There is no way to enable trading with real broker funds through the
Service at this time. This may change in the future with advance
notice.

### 3. Not investment advice, no guaranteed results

Nothing generated or displayed by the Service -- signals, scores,
grades, backtests, or any other output -- is investment, financial,
tax, or legal advice, and none of it is a recommendation to buy or
sell any security, currency, commodity, or crypto asset. Trading and
investing involve substantial risk of loss, including total loss of
principal. Past performance (including any backtested or paper-traded
results shown in the Service) is not indicative of future results.
You are solely responsible for every trading decision made through
your account, whether initiated by you or executed by the Service at
your direction.

### 4. Eligibility and your account

You must be at least 18 years old (or the age of majority in your
jurisdiction) to use the Service. You're responsible for keeping your
password confidential and for all activity under your account. Tell
us promptly if you believe your account has been compromised.

### 5. Your broker credentials

You provide your own broker/exchange API keys. We encrypt them at
rest and only ever decrypt them to place orders you've directed
through the Service. You're responsible for complying with your
broker's own terms of service, and for any fees, restrictions, or
consequences your broker applies to API-driven trading on your
account.

### 6. Subscription and billing

After a 7-day free trial, continued use of the Service requires a
paid subscription, billed monthly in advance through our payment
processor (Stripe). Your subscription renews automatically each month
until you cancel. You can cancel at any time from your account
billing settings; cancellation takes effect at the end of your
current billing period, and we don't provide refunds for partial
periods already paid for. We may change our pricing with reasonable
advance notice; continuing to use the Service after a price change
takes effect means you accept the new price.

### 7. Acceptable use

You agree not to: use the Service for anything illegal; attempt to
reverse-engineer, scrape, or resell access to the Service; interfere
with or overload the Service's infrastructure; or use the Service to
violate any broker's or exchange's own terms of service.

### 8. Disclaimers

The Service is provided "as is" and "as available," without warranty
of any kind, express or implied, including warranties of
merchantability, fitness for a particular purpose, or
non-infringement. We don't warrant that the Service will be
uninterrupted, error-free, or that any signal, price, or position
data shown will always be accurate or current -- broker/exchange
outages, market data delays, and third-party API failures are outside
our control.

### 9. Limitation of liability

To the maximum extent permitted by law, OrderTrade AI and its
operator will not be liable for any indirect, incidental, special,
consequential, or punitive damages, or for any trading losses, lost
profits, or lost data, arising from your use of the Service. Our
total liability for any claim relating to the Service is limited to
the amount you paid us in the 12 months before the claim arose.

### 10. Termination

We may suspend or terminate your access if you violate these Terms or
if we reasonably believe your use of the Service poses a risk to the
platform or other users. You may stop using the Service and cancel
your subscription at any time.

### 11. Changes to these terms

We may update these Terms from time to time. We'll update the "Last
updated" date above when we do; continued use of the Service after a
change takes effect means you accept the updated Terms.

### 12. Governing law

These Terms are governed by the laws of Nigeria, without regard to its
conflict-of-laws principles.

### 13. Contact

Questions about these Terms? Contact us at
support@ordertradeai.com.
"""

_PRIVACY_MD = f"""
*Last updated: {_LEGAL_LAST_UPDATED}*

**This is a draft. It has not been reviewed by a lawyer. It is
provided to give the platform a published Privacy Policy while in
early access, not as a substitute for real legal advice.**

### 1. What we collect

- **Account info:** your email address and a securely hashed (bcrypt)
  password -- we never store your password in plain text.
- **Broker credentials:** the API key/secret you provide for each
  broker you connect, encrypted at rest (Fernet symmetric encryption)
  and decrypted only at the moment we place a trade you've directed.
- **Trading activity:** positions, orders, and settings associated
  with your account, so the Service can function and so you can see
  your own history.
- **Billing info:** handled directly by our payment processor,
  Stripe -- we never see or store your full card number. We keep only
  what Stripe tells us (subscription status, plan, renewal date).
- **Basic technical logs:** standard web server logs (IP address,
  timestamp, request path) kept for security and troubleshooting.

### 2. How we use it

To operate the Service (including placing trades you direct), send
you transactional email (password resets, email verification, billing
notices), respond to support requests, and improve the Service. We do
not use your data for advertising, and we do not sell your personal
data to anyone.

### 3. Who we share it with

Only the third parties needed to run the Service:

- **Your connected brokers** (e.g. Alpaca, Binance, eToro) -- to place
  the orders you direct.
- **Stripe** -- to process subscription billing.
- **Resend** -- to deliver transactional email (password reset,
  verification, billing notices).

We don't share your data with anyone else, and we don't sell it.

### 4. Security

Broker credentials are encrypted at rest; passwords are hashed, never
stored in plain text; all traffic to the Service is encrypted in
transit (HTTPS); and access to the servers that store this data is
restricted. No system is perfectly secure, but we treat your broker
credentials with the same care we'd want for our own.

### 5. Data retention

We keep your account data for as long as your account is active. If
you'd like your account and associated data deleted, contact us at
the address below and we'll process the request.

### 6. Your rights

Depending on where you live, you may have rights to access, correct,
or delete your personal data, or to object to certain processing.
Contact us at the address below to exercise any of these rights.

### 7. Cookies

The Service uses only the session cookie needed to keep you logged
in. We don't use third-party advertising or tracking cookies.

### 8. Children

The Service isn't directed at anyone under 18, and we don't knowingly
collect data from children.

### 9. Where your data is processed

Our servers are located in the EU. Some of our third-party processors
(brokers you connect, Stripe, Resend) may process data in other
regions as part of providing their services.

### 10. Changes to this policy

We may update this Privacy Policy from time to time. We'll update the
"Last updated" date above when we do.

### 11. Contact

Questions about this policy, or want to exercise a data right? Contact
us at support@ordertradeai.com.
"""


def render_legal_page(title, body_markdown):
    # Added 2026-08-29: a top-of-page way out, not just the "Back" button
    # at the very bottom -- someone who lands here from a search engine
    # (rather than clicking through from the signup checkbox) shouldn't
    # have to scroll past the entire document just to leave.
    if st.button("← Back", key="legal_back_top"):
        st.query_params.clear()
        st.rerun()
    st.title("📈 OrderTrade AI")
    st.header(title)
    st.markdown(body_markdown)
    st.divider()
    if st.button("Back", key="legal_back_bottom"):
        st.query_params.clear()
        st.rerun()


# ============================================================
# ENTRY POINT
# ============================================================
_query_params = st.query_params

if "reset_token" in _query_params:
    render_password_reset_screen(_query_params["reset_token"])
elif "verify_token" in _query_params:
    render_email_verification_screen(_query_params["verify_token"])
elif "change_email_token" in _query_params:
    render_email_change_screen(_query_params["change_email_token"])
elif _query_params.get("page") == "terms":
    render_legal_page("Terms of Service", _TERMS_MD)
elif _query_params.get("page") == "privacy":
    render_legal_page("Privacy Policy", _PRIVACY_MD)
elif st.session_state.saas_user_id is None:
    render_auth_screen()
else:
    render_dashboard()
