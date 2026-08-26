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
confirmation checkbox. Still BUY-side only, still US_STOCKS/CRYPTO only
(no eToro execution yet), still fully manual -- no scheduler, nothing
runs unless a signed-in user clicks the buttons themselves. See
saas_decision_engine.py's module docstring for the full scope and
the gaps that are still open (no automated selling, no portfolio-level
exposure cap) before this should be trusted beyond supervised testing.
"""

import pandas as pd
import streamlit as st

from engines import tenant_engine as tenant
from engines import saas_broker_factory
from engines import saas_decision_engine

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

    with signup_tab:
        with st.form("signup_form"):
            new_email = st.text_input("Email", key="signup_email")
            new_password = st.text_input("Password", type="password", key="signup_password")
            confirm_password = st.text_input(
                "Confirm password", type="password", key="signup_confirm"
            )
            signup_submitted = st.form_submit_button("Create Account", use_container_width=True)

        if signup_submitted:
            if not new_email or not new_password:
                st.error("Enter both email and password.")
            elif len(new_password) < 8:
                st.error("Password must be at least 8 characters.")
            elif new_password != confirm_password:
                st.error("Passwords don't match.")
            else:
                user_id = tenant.create_user(new_email, new_password)
                if user_id is None:
                    st.error("An account with this email already exists. Try logging in instead.")
                else:
                    st.success("Account created.")
                    _log_in(user_id, new_email.strip().lower())
                    st.rerun()


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


def render_trading_run(user_id):
    st.subheader("AI Trading")
    st.caption(
        "Checks your existing positions for stop-loss/take-profit/max-"
        "hold-time exits, then runs the AI signal engine across your "
        "enabled asset classes (US Stocks via Alpaca, Crypto via Binance "
        "-- Forex/Commodities execution isn't built yet even if enabled "
        "above), sizes any approved BUY against your real connected-"
        "broker balance, and shows you exactly what it would do. Nothing "
        "is ever bought or sold without you clicking Execute separately "
        "below. Exit protection covers stop-loss/take-profit/a hard "
        "max-hold-time only -- no partial profit-taking yet -- and, like "
        "everything here, only runs when you click these buttons; there "
        "is no background scheduler yet, so positions get no protection "
        "between visits."
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


def render_dashboard():
    user = tenant.get_user(st.session_state.saas_user_id)
    if user is None:
        # Account no longer exists / DB reset -- fail safe back to login.
        _log_out()
        st.rerun()
        return

    header_col, logout_col = st.columns([4, 1])
    with header_col:
        st.title("📈 OrderTrade AI")
        st.caption(f"Signed in as {user['email']}")
    with logout_col:
        st.write("")
        if st.button("Log Out", use_container_width=True):
            _log_out()
            st.rerun()

    render_broker_connections(user["user_id"])
    st.divider()
    render_settings(user["user_id"])
    st.divider()
    render_trading_run(user["user_id"])


# ============================================================
# ENTRY POINT
# ============================================================
if st.session_state.saas_user_id is None:
    render_auth_screen()
else:
    render_dashboard()
