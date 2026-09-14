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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from engines import tenant_engine as tenant
from engines import saas_broker_factory
from engines import saas_decision_engine
from engines import saas_emergency_stop
from engines import saas_admin_engine
from engines import saas_performance_engine
from engines import email_engine
from engines import billing_engine
from engines import saas_i18n as i18n
import mt_broker


# Thin wrapper around engines/saas_i18n.t() that reads the CURRENT
# user's language out of session_state -- see that module's docstring
# for why t() itself takes lang explicitly instead of reaching into
# Streamlit state on its own (it has to stay importable from the
# non-Streamlit background scheduler too). Every render_* function
# below calls this instead of hardcoding English strings directly.
def _t(key, **kwargs):
    return i18n.t(key, st.session_state.get("lang", i18n.DEFAULT_LANGUAGE), **kwargs)

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
#
# FIX 2026-09-02 (post-launch-audit): now a thin delegate to
# tenant.is_admin_email() instead of keeping its own separate
# _ADMIN_EMAILS set -- engines/saas_decision_engine.py's billing gate
# needs the exact same "is this user exempt" definition the background
# scheduler runs through, and having two independently-maintained copies
# of this logic risked them drifting apart. See tenant_engine.py's
# is_admin_email() docstring.
def _is_admin(email):
    return tenant.is_admin_email(email)


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
    page_title="OrderTrade AI | Sign In",
    page_icon="📈",
    # FIX 2026-09-09: was "centered", which caps ALL content (including
    # the data-dense logged-in dashboard -- tables, position lists,
    # performance charts) to a narrow fixed column, leaving large empty
    # margins on anything wider than a laptop screen. Switched to "wide"
    # so the dashboard actually uses the available width. The
    # single-column screens that look wrong stretched full-width (the
    # login/signup screen and the password-reset screen) are explicitly
    # re-narrowed with their own scoped max-width container below --
    # see render_auth_screen()'s and render_password_reset_screen()'s
    # "auth-body"/"reset-body" st.container(key=...) wraps.
    layout="wide",
)

# ============================================================
# SESSION STATE
# ============================================================
if "saas_user_id" not in st.session_state:
    st.session_state.saas_user_id = None
if "saas_user_email" not in st.session_state:
    st.session_state.saas_user_email = None
if "saas_session_token" not in st.session_state:
    st.session_state.saas_session_token = None
if "lang" not in st.session_state:
    # Pre-login default. A logged-in user's saved preference (see
    # tenant_engine.set_user_language()) overrides this in _log_in()
    # and the cookie-restore block below; a logged-out visitor can
    # still switch languages for the auth screen itself via the
    # selector rendered there, which just sets this directly.
    st.session_state.lang = i18n.DEFAULT_LANGUAGE


# ============================================================
# PERSISTENT LOGIN COOKIE -- added 2026-09-01 to fix users getting
# signed out after any full-page round trip to Stripe (Checkout OR the
# Billing Portal's "Return to OrderTrade AI" link) and back.
#
# Root cause: st.session_state only lives as long as the browser tab's
# WebSocket connection to this app. Navigating fully away to
# checkout.stripe.com / billing.stripe.com and back tears that
# connection down and opens a fresh one, which wipes session_state even
# though the person never clicked Log Out. Fixing that needs login to
# survive outside session_state -- a browser cookie backed by
# tenant.login_sessions (see that table's comment in tenant_engine.py).
#
# Streamlit has no server-side "set-cookie" call, so writing the cookie
# goes through a tiny injected script instead (_set_session_cookie/
# _clear_session_cookie below). Reading it back doesn't need JS, though:
# Streamlit 1.37+ exposes st.context.cookies, which reflects whatever
# cookies the browser actually sent with THIS page load's HTTP request
# -- populated correctly even on a brand-new WebSocket connection,
# unlike session_state.
# ============================================================
_SESSION_COOKIE_NAME = "ot_session"
_SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days -- matches tenant.create_login_session()


def _set_session_cookie(token):
    """SameSite=Lax (NOT Strict) is required here -- Strict cookies are
    withheld by the browser on the very cross-site top-level GET that
    Stripe's redirect back to APP_URL is, which would silently defeat
    this whole fix for exactly the case it exists to cover."""
    components.html(
        f"""<script>
        document.cookie = "{_SESSION_COOKIE_NAME}={token}; path=/; max-age={_SESSION_COOKIE_MAX_AGE}; SameSite=Lax; Secure";
        </script>""",
        height=0,
        width=0,
    )


def _clear_session_cookie():
    components.html(
        f"""<script>
        document.cookie = "{_SESSION_COOKIE_NAME}=; path=/; max-age=0; SameSite=Lax; Secure";
        </script>""",
        height=0,
        width=0,
    )


def _log_in(user_id, email):
    st.session_state.saas_user_id = user_id
    st.session_state.saas_user_email = email
    token = tenant.create_login_session(user_id)
    st.session_state.saas_session_token = token
    _set_session_cookie(token)
    _load_user_language(user_id)


def _load_user_language(user_id):
    """Pulls this user's saved display-language preference (if any)
    into session_state so every _t() call for the rest of this session
    renders in their language, not whatever the auth screen happened
    to be set to. A user who never picked one (language is NULL) stays
    on whatever session_state.lang already is -- normally English."""
    user = tenant.get_user(user_id)
    if user and user.get("language") in i18n.SUPPORTED_LANGUAGES:
        st.session_state.lang = user["language"]


def _log_out():
    if st.session_state.saas_session_token:
        tenant.delete_login_session(st.session_state.saas_session_token)
    st.session_state.saas_user_id = None
    st.session_state.saas_user_email = None
    st.session_state.saas_session_token = None
    _clear_session_cookie()


# Runs on every script execution (Streamlit reruns the whole script on
# every interaction), but only ever finds something to do the first time
# a fresh connection shows up already logged out in session_state --
# which is exactly the situation right after a Stripe round trip. Does
# NOT call _log_in() (that would reissue a brand-new cookie/token on
# every single rerun for no reason); it just repopulates session_state
# from the still-valid existing session so the rest of this run's
# rendering sees a logged-in user.
if st.session_state.saas_user_id is None:
    _cookie_token = st.context.cookies.get(_SESSION_COOKIE_NAME)
    if _cookie_token:
        _restored_user_id = tenant.get_user_id_for_login_session(_cookie_token)
        if _restored_user_id:
            _restored_user = tenant.get_user(_restored_user_id)
            if _restored_user is not None:
                st.session_state.saas_user_id = _restored_user_id
                st.session_state.saas_user_email = _restored_user["email"]
                st.session_state.saas_session_token = _cookie_token
                if _restored_user.get("language") in i18n.SUPPORTED_LANGUAGES:
                    st.session_state.lang = _restored_user["language"]


# ============================================================
# LOGGED-OUT VIEW: LOGIN / SIGN UP
# ============================================================
def render_auth_screen():
    # FIX 2026-09-09: page layout is "wide" (see st.set_page_config() call
    # above) so the logged-in dashboard's tables/charts can use the full
    # screen width -- but a login/signup form stretched edge-to-edge on a
    # wide monitor looks broken (huge text inputs, a submit button
    # spanning the whole screen). Re-narrowing just this screen with a
    # scoped max-width container, same pattern as render_legal_page()'s
    # "legal-body" container elsewhere in this file.
    st.markdown(
        """
        <style>
        .st-key-auth-body { max-width: 480px; margin: 0 auto; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="auth-body"):
        _lang_col, _ = st.columns([1, 3])
        with _lang_col:
            _lang_codes = list(i18n.SUPPORTED_LANGUAGES.keys())
            _current_lang = st.session_state.get("lang", i18n.DEFAULT_LANGUAGE)
            _picked_lang = st.selectbox(
                _t("auth.language_label"),
                options=_lang_codes,
                index=_lang_codes.index(_current_lang) if _current_lang in _lang_codes else 0,
                format_func=lambda code: i18n.SUPPORTED_LANGUAGES[code],
                key="auth_lang_picker",
                label_visibility="collapsed",
            )
            if _picked_lang != _current_lang:
                st.session_state.lang = _picked_lang
                st.rerun()

        st.caption(_t("auth.back_link", home_url=BASE_URL))
        st.title(_t("auth.title"))
        st.caption(_t("auth.tagline"))

        login_tab, signup_tab = st.tabs([_t("auth.login.submit"), _t("auth.signup.submit")])

        with login_tab:
            with st.form("login_form"):
                email = st.text_input(_t("auth.login.email_label"), key="login_email")
                password = st.text_input(_t("auth.login.password_label"), type="password", key="login_password")
                submitted = st.form_submit_button(_t("auth.login.submit"), use_container_width=True)

            if submitted:
                if not email or not password:
                    st.error(_t("auth.login.err_missing"))
                else:
                    user_id = tenant.authenticate_user(email, password)
                    if user_id is None:
                        st.error(_t("auth.login.err_invalid"))
                    else:
                        _log_in(user_id, email.strip().lower())
                        st.rerun()

            with st.expander(_t("auth.forgot.expander")):
                with st.form("forgot_password_form"):
                    forgot_email = st.text_input(_t("auth.forgot.email_label"), key="forgot_password_email")
                    forgot_submitted = st.form_submit_button(_t("auth.forgot.submit"))

                if forgot_submitted:
                    if not forgot_email:
                        st.error(_t("auth.forgot.err_missing_email"))
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
                        st.success(_t("auth.forgot.success"))

        with signup_tab:
            with st.form("signup_form"):
                new_email = st.text_input(_t("auth.signup.email_label"), key="signup_email")
                new_password = st.text_input(_t("auth.signup.password_label"), type="password", key="signup_password")
                confirm_password = st.text_input(
                    _t("auth.signup.confirm_password_label"), type="password", key="signup_confirm"
                )
                new_phone = st.text_input(
                    _t("auth.signup.phone_label"), key="signup_phone"
                )
                new_country = st.selectbox(
                    _t("auth.signup.country_label"), options=COUNTRY_OPTIONS, key="signup_country"
                )
                agreed_to_terms = st.checkbox(
                    _t("auth.signup.agree_terms"),
                    key="signup_agree_terms",
                )
                signup_submitted = st.form_submit_button(_t("auth.signup.submit"), use_container_width=True)

            if signup_submitted:
                if not new_email or not new_password:
                    st.error(_t("auth.signup.err_missing"))
                elif len(new_password) < 8:
                    st.error(_t("auth.signup.err_short_password"))
                elif new_password != confirm_password:
                    st.error(_t("auth.signup.err_mismatch"))
                elif not agreed_to_terms:
                    st.error(_t("auth.signup.err_must_agree"))
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
                        st.error(_t("auth.signup.err_exists"))
                    else:
                        # Carry the language picked on this screen into the
                        # new account so it's not lost the moment they log in
                        # (_log_in() below would otherwise overwrite
                        # session_state.lang with the account's saved NULL
                        # preference, silently reverting them to English).
                        _picked_lang = st.session_state.get("lang", i18n.DEFAULT_LANGUAGE)
                        if _picked_lang != i18n.DEFAULT_LANGUAGE:
                            tenant.set_user_language(user_id, _picked_lang)
                        # Log in immediately (don't gate account access on
                        # verification -- that would strand a user with a
                        # broken/slow mail delivery). The dashboard shows a
                        # persistent banner with a resend option until
                        # email_verified flips to True. See render_dashboard().
                        try:
                            verify_token = tenant.create_email_verification_token(user_id)
                            verify_url = f"{APP_URL}/?verify_token={verify_token}"
                            email_engine.send_verification_email(new_email, verify_url)
                            st.success(_t("auth.signup.success_verify"))
                        except Exception:
                            st.success(_t("auth.signup.success_plain"))
                            st.warning(_t("auth.signup.warn_email_failed"))
                        _log_in(user_id, new_email.strip().lower())
                        st.rerun()

        st.caption(_t("auth.footer_links"))


# ============================================================
# LIVE TRADING SWITCH (Lock 1 of the two-lock design -- see engines/
# tenant_engine.py's set_live_trading_status() docstring). Rendered
# above render_broker_connections() so that function can read
# user_settings.allow_live_trading and decide whether to offer a
# live-vs-demo environment choice for each broker. Flipping this flag
# on its own does NOT let any order reach a real account -- that is a
# second, independent gate inside engines/saas_broker_factory.py,
# changed broker-by-broker (Tasks #302-305), never as a side effect of
# this switch.
# ============================================================
def render_live_trading_switch(user_id):
    settings = tenant.get_user_settings(user_id)
    if settings is None:
        return
    allow_live = settings.get("allow_live_trading", False)

    st.subheader(_t("live.header"))

    # FEATURE 2026-09-09 (task #306): tenant_engine.get_live_trading_
    # audit_log() has existed since the two-lock design was built
    # (set_live_trading_status() has always written to it) but was
    # never actually surfaced anywhere in this dashboard -- a user had
    # no way to see their own history of switching live trading on/off,
    # or confirm exactly when/why it changed. Shown collapsed and only
    # when there's at least one entry, so a user who has never touched
    # this setting doesn't see an empty, pointless expander.
    audit_entries = tenant.get_live_trading_audit_log(user_id)
    if audit_entries:
        with st.expander(_t("live.audit_expander", count=len(audit_entries))):
            for entry in audit_entries:
                status_label = _t("live.audit_status_on") if entry["allow_live_trading"] else _t("live.audit_status_off")
                st.markdown(f"**{entry['created_at']}**: {status_label}")
                if entry.get("reason"):
                    st.caption(entry["reason"])

    if allow_live:
        st.success(_t("live.status_on"))
        with st.expander(_t("live.revert_expander")):
            st.warning(_t("live.revert_warning"))
            revert_confirm_text = st.text_input(
                _t("live.revert_confirm_label"), key="live_revert_confirm_text"
            )
            revert_phrase_matches = (
                revert_confirm_text.strip().upper() == _t("live.revert_confirm_phrase").upper()
            )
            if st.button(
                _t("live.revert_button"), key="live_revert_btn", disabled=not revert_phrase_matches
            ):
                tenant.set_live_trading_status(
                    user_id, False, reason="user reverted to demo via dashboard"
                )
                st.success(_t("live.revert_success"))
                st.rerun()
        return

    st.info(_t("live.status_off"))
    with st.expander(_t("live.switch_expander")):
        st.warning(_t("live.risk_intro"))
        ack_no_guarantee = st.checkbox(_t("live.ack_no_guarantee"), key="live_ack_no_guarantee")
        ack_responsible = st.checkbox(_t("live.ack_responsible"), key="live_ack_responsible")
        ack_bug_risk = st.checkbox(_t("live.ack_bug_risk"), key="live_ack_bug_risk")
        ack_tos = st.checkbox(_t("live.ack_tos"), key="live_ack_tos")
        all_acked = ack_no_guarantee and ack_responsible and ack_bug_risk and ack_tos

        confirm_phrase = _t("live.confirm_phrase")
        st.caption(_t("live.confirm_instruction", phrase=confirm_phrase))
        confirm_text = st.text_input(_t("live.confirm_label"), key="live_confirm_text")
        phrase_matches = confirm_text.strip().upper() == confirm_phrase.upper()

        if st.button(
            _t("live.switch_button"),
            key="live_switch_btn",
            disabled=not (all_acked and phrase_matches),
        ):
            tenant.set_live_trading_status(
                user_id, True, reason="user confirmed live-trading switch via dashboard"
            )
            st.success(_t("live.switch_success"))
            st.rerun()


# ============================================================
# LOGGED-IN VIEW: DASHBOARD (broker connections + settings)
# ============================================================
_BROKER_FIELDS = {
    "ALPACA": {
        "label": "Alpaca (US Stocks, Paper)",
        "environment": "paper",
        "key_label": "API Key ID",
        "secret_label": "Secret Key",
        "has_extra": False,
    },
    "BINANCE": {
        "label": "Binance (Crypto, Testnet)",
        "environment": "testnet",
        "key_label": "API Key",
        "secret_label": "Secret Key",
        "has_extra": False,
    },
    "ETORO": {
        "label": "eToro (Forex/Commodities, Demo)",
        "environment": "demo",
        "key_label": "API Key",
        "secret_label": "User Key",
        "has_extra": False,
    },
}


# Brokers whose execution path in engines/saas_broker_factory.py has
# actually been changed to read the stored credential's environment
# (double-gated by user_settings.allow_live_trading -- see that file's
# SAFETY docstring). Task #302 = ALPACA, task #303 = BINANCE, task #304
# = ETORO; #305 adds MT_BRIDGE to this set once done -- do NOT add a
# broker here before its execution functions are actually wired,
# otherwise a user could save "live" credentials that still silently
# trade paper. That fails safe (never a real order) but is confusing
# and undermines trust in the switch actually doing what it says.
def render_broker_connections(user_id):
    st.subheader(_t("broker.header"))
    st.caption(_t("broker.caption"))

    live_capable_brokers = {"ALPACA", "BINANCE", "ETORO"}
    settings = tenant.get_user_settings(user_id)
    allow_live = bool(settings and settings.get("allow_live_trading"))

    connected = {c["broker"]: c for c in tenant.list_connected_brokers(user_id)}

    for broker_code, meta in _BROKER_FIELDS.items():
        status = connected.get(broker_code)
        status_text = (
            _t("broker.status_connected", environment=status['environment'], date=status['updated_at'][:10])
            if status else _t("broker.status_not_connected")
        )

        with st.expander(_t("broker.expander_title", broker=meta['label'], status=status_text)):
            environment_to_save = meta["environment"]

            # Live/demo choice per broker (Lock 2) -- only offered once (a)
            # the account-level switch (Lock 1) is on, AND (b) this
            # specific broker's execution path actually honors it. See
            # live_capable_brokers above and engines/saas_broker_
            # factory.py's SAFETY docstring for the full two-lock design
            # this mirrors.
            #
            # FIX (found via live-fire UI test, same day): this used to be
            # rendered INSIDE the st.form() below, alongside the credential
            # inputs. Streamlit widgets inside a form do not trigger a
            # rerun on change -- only st.form_submit_button() does -- so
            # the "this will connect your REAL account" warning never
            # appeared until AFTER the user had already clicked Save,
            # defeating the entire point of warning them first. Rendering
            # the radio here, outside the form, makes it a normal reactive
            # widget: selecting "Live" reruns immediately and shows the
            # warning before the user ever gets to the credential fields
            # or the Save button.
            env_choice = "demo"
            if allow_live and broker_code in live_capable_brokers:
                env_choice = st.radio(
                    _t("live.env_choice_label"),
                    options=["demo", "live"],
                    format_func=lambda v: _t("live.env_demo_option") if v == "demo" else _t("live.env_live_option"),
                    key=f"{broker_code}_env_choice",
                    horizontal=True,
                )
                if env_choice == "live":
                    # meta["label"] includes the demo/paper parenthetical
                    # ("Alpaca (US Stocks, Paper)") which reads as
                    # self-contradictory right inside a "this is REAL"
                    # warning -- strip it down to just the broker name.
                    broker_short_name = meta["label"].split(" (")[0]
                    st.warning(_t("live.env_live_warning", broker=broker_short_name))
                environment_to_save = meta["environment"] if env_choice == "demo" else "live"

            with st.form(f"broker_form_{broker_code}"):
                api_key = st.text_input(meta["key_label"], type="password", key=f"{broker_code}_key")
                api_secret = st.text_input(meta["secret_label"], type="password", key=f"{broker_code}_secret")
                save_clicked = st.form_submit_button(_t("broker.save_button"))

            if save_clicked:
                if not api_key or not api_secret:
                    st.error(_t("broker.err_required"))
                else:
                    # FIX 2026-09-03 (post-launch-audit Moderate finding):
                    # credentials used to be saved with no validation at
                    # all -- a typo'd key would sit there until the
                    # scheduler (or a manual Test Connection click) hit
                    # it later. Now every save immediately test-connects
                    # with a real API call; a bad save is caught right
                    # here and rolled back instead of silently persisting.
                    previous_creds = tenant.get_broker_credentials(user_id, broker_code)
                    tenant.save_broker_credentials(
                        user_id,
                        broker=broker_code,
                        environment=environment_to_save,
                        api_key=api_key,
                        api_secret=api_secret,
                    )
                    with st.spinner(_t("broker.verifying_spinner", label=meta['label'])):
                        check = saas_broker_factory.check_user_broker_connection(user_id, broker_code)
                    if check.get("connected"):
                        st.success(_t("broker.success_saved", broker=meta['label']))
                        st.rerun()
                    elif check.get("status") == "unavailable":
                        # FIX 2026-09-03 (#258): "unavailable" means the
                        # check itself hit a network/timeout/rate-limit
                        # issue on Alpaca's/Binance's/eToro's side, not
                        # necessarily a bad key -- see
                        # engines/broker_error_messages.broker_error_
                        # status()'s docstring. Previously ANY failed
                        # check rolled back here, which meant a purely
                        # transient blip could silently discard perfectly
                        # valid, just-entered credentials and blame the
                        # user's input. The credentials just saved above
                        # are deliberately left in place -- not rolled
                        # back -- and the scheduler/a manual Test
                        # Connection retry will pick them up once the
                        # broker is reachable again.
                        st.warning(
                            _t("broker.warn_temp_issue", icon=check.get('error'), broker=meta['label'])
                        )
                    else:
                        # FIX 2026-09-03 (#257): check.get("error") is now
                        # already a clean, user-safe sentence from
                        # engines/broker_error_messages.py -- shown as-is
                        # rather than wrapped in a second "Could not
                        # connect..." prefix (the raw SDK/API text is
                        # still logged server-side by that helper).
                        if previous_creds is not None:
                            tenant.save_broker_credentials(
                                user_id,
                                broker=broker_code,
                                environment=previous_creds["environment"],
                                api_key=previous_creds["api_key"],
                                api_secret=previous_creds["api_secret"],
                                extra=previous_creds["extra"],
                            )
                            st.error(
                                _t("broker.err_kept_unchanged", icon=check.get('error'), broker=meta['label'])
                            )
                        else:
                            tenant.delete_broker_credentials(user_id, broker_code)
                            st.error(
                                _t("broker.err_nothing_saved", icon=check.get('error'))
                            )

            if status:
                if st.button(_t("broker.test_connection"), key=f"test_{broker_code}"):
                    result = saas_broker_factory.check_user_broker_connection(user_id, broker_code)
                    if result.get("connected"):
                        st.success(
                            _t(
                                "broker.success_connected",
                                cash=f"{result.get('cash', 0):,.2f}",
                                equity=f"{result.get('equity', result.get('cash', 0)):,.2f}",
                            )
                        )
                    elif result.get("status") == "unavailable":
                        # FIX 2026-09-03 (#258): distinguish a likely-
                        # transient network/timeout/rate-limit hiccup from
                        # a genuine credential failure -- see
                        # check_user_alpaca_connection()'s #258 comment.
                        st.warning(result.get("error"))
                    else:
                        st.error(result.get("error"))

    render_mt_bridge_connection(user_id, connected)


def render_mt_bridge_connection(user_id, connected):
    """
    MT4/MT5 connect form -- added 2026-09-02 (Phase 2 of the MT4/5 bridge,
    see mt_broker.py's module docstring). Kept separate from the
    _BROKER_FIELDS-driven loop above rather than shoehorned into it:
    every other broker there needs exactly two credential fields (an API
    key + a secret), but MT4/5 needs four (login, password, server,
    platform) since it authenticates against a specific broker server,
    not a single global API endpoint -- see mt_broker.save_mt_credentials()
    for why each of these is required.

    Alternative to eToro for FOREX/COMMODITIES, not a replacement -- a
    user can have either, both, or neither connected; see
    saas_decision_engine.py's _resolve_broker_for_asset_class() for how
    that choice is resolved per user at trade time.
    """
    status = connected.get("MT_BRIDGE")
    status_text = (
        _t("broker.status_connected", environment=status['environment'], date=status['updated_at'][:10])
        if status else _t("broker.status_not_connected")
    )

    with st.expander(_t("mt.expander_title", status=status_text)):
        st.caption(_t("mt.caption"))
        with st.form("broker_form_MT_BRIDGE"):
            mt_login = st.text_input(_t("mt.login_label"), key="MT_BRIDGE_login")
            mt_password = st.text_input(
                _t("mt.password_label"), type="password", key="MT_BRIDGE_password",
                help=_t("mt.password_help"),
            )
            mt_server = st.text_input(
                _t("mt.server_label"), key="MT_BRIDGE_server",
                help=_t("mt.server_help"),
            )
            mt_platform = st.selectbox(_t("mt.platform_label"), options=["mt5", "mt4"], key="MT_BRIDGE_platform")
            mt_save_clicked = st.form_submit_button(_t("mt.save_button"))

        if mt_save_clicked:
            if not mt_login or not mt_password or not mt_server:
                st.error(_t("mt.err_required"))
            else:
                # environment="demo" here is only ever a PLACEHOLDER until
                # this account has actually been connected once -- MT4/5
                # has no user-chosen live/demo mode the way Alpaca/
                # Binance/eToro do (see mt_broker.py's module docstring
                # LIVE TRADING GATE section, task #305). mt_broker.py's
                # check_user_mt_connection() self-corrects this stored
                # label to whatever MetaApi's account_information.type
                # actually says the first time "Test Connection" below
                # succeeds (and again on every trade attempt) -- this
                # label is NEVER what decides whether real orders are
                # allowed; that decision is re-verified fresh from
                # MetaApi on every single trade, not from this stored
                # value.
                mt_broker.save_mt_credentials_sync(
                    user_id, mt_login, mt_password, mt_server,
                    platform=mt_platform, environment="demo",
                )
                st.success(_t("mt.success_saved"))
                st.rerun()

        if status:
            # FIX 2026-09-02 (task #238 follow-up): a brand-new MT4/5
            # account's first-ever broker connection can take many
            # minutes (live-tested: ~16 min end-to-end) -- mt_broker.py's
            # check_user_mt_connection() now returns fast with
            # status="deploying" during that window instead of blocking
            # or reporting a hard failure (see its docstring). This used
            # to show the raw exception text as "Connection failed" for
            # a still-deploying account, which read as broken when it
            # wasn't -- now branches on `status` to show a "still
            # connecting" message with a retry button instead.
            if st.button(_t("mt.test_connection"), key="test_MT_BRIDGE"):
                with st.spinner(_t("mt.checking_spinner")):
                    result = saas_broker_factory.check_user_mt_bridge_connection(user_id)
                st.session_state["mt_bridge_last_check"] = result

            last_result = st.session_state.get("mt_bridge_last_check")
            if last_result:
                if last_result.get("connected"):
                    st.success(
                        _t(
                            "mt.success_connected",
                            platform=last_result.get('broker_name', _t("mt.default_broker_name")),
                            balance=f"{last_result.get('cash', 0):,.2f}",
                            equity=f"{last_result.get('equity', 0):,.2f}",
                        )
                    )
                    # FIX 2026-09-08 (task #305, LIVE TRADING GATE): tell
                    # the user honestly whether MetaApi reports this as a
                    # real-money account -- mt_broker.py's
                    # check_user_mt_connection() detects this from
                    # account_information.type, NOT from anything the
                    # user chose in this form (there is no such choice
                    # for MT4/5 -- see that file's module docstring).
                    # This is purely informational: the actual block on
                    # real orders is enforced server-side in
                    # mt_broker.py regardless of whether the user ever
                    # sees this message.
                    if last_result.get("environment") == "live":
                        settings = tenant.get_user_settings(user_id)
                        if settings and settings.get("allow_live_trading"):
                            st.info(_t("mt.live_account_enabled"))
                        else:
                            st.warning(_t("mt.live_account_locked"))
                elif last_result.get("status") == "deploying":
                    st.info(_t("mt.info_still_connecting"))
                else:
                    # FIX 2026-09-03 (#257): last_result["error"] is
                    # already a clean, user-safe message from
                    # engines/broker_error_messages.py, not raw MetaApi
                    # SDK exception text.
                    st.error(last_result.get("error"))


def render_pause_control(user_id):
    """
    FIX 2026-09-04 (#264): the pause/resume kill switch used to live
    inside render_settings(), several sections down the dashboard --
    below broker connections and open positions -- so a user who
    urgently wanted to stop new trades had to scroll past all of that
    first. It's now rendered here, at the very top of the dashboard,
    right after the header, so it's the first interactive control
    visible after login regardless of which tab or how far the rest of
    the page has scrolled. Still reads/writes the exact same
    settings.trading_paused row that render_settings() and
    engines/saas_decision_engine.py use -- this is a relocation, not a
    new mechanism. Blocks new BUY evaluation only; stop-loss/
    take-profit/time-based exits on positions already held keep running
    even while paused.
    """
    settings = tenant.get_user_settings(user_id)
    if settings is None:
        return

    is_paused = settings.get("trading_paused", False)
    status_col, button_col = st.columns([4, 1])
    with status_col:
        if is_paused:
            st.error(_t("pause.paused"))
        else:
            st.success(_t("pause.active"))
    with button_col:
        st.write("")
        pause_label = _t("pause.resume_button") if is_paused else _t("pause.pause_button")
        if st.button(pause_label, key="toggle_trading_paused_top", use_container_width=True):
            tenant.save_user_settings(user_id, trading_paused=not is_paused)
            st.rerun()

    st.divider()


def render_settings(user_id):
    st.subheader(_t("settings.header"))

    settings = tenant.get_user_settings(user_id)
    if settings is None:
        st.error(_t("settings.err_load"))
        return

    st.info(_t("settings.info_paper_only"))

    # FIX 2026-09-04 (#264): the pause/resume toggle itself now lives in
    # render_pause_control() at the top of the dashboard (see that
    # function's docstring) -- this is just a pointer so a user who
    # lands directly on this section isn't confused by its absence.
    is_paused = settings.get("trading_paused", False)
    pause_status = _t("settings.pause_status_paused") if is_paused else _t("settings.pause_status_active")
    st.caption(_t("settings.pause_caption", status=pause_status))

    with st.form("settings_form"):
        max_position_size = st.slider(
            _t("settings.max_position_label"),
            min_value=5, max_value=50,
            value=int(settings["max_position_size"] * 100),
            step=5,
        )
        enabled_classes = st.multiselect(
            _t("settings.asset_classes_label"),
            options=["US_STOCKS", "CRYPTO", "FOREX", "COMMODITIES"],
            default=settings["enabled_asset_classes"],
        )
        save_settings_clicked = st.form_submit_button(_t("settings.save_button"))

    if save_settings_clicked:
        tenant.save_user_settings(
            user_id,
            max_position_size=max_position_size / 100,
            enabled_asset_classes=enabled_classes,
        )
        st.success(_t("settings.success_saved"))
        st.rerun()


def render_account_settings(user):
    st.subheader(_t("account.header"))

    with st.expander(_t("account.profile_expander")):
        with st.form("profile_form"):
            phone = st.text_input(
                _t("account.phone_label"), value=user.get("phone") or "", key="profile_phone"
            )
            current_country = user.get("country") or "Prefer not to say"
            country_index = (
                COUNTRY_OPTIONS.index(current_country)
                if current_country in COUNTRY_OPTIONS else 0
            )
            country = st.selectbox(
                _t("account.country_label"), options=COUNTRY_OPTIONS,
                index=country_index, key="profile_country",
            )
            profile_saved = st.form_submit_button(_t("account.save_profile_button"))

        if profile_saved:
            tenant.update_profile_fields(
                user["user_id"],
                phone=phone,
                country=(country if country != "Prefer not to say" else ""),
            )
            st.success(_t("account.success_profile_updated"))
            st.rerun()

    with st.expander(_t("account.language_expander")):
        _lang_codes = list(i18n.SUPPORTED_LANGUAGES.keys())
        _current_lang = user.get("language") or st.session_state.get("lang", i18n.DEFAULT_LANGUAGE)
        with st.form("language_form"):
            picked_lang = st.selectbox(
                _t("account.language_select_label"),
                options=_lang_codes,
                index=_lang_codes.index(_current_lang) if _current_lang in _lang_codes else 0,
                format_func=lambda code: i18n.SUPPORTED_LANGUAGES[code],
                key="account_lang_picker",
            )
            language_saved = st.form_submit_button(_t("account.language_save_button"))

        if language_saved:
            tenant.set_user_language(user["user_id"], picked_lang)
            st.session_state.lang = picked_lang
            st.success(_t("account.language_saved"))
            st.rerun()

    with st.expander(_t("account.change_email_expander")):
        st.caption(_t("account.change_email_caption", email=user['email']))
        with st.form("change_email_form"):
            new_email = st.text_input(_t("account.new_email_label"), key="change_email_new")
            current_password = st.text_input(
                _t("account.current_password_label"), type="password", key="change_email_password"
            )
            change_submitted = st.form_submit_button(_t("account.send_confirmation_button"))

        if change_submitted:
            if not new_email or not current_password:
                st.error(_t("account.err_missing"))
            elif tenant.authenticate_user(user["email"], current_password) is None:
                st.error(_t("account.err_wrong_password"))
            elif new_email.strip().lower() == user["email"]:
                st.error(_t("account.err_same_email"))
            else:
                token = tenant.request_email_change(user["user_id"], new_email)
                if token is None:
                    st.error(_t("account.err_email_in_use"))
                else:
                    try:
                        confirm_url = f"{APP_URL}/?change_email_token={token}"
                        email_engine.send_email_change_confirmation(new_email, confirm_url)
                        st.success(
                            _t("account.success_confirmation_sent", email=new_email.strip().lower())
                        )
                    except Exception:
                        print("[account] send_email_change_confirmation failed:")
                        traceback.print_exc()
                        st.error(_t("account.err_send_failed"))


def render_trading_run(user_id):
    st.subheader(_t("trading.header"))
    st.caption(_t("trading.caption"))

    preview_clicked = st.button(_t("trading.preview_button"), key="preview_signals")
    if preview_clicked:
        with st.spinner(_t("trading.preview_spinner")):
            st.session_state.saas_preview_results = (
                saas_decision_engine.run_decision_loop_for_user(user_id, dry_run=True)
            )
        st.session_state.saas_preview_ran_for = user_id

    results = st.session_state.get("saas_preview_results")
    if results is not None and st.session_state.get("saas_preview_ran_for") == user_id:
        # FIX 2026-09-05 (feature-parity gap flagged by user): every row
        # here now carries the raw signal-engine fields (price, confidence,
        # trend, strategy -- see saas_decision_engine._signal_snapshot())
        # regardless of whether it was skipped/rejected/approved. Renamed/
        # reordered here purely for readability -- same underlying data
        # saas_decision_engine.py already returns, nothing recomputed.
        _column_renames = {
            "ticker": _t("col.ticker"), "asset_class": _t("col.asset_class"), "action": _t("col.action"),
            "signal": _t("col.signal"), "price": _t("col.price_usd"), "daily_change_pct": _t("col.daily_change_pct"),
            "confidence": _t("col.ai_confidence_pct"), "trend_score": _t("col.trend_score"),
            "trend_details": _t("col.trend_details"), "strategy": _t("col.strategy"),
            "strategy_score": _t("col.strategy_score"), "ai_trade_score": _t("col.ai_trade_score"),
            "risk_reward": _t("col.risk_reward"), "trade_grade": _t("col.trade_grade"),
            "stop_loss": _t("col.stop_loss"), "take_profit": _t("col.take_profit"),
            "trade_amount": _t("col.trade_amount_usd"), "message": _t("col.message"),
        }
        _column_order = [
            _t("col.ticker"), _t("col.signal"), _t("col.action"), _t("col.price_usd"), _t("col.daily_change_pct"),
            _t("col.ai_confidence_pct"), _t("col.trend_score"), _t("col.strategy"), _t("col.ai_trade_score"),
            _t("col.risk_reward"), _t("col.trade_grade"), _t("col.trade_amount_usd"), _t("col.asset_class"),
            _t("col.message"),
        ]
        df = pd.DataFrame(results).rename(columns=_column_renames)
        df = df[[c for c in _column_order if c in df.columns]]
        st.dataframe(df, use_container_width=True)

        buy_candidates = [r for r in results if r["action"] == "would_buy"]
        sell_candidates = [r for r in results if r["action"] == "would_sell"]
        if buy_candidates or sell_candidates:
            parts = []
            if buy_candidates:
                parts.append(_t("trading.buy_count", count=len(buy_candidates)))
            if sell_candidates:
                parts.append(_t("trading.sell_count", count=len(sell_candidates)))
            st.warning(
                _t("trading.warn_real_orders", count=_t("trading.and_joiner").join(parts))
            )
            confirm = st.checkbox(
                _t("trading.confirm_checkbox"),
                key="saas_execute_confirm",
            )
            if st.button(_t("trading.execute_button"), disabled=not confirm, key="execute_trades"):
                with st.spinner(_t("trading.execute_spinner")):
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
                    st.success(_t("trading.success_bought", count=len(bought)))
                if sold:
                    st.success(_t("trading.success_sold", count=len(sold)))
                if reconciled:
                    st.info(_t("trading.info_confirmed", count=len(reconciled)))
                if pending:
                    st.warning(_t("trading.warn_pending", count=len(pending)))
                if failed:
                    st.error(_t("trading.err_failed", count=len(failed)))
        else:
            # FIX 2026-09-05 (feature-parity gap flagged by user): this used
            # to just say "No approved BUY candidates or exit triggers right
            # now" with zero context -- indistinguishable from "the AI found
            # nothing interesting" and "the AI found real BUY signals but
            # every single one got blocked by a position cap/RR/already-
            # holding gate", which are very different situations for a user
            # to see. app.py's own dashboard (single-owner bot) always
            # separates these two cases ("detected raw signals but none
            # passed execution filters" vs "no strong signal available").
            # Mirrors that here using the raw "signal" field every row now
            # carries (see _signal_snapshot() in saas_decision_engine.py),
            # not just the post-filter "action" field.
            raw_buy_count = sum(1 for r in results if r.get("signal") == "BUY")
            if raw_buy_count > 0:
                st.info(_t("trading.info_blocked_signals", count=raw_buy_count))
            else:
                st.info(_t("trading.info_no_signal"))


_BROKER_DISPLAY_NAMES = {
    "ALPACA": "Alpaca",
    "BINANCE": "Binance",
    "ETORO": "eToro",
    "MT_BRIDGE": "MT4/5",
}


def _broker_display_name(broker_code):
    """Short display name for My Positions' Broker column -- deliberately
    kept separate from _BROKER_FIELDS above (which only drives the
    generic connect-form loop and excludes MT_BRIDGE on purpose, since
    MT_BRIDGE has its own dedicated 4-field form via
    render_mt_bridge_connection()).

    FIX 2026-09-02 (post-launch-audit): render_open_positions() used to
    call _BROKER_FIELDS[broker_code]["label"] directly, which raised an
    unhandled KeyError for any user with an open MT4/5 position --
    MT_BRIDGE was never added to that dict, and this call sat OUTSIDE
    the try/except that already guards the position-fetch above it.
    Streamlit has no error boundary around a render function, so this
    crashed the user's ENTIRE dashboard, not just the positions table.
    Falls back to the raw broker_code for anything not explicitly listed
    here so a future 5th broker can't reproduce this same crash even if
    this map isn't updated in lockstep with _BROKER_FIELDS.
    """
    return _BROKER_DISPLAY_NAMES.get(broker_code, broker_code)


@st.cache_data(ttl=20, show_spinner=False)
def _cached_broker_positions(user_id, broker_code):
    """Cached wrapper around saas_broker_factory.get_user_open_positions_or_error().

    PERF FIX 2026-09-05: this used to be called live, uncached, on EVERY
    Streamlit rerun -- which means every single page load AND every
    button/widget click ANYWHERE on the dashboard (Streamlit reruns the
    whole script top-to-bottom on any interaction) re-hit every connected
    broker's live API, sequentially, for every user. On a single-vCPU
    droplet that's the dominant cost behind "the dashboard feels slow" and
    it gets strictly worse as more users sign up (more concurrent live
    broker round-trips serialized on one core). A short TTL keeps
    positions fresh enough (still-open PnL only meaningfully changes on
    the order of seconds-minutes, not on every widget click) while
    collapsing repeated reruns within the same ~20s window into one real
    broker call. See get_user_open_positions_or_error()'s own docstring
    for why failures are disambiguated from "genuinely zero positions".
    """
    try:
        return saas_broker_factory.get_user_open_positions_or_error(user_id, broker_code)
    except Exception:
        return [], _t("positions.fetch_error_generic")


def render_open_positions(user_id):
    st.subheader(_t("positions.header"))
    st.caption(_t("positions.caption"))

    connected_brokers = [c["broker"] for c in tenant.list_connected_brokers(user_id)]
    if not connected_brokers:
        st.info(_t("positions.info_connect_broker"))
        return

    all_positions = []
    fetch_failures = []
    # PERF FIX 2026-09-05: these are independent I/O-bound calls to
    # different brokers' APIs -- previously run one after another in a
    # plain for-loop, so a user with 3 connected brokers paid the sum of
    # all 3 round-trips in series. Fetching them concurrently means the
    # wall-clock cost is roughly the SLOWEST broker, not the sum of all
    # of them. Safe to parallelize: each call only reads that one
    # broker's account for this one user_id, no shared mutable state.
    with ThreadPoolExecutor(max_workers=max(1, len(connected_brokers))) as pool:
        future_to_broker = {
            pool.submit(_cached_broker_positions, user_id, broker_code): broker_code
            for broker_code in connected_brokers
        }
        results_by_broker = {}
        for future in future_to_broker:
            broker_code = future_to_broker[future]
            try:
                results_by_broker[broker_code] = future.result()
            except Exception:
                results_by_broker[broker_code] = ([], _t("positions.fetch_error_generic"))

    # Preserve the original connected_brokers ordering for a stable display.
    for broker_code in connected_brokers:
        positions, fetch_error = results_by_broker[broker_code]
        if fetch_error:
            fetch_failures.append((_broker_display_name(broker_code), fetch_error))
        for p in positions:
            all_positions.append({_t("col.broker"): _broker_display_name(broker_code), **p})

    for broker_label, fetch_error in fetch_failures:
        st.warning(
            _t("positions.warn_fetch_failed", broker=broker_label, error=fetch_error)
        )

    if not all_positions:
        if fetch_failures:
            st.info(_t("positions.info_no_other"))
        else:
            st.info(_t("positions.info_none"))
        return

    # FIX 2026-09-09 (eToro-inspired redesign): a top summary bar --
    # total cost basis, total unrealized P&L, and the resulting current
    # value -- computed from the raw per-broker dicts BEFORE the
    # column-renaming below.
    #
    # FIX 2026-09-14 (Total Invested showing ~$272K instead of real
    # committed capital): this used to compute entry_price * quantity
    # uniformly for every broker. That's only correct for Alpaca/Binance,
    # where "quantity" is a real share/unit count. For eToro, "quantity"
    # is already the dollar margin invested (not a share count), so
    # multiplying it by entry_price again produced a nonsense inflated
    # figure; for MT4/5, "quantity" is lots, so open_price * lots isn't a
    # dollar amount at all. Each broker's fetcher in saas_broker_factory.py
    # now exposes its own correctly-computed invested_amount (see that
    # file's _get_etoro_open_positions()/_get_mt_bridge_open_positions()
    # FIX comments), so this just sums whatever each one already got
    # right rather than re-deriving it here per-broker.
    positions_missing_invested = [
        p for p in all_positions if p.get("invested_amount") is None
    ]
    total_invested = sum(p.get("invested_amount") or 0 for p in all_positions)
    total_unrealized_pnl = sum(p.get("unrealized_pnl") or 0 for p in all_positions)
    total_current_value = total_invested + total_unrealized_pnl
    # FIX 2026-09-09b: each stat wrapped in its own st.container(border=True)
    # -- a real bordered card box, same as eToro's own stat bar -- instead
    # of a bare st.metric() floating with no visual boundary. Streamlit
    # 1.58 (this project's pinned version) supports border=True natively.
    summary_cols = st.columns(3)
    with summary_cols[0].container(border=True):
        st.metric(_t("positions.metric_invested"), f"${total_invested:,.2f}")
    with summary_cols[1].container(border=True):
        st.metric(
            _t("positions.metric_unrealized_pnl"),
            f"${total_unrealized_pnl:,.2f}",
            delta=f"{total_unrealized_pnl:,.2f}",
        )
    with summary_cols[2].container(border=True):
        st.metric(_t("positions.metric_current_value"), f"${total_current_value:,.2f}")

    df = pd.DataFrame(all_positions)
    df = df.rename(columns={
        "ticker": _t("col.ticker"),
        "quantity": _t("col.quantity"),
        "entry_price": _t("col.entry_price"),
        "current_price": _t("col.current_price"),
        "unrealized_pnl": _t("col.unrealized_pnl_usd"),
        "unrealized_pnl_pct": _t("col.unrealized_pnl_pct"),
        "stop_loss": _t("col.stop_loss"),
        "take_profit": _t("col.take_profit"),
    })
    column_order = [_t("col.broker"), _t("col.ticker"), _t("col.quantity"), _t("col.entry_price"), _t("col.current_price"),
                     _t("col.unrealized_pnl_usd"), _t("col.unrealized_pnl_pct"), _t("col.stop_loss"), _t("col.take_profit")]
    df = df[[c for c in column_order if c in df.columns]]

    # FIX 2026-09-09: color the two P&L columns green/red (eToro's own
    # History view does the same for its P&L($) column) instead of
    # leaving every row the same neutral color -- the sign is the first
    # thing a user scans for, and a plain dataframe made them read every
    # number to find it. pandas Styler renders inside st.dataframe
    # without needing any custom HTML/JS.
    def _color_pnl(value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return ""
        if v > 0:
            return "color: #2ecc71; font-weight: 600;"
        if v < 0:
            return "color: #ff4d4f; font-weight: 600;"
        return ""

    pnl_cols = [c for c in (_t("col.unrealized_pnl_usd"), _t("col.unrealized_pnl_pct")) if c in df.columns]
    styled_df = df.style.map(_color_pnl, subset=pnl_cols) if pnl_cols else df
    st.dataframe(styled_df, use_container_width=True, hide_index=True)

    if "eToro" in df[_t("col.broker")].values:
        st.caption(_t("positions.caption_etoro_pnl_note"))

    # FIX 2026-09-14: an MT4/5 position whose original entry order was
    # never journaled (e.g. adopted/reconciled rather than opened through
    # the normal buy path) has no trade_amount to report -- surfaced
    # explicitly rather than silently undercounting Total Invested above.
    if positions_missing_invested:
        st.caption(
            f"Total Invested excludes {len(positions_missing_invested)} "
            f"position(s) with no recorded entry amount -- check the "
            f"broker's own app for their exact cost."
        )


# ============================================================
# PERFORMANCE / P&L (post-launch-audit #262)
# Realized (closed-trade) P&L, separate from "My Positions" above,
# which only ever shows live UNREALIZED P&L on still-open positions.
# See engines/saas_performance_engine.py's module docstring for the
# FIFO-matching approach and the leveraged-broker ($ figures excluded
# for eToro/MT4-5) caveat.
# ============================================================
@st.cache_data(ttl=30, show_spinner=False)
def _cached_performance_metrics(user_id):
    return saas_performance_engine.calculate_performance_metrics_for_user(user_id)


@st.cache_data(ttl=30, show_spinner=False)
def _cached_pnl_by_asset_class(user_id):
    return saas_performance_engine.calculate_pnl_by_asset_class_for_user(user_id)


@st.cache_data(ttl=30, show_spinner=False)
def _cached_pnl_by_exit_strategy(user_id):
    return saas_performance_engine.calculate_pnl_by_exit_strategy_for_user(user_id)


@st.cache_data(ttl=30, show_spinner=False)
def _cached_monthly_pnl(user_id):
    return saas_performance_engine.calculate_monthly_pnl_for_user(user_id)


def render_performance_view(user_id):
    st.subheader(_t("perf.header"))
    st.caption(_t("perf.caption"))

    # PERF FIX 2026-09-05: these 4 calls each independently re-read and
    # re-compute FIFO-matched P&L from the user's full order history on
    # every single Streamlit rerun (every page load/click anywhere on the
    # dashboard), even though closed trades only change when a position
    # actually closes -- not on every widget interaction. Short TTL cache
    # collapses that repeated recomputation while still picking up a
    # newly-closed trade within 30 seconds.
    metrics = _cached_performance_metrics(user_id)

    if metrics["trades_closed"] == 0:
        st.info(_t("perf.info_none"))
        return

    # FIX 2026-09-09b: every headline stat gets its own bordered card
    # (st.container(border=True)) instead of a bare st.metric() -- see
    # the matching change in render_open_positions() above for why.
    row1 = st.columns(4)
    with row1[0].container(border=True):
        st.metric(_t("perf.metric_total_realized"), f"${metrics['total_pnl']:,.2f}")
    with row1[1].container(border=True):
        st.metric(_t("perf.metric_win_rate_all"), f"{metrics['win_rate']:.1f}%")
    with row1[2].container(border=True):
        st.metric(_t("perf.metric_trades_closed"), f"{metrics['trades_closed']}")
    pf = metrics["profit_factor"]
    with row1[3].container(border=True):
        st.metric(_t("perf.metric_profit_factor"), "N/A" if pf is None else f"{pf:.2f}")

    row2 = st.columns(4)
    with row2[0].container(border=True):
        st.metric(_t("perf.metric_avg_win"), f"${metrics['average_win']:,.2f}")
    with row2[1].container(border=True):
        st.metric(_t("perf.metric_avg_loss"), f"${metrics['average_loss']:,.2f}")
    with row2[2].container(border=True):
        st.metric(_t("perf.metric_largest_win"), f"${metrics['largest_win']:,.2f}")
    with row2[3].container(border=True):
        st.metric(_t("perf.metric_max_drawdown"), f"${metrics['max_drawdown']:,.2f}")

    if metrics["priced_trades_closed"] < metrics["trades_closed"]:
        st.caption(
            _t("perf.caption_priced_note", priced=metrics['priced_trades_closed'], total=metrics['trades_closed'])
        )

    # FIX 2026-09-09b: the time-range filter and period-summary cards used
    # to live INSIDE the collapsed "Closed trades" expander, which meant a
    # returning user had to click to expand before they'd ever see the
    # filter existed at all -- eToro's own time-range dropdown sits in
    # plain view above its history table, never behind a click. Moved out
    # here so it's visible immediately; only the raw trade-by-trade table
    # itself stays inside the expander below.
    _RANGE_DAYS = {"7D": 7, "30D": 30, "3M": 90, "6M": 180, "1Y": 365}
    range_options = list(_RANGE_DAYS.keys()) + [_t("perf.range_all")]
    selected_range = st.selectbox(
        _t("perf.time_range_label"), options=range_options,
        index=len(range_options) - 1, key="perf_time_range",
    )

    def _parse_exit_time(raw):
        try:
            return datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            return None

    all_closed = metrics["closed_trades"]
    if selected_range in _RANGE_DAYS:
        cutoff = datetime.now() - timedelta(days=_RANGE_DAYS[selected_range])
        period_trades = [
            t for t in all_closed
            if (_parse_exit_time(t.get("exit_time")) or datetime.min) >= cutoff
        ]
    else:
        period_trades = all_closed

    period_priced = [t for t in period_trades if t["priced"]]
    period_pnl = sum(t["pnl"] for t in period_priced)
    period_wins = [t for t in period_trades if t["pnl_percent"] > 0]
    period_win_rate = (len(period_wins) / len(period_trades) * 100) if period_trades else 0.0

    period_cols = st.columns(3)
    with period_cols[0].container(border=True):
        st.metric(
            _t("perf.metric_period_pnl"), f"${period_pnl:,.2f}", delta=f"{period_pnl:,.2f}",
        )
    with period_cols[1].container(border=True):
        st.metric(_t("perf.metric_period_trades"), f"{len(period_trades)}")
    with period_cols[2].container(border=True):
        st.metric(_t("perf.metric_period_winrate"), f"{period_win_rate:.1f}%")

    with st.expander(_t("perf.expander_closed_trades")):
        if not period_trades:
            st.info(_t("perf.info_none"))
        else:
            # FIX 2026-09-09: exit_strategy_raw (added in
            # saas_performance_engine.py alongside the existing
            # English-only "exit_strategy" label) lets this badge be
            # translated instead of always showing English -- and,
            # since EXIT_PROTECTION alone doesn't say whether a trade
            # closed on its stop-loss or its take-profit, the badge for
            # that one code is derived from the trade's own P&L sign,
            # same distinction eToro's own SL/TP badges make.
            def _exit_badge(raw_strategy, pnl):
                if raw_strategy == "PARTIAL_PROFIT":
                    return _t("perf.badge_partial_profit")
                if raw_strategy == "MAX_HOLD_TIME_EXIT":
                    return _t("perf.badge_time_exit")
                if raw_strategy == "EXIT_PROTECTION":
                    return _t("perf.badge_take_profit") if pnl >= 0 else _t("perf.badge_stop_loss")
                return _t("perf.badge_unknown")

            trades_df = pd.DataFrame(period_trades)
            trades_df["_badge"] = trades_df.apply(
                lambda row: _exit_badge(row.get("exit_strategy_raw"), row.get("pnl") or 0), axis=1
            )
            trades_df = trades_df.rename(columns={
                "ticker": _t("col.ticker"), "broker": _t("col.broker"), "asset_class": _t("col.asset_class"),
                "entry_price": _t("col.entry_price"), "exit_price": _t("col.exit_price"),
                "quantity": _t("col.quantity"), "pnl": _t("col.pnl_usd"), "pnl_percent": _t("col.pnl_pct"),
                "_badge": _t("col.exit_strategy"), "exit_time": _t("col.closed_at"),
            })
            # Leveraged (non-priced) rows keep a real, meaningful P&L (%) but
            # their $ figure isn't trustworthy -- blank it out here rather
            # than show a number the summary metrics above deliberately exclude.
            trades_df.loc[~trades_df["priced"], _t("col.pnl_usd")] = None
            column_order = [_t("col.closed_at"), _t("col.broker"), _t("col.ticker"), _t("col.asset_class"), _t("col.entry_price"),
                             _t("col.exit_price"), _t("col.quantity"), _t("col.pnl_usd"), _t("col.pnl_pct"), _t("col.exit_strategy")]
            trades_df = trades_df[[c for c in column_order if c in trades_df.columns]]
            trades_df = trades_df.sort_values(_t("col.closed_at"), ascending=False)

            # FIX 2026-09-09: color P&L green/red, same rationale as the
            # matching change in render_open_positions() above.
            def _color_pnl(value):
                try:
                    v = float(value)
                except (TypeError, ValueError):
                    return ""
                if v > 0:
                    return "color: #2ecc71; font-weight: 600;"
                if v < 0:
                    return "color: #ff4d4f; font-weight: 600;"
                return ""

            pnl_cols = [c for c in (_t("col.pnl_usd"), _t("col.pnl_pct")) if c in trades_df.columns]

            # FIX 2026-09-09b: give the exit-reason badge column a filled
            # background color per category (eToro's own SL/TP badges are
            # colored pills, not plain text). st.dataframe's Styler support
            # only reaches color/background-color -- no border-radius/padding,
            # since the grid is canvas-rendered, not real DOM -- so this is
            # a tinted cell rather than a true rounded chip, but it gets the
            # same at-a-glance color-coded read eToro's badges give.
            _BADGE_STYLES = {
                _t("perf.badge_take_profit"): "background-color: #163a2a; color: #2ecc71; font-weight: 600;",
                _t("perf.badge_partial_profit"): "background-color: #163a2a; color: #2ecc71; font-weight: 600;",
                _t("perf.badge_stop_loss"): "background-color: #3a1a1a; color: #ff4d4f; font-weight: 600;",
                _t("perf.badge_time_exit"): "background-color: #2a2a1a; color: #d9b84a; font-weight: 600;",
                _t("perf.badge_unknown"): "background-color: #262626; color: #9a9a9a; font-weight: 600;",
            }

            def _badge_style(value):
                return _BADGE_STYLES.get(value, "")

            badge_col = _t("col.exit_strategy")
            styled_trades = trades_df.style
            if pnl_cols:
                styled_trades = styled_trades.map(_color_pnl, subset=pnl_cols)
            if badge_col in trades_df.columns:
                styled_trades = styled_trades.map(_badge_style, subset=[badge_col])
            st.dataframe(styled_trades, use_container_width=True, hide_index=True)

    asset_class_breakdown = _cached_pnl_by_asset_class(user_id)
    strategy_breakdown = _cached_pnl_by_exit_strategy(user_id)
    monthly_breakdown = _cached_monthly_pnl(user_id)

    breakdown_col1, breakdown_col2 = st.columns(2)
    with breakdown_col1:
        st.markdown(_t("perf.header_pnl_by_asset"))
        if asset_class_breakdown:
            df = pd.DataFrame(asset_class_breakdown).rename(columns={
                "asset_class": _t("col.asset_class"), "trades_closed": _t("col.trades"),
                "win_rate": _t("col.win_rate_pct"), "total_pnl": _t("col.total_pnl_usd"),
            })[[_t("col.asset_class"), _t("col.trades"), _t("col.win_rate_pct"), _t("col.total_pnl_usd")]]
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.caption(_t("perf.caption_no_priced"))
    with breakdown_col2:
        st.markdown(_t("perf.header_pnl_by_exit"))
        if strategy_breakdown:
            df = pd.DataFrame(strategy_breakdown).rename(columns={
                "exit_strategy": _t("col.exit_strategy"), "trades_closed": _t("col.trades"),
                "win_rate": _t("col.win_rate_pct"), "total_pnl": _t("col.total_pnl_usd"),
            })[[_t("col.exit_strategy"), _t("col.trades"), _t("col.win_rate_pct"), _t("col.total_pnl_usd")]]
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.caption(_t("perf.caption_no_priced"))

    if monthly_breakdown:
        st.markdown(_t("perf.header_monthly_pnl"))
        df = pd.DataFrame(monthly_breakdown).rename(columns={
            "month": _t("col.month"), "trades_closed": _t("col.trades"),
            "win_rate": _t("col.win_rate_pct"), "total_pnl": _t("col.total_pnl_usd"),
        })[[_t("col.month"), _t("col.trades"), _t("col.win_rate_pct"), _t("col.total_pnl_usd")]]
        st.dataframe(df, use_container_width=True, hide_index=True)


# ============================================================
# ADMIN PANEL (only rendered for emails in ADMIN_EMAILS)
# ============================================================
def render_admin_panel():
    st.subheader("🛡️ Platform Kill Switch")
    st.caption(
        "Blocks new BUY evaluation for EVERY user on the platform at "
        "once, for a systemic issue (bad model, broken broker "
        "integration, a bug in the decision loop itself), not a "
        "response to one user's problem. Each user's stop-loss/take-"
        "profit/max-hold-time exit protection keeps running on their "
        "existing positions even while this is active. A platform-"
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
        "brokers, deliberately not a blended dollar total. Different "
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
        if exposure["total_open_positions"] else "N/A",
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
    df["connected_brokers"] = df["connected_brokers"].apply(lambda b: ", ".join(b) if b else "None")
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
    st.title(_t("billing.title"))
    st.caption(_t("billing.signed_in_as", email=user['email']))

    if st.query_params.get("billing") == "success":
        st.info(_t("billing.info_payment_received"))

    billing = tenant.get_billing_info(user["user_id"]) or {}
    status = billing.get("billing_status", "none")

    if status in ("past_due", "canceled"):
        reason = _t("billing.reason_past_due") if status == "past_due" else _t("billing.reason_canceled")
        st.error(_t("billing.err_needs_attention", status=reason))
        if billing.get("stripe_customer_id"):
            try:
                portal_url = billing_engine.create_billing_portal_session(
                    billing["stripe_customer_id"], APP_URL
                )
                st.link_button(_t("billing.manage_button"), portal_url, use_container_width=True)
            except Exception:
                # Logged (not just shown to the user) so failures are
                # actually diagnosable via journalctl -- a bare "except
                # Exception: st.warning(...)" with no logging call was the
                # root cause of a hard-to-debug Checkout failure earlier.
                print("[billing] create_billing_portal_session failed:")
                traceback.print_exc()
                st.warning(_t("billing.warn_portal_failed"))
    else:
        st.subheader(_t("billing.trial_header"))
        st.write(_t("billing.trial_body"))
        try:
            checkout_url = billing_engine.create_checkout_session(
                user["user_id"], user["email"], APP_URL
            )
            st.link_button(_t("billing.start_trial_button"), checkout_url, use_container_width=True)
        except Exception:
            print("[billing] create_checkout_session failed:")
            traceback.print_exc()
            st.error(_t("billing.err_checkout_failed"))

    st.divider()
    st.caption(_t("billing.footer_links"))
    if st.button(_t("billing.logout_button"), key="billing_gate_logout"):
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

    # FIX 2026-09-10: used to also show st.caption(_t("dash.signed_in_as",
    # email=user['email'])) right here -- the user's real email in
    # plaintext, first thing visible on page load/every screenshot.
    # Removed; the email is still reachable (Account Settings -> Profile,
    # and the change-email flow) for anyone who actually needs to see it,
    # just not broadcast on every load of the dashboard itself.
    header_col, billing_col, logout_col = st.columns([3, 1, 1])
    with header_col:
        st.title(_t("dash.title"))
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
            if st.button(_t("dash.manage_billing_button"), use_container_width=True):
                try:
                    portal_url = billing_engine.create_billing_portal_session(
                        billing["stripe_customer_id"], APP_URL
                    )
                    st.markdown(
                        f'<meta http-equiv="refresh" content="0; url={portal_url}">',
                        unsafe_allow_html=True,
                    )
                    st.caption(_t("dash.opening_portal_caption"))
                except Exception:
                    print("[billing] create_billing_portal_session failed:")
                    traceback.print_exc()
                    st.error(_t("dash.err_portal_failed"))
    with logout_col:
        st.write("")
        if st.button(_t("dash.logout_button"), use_container_width=True):
            _log_out()
            st.rerun()

    if not user["email_verified"]:
        banner_col, button_col = st.columns([4, 1])
        with banner_col:
            st.warning(_t("dash.warn_verify_email"))
        with button_col:
            st.write("")
            if st.button(_t("dash.resend_email_button"), key="resend_verification"):
                try:
                    verify_token = tenant.create_email_verification_token(user["user_id"])
                    verify_url = f"{APP_URL}/?verify_token={verify_token}"
                    email_engine.send_verification_email(user["email"], verify_url)
                    st.success(_t("dash.success_email_sent"))
                except Exception:
                    st.error(_t("dash.err_email_send_failed"))

    # FIX 2026-09-04 (#264): rendered once here, before the admin/non-admin
    # split, so the pause control is the first thing visible on the
    # dashboard for every user regardless of tab -- see its docstring.
    render_pause_control(user["user_id"])

    # FIX 2026-09-10: reordered -- Account Settings used to be dead last
    # (below Performance), and AI Trading / Trading Settings sat behind
    # Open Positions and Performance too. eToro (and most brokerage apps)
    # put account/profile controls near the top and put the actual
    # trading controls before the reporting views, not after. Account
    # Settings now comes first (right after the pause control), then the
    # actual trading setup (Live Trading, Broker Connections, Trading
    # Settings, AI Trading), with the two read-only reporting views (Open
    # Positions, Performance) last. Same order in both the admin and
    # non-admin branches -- this is a pure reorder, no section's own
    # content changed.
    if _is_admin(user["email"]):
        my_tab, admin_tab = st.tabs([_t("dash.my_dashboard_tab"), _t("dash.admin_tab_label")])
        with my_tab:
            render_account_settings(user)
            st.divider()
            render_live_trading_switch(user["user_id"])
            st.divider()
            render_broker_connections(user["user_id"])
            st.divider()
            render_settings(user["user_id"])
            st.divider()
            render_trading_run(user["user_id"])
            st.divider()
            render_open_positions(user["user_id"])
            st.divider()
            render_performance_view(user["user_id"])
        with admin_tab:
            render_admin_panel()
    else:
        render_account_settings(user)
        st.divider()
        render_live_trading_switch(user["user_id"])
        st.divider()
        render_broker_connections(user["user_id"])
        st.divider()
        render_settings(user["user_id"])
        st.divider()
        render_trading_run(user["user_id"])
        st.divider()
        render_open_positions(user["user_id"])
        st.divider()
        render_performance_view(user["user_id"])

    st.divider()
    # FIX 2026-09-10: the Terms/Privacy links used to be a plain
    # left-aligned st.caption(), sitting right up against the divider
    # above -- easy to miss, and visually lopsided on the wide layout.
    # Scoped container + CSS (same st-key-<key> pattern used for
    # auth-body/reset-body/legal-body elsewhere in this file) centers it
    # and gives it real breathing room above, without touching any other
    # page's footer.
    st.markdown(
        """
        <style>
        .st-key-dash-footer { text-align: center; margin-top: 2rem; opacity: 0.75; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="dash-footer"):
        st.caption(_t("dash.footer_links"))


# ============================================================
# PASSWORD RESET / EMAIL VERIFICATION LANDING SCREENS
# Reached via the links inside the emails sent above -- ordertradeai.com/
# ?reset_token=... or ?verify_token=.... Checked BEFORE the normal
# logged-in/logged-out branch below so these work whether or not the
# person clicking the link happens to already be signed in on this
# browser.
# ============================================================
def render_password_reset_screen(token):
    # FIX 2026-09-09: same reasoning as render_auth_screen()'s
    # "auth-body" wrap -- re-narrowing this single-column password
    # form now that the page-wide layout is "wide".
    st.markdown(
        """
        <style>
        .st-key-reset-body { max-width: 480px; margin: 0 auto; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="reset-body"):
        st.title(_t("reset.title"))
        st.subheader(_t("reset.header"))

        user_id = tenant.verify_password_reset_token(token)
        if user_id is None:
            st.error(_t("reset.err_invalid_link"))
            if st.button(_t("reset.back_to_login_button")):
                st.query_params.clear()
                st.rerun()
            return

        with st.form("password_reset_form"):
            new_password = st.text_input(_t("reset.new_password_label"), type="password", key="reset_new_password")
            confirm = st.text_input(_t("reset.confirm_password_label"), type="password", key="reset_confirm_password")
            submitted = st.form_submit_button(_t("reset.submit_button"), use_container_width=True)

        if submitted:
            if len(new_password) < 8:
                st.error(_t("reset.err_short_password"))
            elif new_password != confirm:
                st.error(_t("reset.err_mismatch"))
            else:
                ok = tenant.reset_password(token, new_password)
                if ok:
                    st.success(_t("reset.success"))
                    if st.button(_t("reset.go_to_login_button")):
                        st.query_params.clear()
                        st.rerun()
                else:
                    st.error(_t("reset.err_used_or_expired"))


def render_email_verification_screen(token):
    st.title(_t("verify.title"))
    ok = tenant.verify_email_token(token)
    if ok:
        st.success(_t("verify.success"))
    else:
        st.error(_t("verify.err_invalid"))
    if st.button(_t("verify.continue_button")):
        st.query_params.clear()
        st.rerun()


def render_email_change_screen(token):
    st.title(_t("emailchange.title"))
    new_email = tenant.confirm_email_change(token)
    if new_email:
        st.success(_t("emailchange.success", email=new_email))
        # Keep an already-active session in sync so the header/caption
        # don't keep showing the old address for the rest of this visit --
        # doesn't touch anyone else's session, just this browser's.
        if st.session_state.saas_user_id:
            st.session_state.saas_user_email = new_email
    else:
        st.error(_t("emailchange.err_invalid"))
    if st.button(_t("emailchange.continue_button")):
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
_LEGAL_LAST_UPDATED = "September 9, 2026"

_TERMS_MD = f"""
*Last updated: {_LEGAL_LAST_UPDATED}*

### 1. Acceptance of terms

By creating an account or using OrderTrade AI ("the Service"), you
agree to these Terms of Service ("Terms"). If you don't agree, don't
use the Service.

### 2. What the Service does

OrderTrade AI generates AI-assisted trade signals and, at your
explicit request, places orders through broker accounts that **you**
connect using your own API credentials. The Service never holds,
custodies, or has independent access to your funds. Every order is
placed directly against your own connected broker account, and you
must separately confirm ("Execute") before any live order is sent.

**By default, every account is paper/demo trading only.** No order the
Service places can reach a real broker account unless you take the
separate, explicit steps described in Section 3 below to enable live
trading for your account. Connecting a broker in demo/paper mode never
risks real money, regardless of anything else in these Terms.

### 3. Live trading and real-money risk

The Service supports, as an opt-in feature, placing real orders
against a real-money broker account you connect. This section applies
only if and when you enable it.

**How it's enabled.** Live trading is off by default for every
account. To turn it on, you must go through a dedicated confirmation
flow in your account settings: reading a risk disclosure, checking
each of several individual acknowledgment boxes (including that
results aren't guaranteed, that you're solely responsible for your own
account, and that a software bug or broker outage could cause a real
loss), and typing a confirmation phrase. Enabling live trading for
your account does not, by itself, connect any broker in live mode.
You separately choose "live" instead of "demo" when connecting each
individual broker. Every live/demo change is logged with a timestamp
and reason, visible to you in your account settings.

**The risk is real and it is yours.** Once live trading is enabled and
a broker is connected in live mode, the Service will place real orders
using real money in that account, without asking for your confirmation
on each individual trade. You can lose some or all of the money in any
connected live broker account. This can happen even if the Service,
your broker, and your internet connection all work exactly as intended,
since trading itself carries risk, independent of any bug. It can also
happen because of a software defect in the Service, an error or outage
at your broker, a market data delay, a connectivity failure between
the Service and your broker, or any other technical failure, expected
or not. **The Service, its operator, and anyone associated with it are
not liable for trading losses in a live account, including losses
caused in whole or in part by a bug or defect in the Service.** See
Section 10 (Limitation of liability) for how this interacts with our
overall liability cap. This risk allocation, not any promise of
correctness or uptime, is what makes it acceptable for you to use this
feature at all: you are compensated for taking on this risk by paying
a flat subscription fee regardless of trading outcome, not by any
share of profits, and we do not price, underwrite, or otherwise treat
your live trading risk as something we've agreed to absorb.

**You can turn it off, but not undo what already happened.** You may
revert to demo trading at any time from your account settings. Doing
so stops the Service from placing new orders against your real
account, but it does not close any position already open at your
broker. You're responsible for managing those directly with your
broker afterward. It also does not reverse any trade already placed.

**Only enable this with money you can afford to lose,** in an amount
you've deliberately decided to risk, using a broker account you
actively monitor. If you are not willing to accept the possibility of
losing everything in a connected live account, do not enable live
trading.

### 4. Not investment advice, no guaranteed results

Nothing generated or displayed by the Service, including signals, scores,
grades, backtests, or any other output, is investment, financial,
tax, or legal advice, and none of it is a recommendation to buy or
sell any security, currency, commodity, or crypto asset. Trading and
investing involve substantial risk of loss, including total loss of
principal. Past performance (including any backtested or paper-traded
results shown in the Service) is not indicative of future results.
You are solely responsible for every trading decision made through
your account, whether initiated by you or executed by the Service at
your direction.

### 5. Eligibility and your account

You must be at least 18 years old (or the age of majority in your
jurisdiction) to use the Service. You're responsible for keeping your
password confidential and for all activity under your account. Tell
us promptly if you believe your account has been compromised.

### 6. Your broker credentials

You provide your own broker/exchange API keys. We encrypt them at
rest and only ever decrypt them to place orders you've directed
through the Service. You're responsible for complying with your
broker's own terms of service, and for any fees, restrictions, or
consequences your broker applies to API-driven trading on your
account.

### 7. Subscription and billing

After a 14-day free trial, continued use of the Service requires a
paid subscription, billed monthly in advance through our payment
processor (Stripe). Your subscription renews automatically each month
until you cancel. You can cancel at any time from your account
billing settings; cancellation takes effect at the end of your
current billing period, and we don't provide refunds for partial
periods already paid for. We may change our pricing with reasonable
advance notice; continuing to use the Service after a price change
takes effect means you accept the new price.

### 8. Acceptable use

You agree not to: use the Service for anything illegal; attempt to
reverse-engineer, scrape, or resell access to the Service; interfere
with or overload the Service's infrastructure; or use the Service to
violate any broker's or exchange's own terms of service.

### 9. Disclaimers

The Service is provided "as is" and "as available," without warranty
of any kind, express or implied, including warranties of
merchantability, fitness for a particular purpose, or
non-infringement. We don't warrant that the Service will be
uninterrupted, error-free, or that any signal, price, or position
data shown will always be accurate or current. Broker/exchange
outages, market data delays, and third-party API failures are outside
our control.

### 10. Limitation of liability

To the maximum extent permitted by law, OrderTrade AI and its
operator will not be liable for any indirect, incidental, special,
consequential, or punitive damages, or for any trading losses, lost
profits, or lost data, arising from your use of the Service. Our
total liability for any claim relating to the Service is limited to
the amount you paid us in the 12 months before the claim arose.

### 11. Termination

We may suspend or terminate your access if you violate these Terms or
if we reasonably believe your use of the Service poses a risk to the
platform or other users. You may stop using the Service and cancel
your subscription at any time.

### 12. Changes to these terms

We may update these Terms from time to time. We'll update the "Last
updated" date above when we do; continued use of the Service after a
change takes effect means you accept the updated Terms.

### 13. Governing law

These Terms are governed by the laws of Malta, without regard to its
conflict-of-laws principles.

### 14. Contact

Questions about these Terms? Contact us at
support@ordertradeai.com.
"""

_PRIVACY_MD = f"""
*Last updated: {_LEGAL_LAST_UPDATED}*

### 1. What we collect

- **Account info:** your email address and a securely hashed (bcrypt)
  password. We never store your password in plain text.
- **Broker credentials:** the API key/secret you provide for each
  broker you connect, encrypted at rest (Fernet symmetric encryption)
  and decrypted only at the moment we place a trade you've directed.
- **Trading activity:** positions, orders, and settings associated
  with your account, so the Service can function and so you can see
  your own history.
- **Billing info:** handled directly by our payment processor,
  Stripe. We never see or store your full card number. We keep only
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

- **Your connected brokers** (e.g. Alpaca, Binance, eToro): to place
  the orders you direct.
- **Stripe**: to process subscription billing.
- **Resend**: to deliver transactional email (password reset,
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

The Service itself (this dashboard, at /app) uses only a session
cookie needed to keep you logged in. No advertising or tracking
cookies here.

Our marketing site at ordertradeai.com/ uses Google Analytics (GA4) to
understand visitor traffic, which sets a cookie and shares data with
Google. This only runs after you accept it in the cookie banner shown
on that page. Declining, or not choosing, means it never loads. You
can change your choice at any time using the "Cookie preferences" link
in that page's footer. See Google's own privacy policy for how Google
processes this data: https://policies.google.com/privacy

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
    if st.button(_t("legal.back_button_top"), key="legal_back_top"):
        st.query_params.clear()
        st.rerun()
    st.title(_t("legal.title"))
    st.header(title)
    # Readability pass 2026-09-04: full-container-width paragraphs made
    # every line stretch the whole page, so line length varied a lot
    # (short line next to a long one) and just looked ragged. Considered
    # justifying the text to even out the right edge, but true justify
    # on the web (no hyphenation) creates uneven word-spacing gaps
    # instead -- worse for reading, not better. Left-aligned text stays
    # the standard; what actually helps here is capping the line length
    # and loosening the line-height, both scoped to just this container
    # (via Streamlit's st.container(key=...), which emits a stable
    # `st-key-<key>` CSS class) so it doesn't affect any other page.
    st.markdown(
        """
        <style>
        .st-key-legal-body { max-width: 760px; }
        .st-key-legal-body [data-testid="stMarkdownContainer"] p,
        .st-key-legal-body [data-testid="stMarkdownContainer"] li {
            line-height: 1.75;
            text-align: left;
            margin-bottom: 1em;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="legal-body"):
        st.markdown(body_markdown)
    st.divider()
    if st.button(_t("legal.back_button_bottom"), key="legal_back_bottom"):
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
