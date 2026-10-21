"""SpreadThrift - Streamlit Multi-Tenant Web App for Vinted Inventory, Sales & Analytics."""

import os
import re
import streamlit as st

from src.config import settings
from src.auth import is_oauth_configured, get_authorization_url, exchange_code_for_credentials
from src.telemetry import log_session, log_error, log_event, log_feedback

# Page Configuration
LOGO_PATH = "assets/invintory.png"

st.set_page_config(
    page_title="SpreadThrift",
    page_icon=LOGO_PATH if os.path.exists(LOGO_PATH) else "👕",
    layout="wide",
    initial_sidebar_state="expanded",
)

if os.path.exists(LOGO_PATH):
    st.logo(LOGO_PATH, size="large")

# -----------------------------------------------------------------------------
# Session State Initialization
# -----------------------------------------------------------------------------
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False
if "user_info" not in st.session_state:
    st.session_state.user_info = {}
if "user_credentials" not in st.session_state:
    st.session_state.user_credentials = None
if "spreadsheet_id" not in st.session_state:
    st.session_state.spreadsheet_id = settings.google_sheet_id
if "selected_lot" not in st.session_state:
    st.session_state.selected_lot = "LOT-001"
if "parsed_results" not in st.session_state:
    st.session_state.parsed_results = None

# -----------------------------------------------------------------------------
# Legal & Compliance Dialogs
# -----------------------------------------------------------------------------
@st.dialog("🛡️ SpreadThrift Privacy Policy", width="large")
def show_privacy_dialog():
    privacy_file = settings.base_dir / "PRIVACY_POLICY.md"
    if privacy_file.exists():
        st.markdown(privacy_file.read_text(encoding="utf-8"))
    else:
        st.info("Privacy Policy document is available at PRIVACY_POLICY.md")


@st.dialog("📜 SpreadThrift Terms of Service", width="large")
def show_terms_dialog():
    terms_file = settings.base_dir / "TERMS_OF_SERVICE.md"
    if terms_file.exists():
        st.markdown(terms_file.read_text(encoding="utf-8"))
    else:
        st.info("Terms of Service document is available at TERMS_OF_SERVICE.md")


@st.dialog("📖 SpreadThrift User & Feature Guide", width="large")
def show_user_guide_dialog():
    guide_file = settings.base_dir / "USER_GUIDE.md"
    if guide_file.exists():
        st.markdown(guide_file.read_text(encoding="utf-8"))
    else:
        st.info("User Guide document is available at USER_GUIDE.md")

# -----------------------------------------------------------------------------
# OAuth Callback Handler
# -----------------------------------------------------------------------------
query_params = st.query_params
if "code" in query_params and not st.session_state.authenticated:
    code = query_params["code"]
    state = query_params.get("state")
    try:
        creds, user_info = exchange_code_for_credentials(code, state=state)
        u_email = (user_info.get("email") or "").lower()
        st.session_state.user_credentials = creds
        st.session_state.user_info = user_info
        st.session_state.authenticated = True
        st.session_state.is_admin = u_email in settings.admin_email_list
        log_session(u_email, user_info.get("name", "Google User"), auth_provider="google")
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        log_error(None, "app.py:oauth", e)
        st.error(f"Authentication failed: {e}")

# -----------------------------------------------------------------------------
# Authentication Screen (if not logged in)
# -----------------------------------------------------------------------------
if not st.session_state.authenticated:
    header_col1, header_col2 = st.columns([0.12, 0.88], vertical_alignment="center")
    with header_col1:
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=80, alt="SpreadThrift Logo")
    with header_col2:
        st.title("SpreadThrift")
        st.markdown("**From inbox alerts to bottom-line gains.**")

    st.markdown("Automate your Vinted inventory tracking, net profits, and performance metrics directly with Google Sheets.")

    col1, col2 = st.columns([1.2, 1])

    with col1:
        st.markdown("### Welcome! Choose How to Sign In")
        
        # 1. Sign in with Google (OAuth)
        if is_oauth_configured():
            try:
                auth_url, _ = get_authorization_url()
                st.link_button("🔑 Sign in with Google", auth_url, type="primary", width="stretch")
                st.caption("Sign in with your Google account to connect directly to your personal Google Sheets.")
            except Exception as e:
                st.warning(f"OAuth configuration notice: {e}")
        else:
            st.info("ℹ️ Google OAuth can be enabled by setting `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env` or Streamlit Secrets.")

        st.divider()

        # 2. Local / Development Direct Access
        st.markdown("#### Local Mode (Personal Workspace)")
        sa_exists = settings.google_service_account_file.exists()
        
        if sa_exists:
            if st.button("🚀 Continue with Local Credentials", type="secondary", width="stretch"):
                st.session_state.authenticated = True
                st.session_state.is_admin = True
                st.session_state.user_info = {
                    "email": "local-admin@vinted-tracker",
                    "name": "Local Admin",
                }
                log_session("local-admin@vinted-tracker", "Local Admin", auth_provider="service_account")
                st.rerun()
            st.caption(f"Uses local Service Account: `{settings.google_service_account_file.name}`")
        else:
            st.warning("No local `credentials.json` found. Please sign in with Google or place credentials.json in your project directory.")

        st.divider()

        # 3. Direct Administrator Passcode Login
        with st.expander("👑 Administrator Access"):
            st.caption("Sign in directly as the SpreadThrift administrator to monitor user metrics and system error alerts:")
            admin_input_pass = st.text_input("Master Passcode", type="password", key="admin_direct_login_pass")
            admin_input_email = st.text_input("Admin Email", value="admin@spreadthrift.com", key="admin_direct_login_email")
            if st.button("Unlock Admin Portal", type="primary", width="stretch", key="btn_admin_direct_login"):
                if admin_input_pass == settings.admin_password:
                    st.session_state.authenticated = True
                    st.session_state.is_admin = True
                    st.session_state.user_info = {
                        "email": admin_input_email,
                        "name": "SpreadThrift Administrator",
                    }
                    log_session(admin_input_email, "SpreadThrift Administrator", auth_provider="master_passcode")
                    st.rerun()
                else:
                    st.error("Invalid master passcode.")

    with col2:
        st.markdown("""
        ### What This App Does
        * **📈 Interactive Plotly Metrics**: Track gross revenue, net profit, margin trends, brand rankings, and price-to-profit distributions.
        * **📦 1-Click HTML Import**: Upload your saved Vinted profile or orders HTML to update active items and sales.
        * **⚡ Zero Duplicate Guarantee**: Intelligent set-based reconciliation avoids duplicating previously imported items.
        * **📬 Real-Time Email Sync**: Pull sales and offers directly from your Gmail inbox via IMAP.
        * **🔒 100% Private**: Your data connects straight to your personal Google Drive with zero middleman database.
        """)

    st.divider()
    leg_col1, leg_col2, leg_col3, leg_col4 = st.columns([2.2, 1, 1, 1], vertical_alignment="center")
    with leg_col1:
        st.caption(
            "⚖️ **Third-Party Trademark & Legal Disclaimer**: Independent tool. Not affiliated with, authorized, or endorsed by "
            "Vinted, Google LLC, or Meta Platforms, Inc. All trademarks belong to their respective owners."
        )
    with leg_col2:
        if st.button("📖 User Guide", width="stretch", key="login_guide_btn", icon=":material/menu_book:"):
            show_user_guide_dialog()
    with leg_col3:
        if st.button("🛡️ Privacy Policy", width="stretch", key="login_privacy_btn"):
            show_privacy_dialog()
    with leg_col4:
        if st.button("📜 Terms of Service", width="stretch", key="login_terms_btn"):
            show_terms_dialog()

    st.stop()

# -----------------------------------------------------------------------------
# Sidebar: Settings & User Profile
# -----------------------------------------------------------------------------
with st.sidebar:
    st.sidebar.caption("From inbox alerts to bottom-line gains.")
    if st.session_state.get("is_admin", False):
        st.sidebar.caption("👑 **Administrator Session**")
    st.sidebar.divider()
    u_email = st.session_state.user_info.get("email", "Local User")
    u_name = st.session_state.user_info.get("name", "Vinted Seller")
    u_pic = st.session_state.user_info.get("picture")

    if u_pic:
        st.sidebar.image(u_pic, width=54)
    st.sidebar.markdown(f"**{u_name}**")
    st.sidebar.caption(u_email)

    if st.sidebar.button("Log Out", icon=":material/logout:", width="stretch"):
        st.session_state.authenticated = False
        st.session_state.user_credentials = None
        st.session_state.user_info = {}
        st.cache_data.clear()
        st.rerun()

    st.sidebar.divider()
    st.sidebar.markdown("### 📊 Google Sheet")

    # Template Clone Button
    master_template_id = "1osw6Osf37GMeCEf3dXA6_TqaUPCCfsUg4VS5HIKLGWg"
    template_copy_url = f"https://docs.google.com/spreadsheets/d/{master_template_id}/copy"
    st.sidebar.link_button("📋 Copy Sheet Template", template_copy_url, width="stretch")

    sheet_id_input = st.sidebar.text_input(
        "Spreadsheet ID or URL",
        value=st.session_state.spreadsheet_id,
        help="Paste the ID from your Google Sheet URL (https://docs.google.com/spreadsheets/d/<ID>/edit)",
    )
    if sheet_id_input:
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_id_input)
        clean_id = m.group(1) if m else sheet_id_input.strip()
        if clean_id != st.session_state.spreadsheet_id:
            st.session_state.spreadsheet_id = clean_id
            st.cache_data.clear()
            st.rerun()

    if st.session_state.spreadsheet_id:
        sheet_url = f"https://docs.google.com/spreadsheets/d/{st.session_state.spreadsheet_id}/edit"
        st.sidebar.link_button("🔗 Open Live Sheet", sheet_url, width="stretch")

    st.sidebar.divider()
    # Documentation & User Guide
    if st.sidebar.button("📖 User Guide & Manual", icon=":material/menu_book:", width="stretch", key="sb_user_guide_btn"):
        show_user_guide_dialog()

    # Feedback & User Voice Module
    with st.sidebar.popover("💬 Feedback & Requests", icon=":material/rate_review:", width="stretch"):
        st.markdown("#### 💬 Share Your Feedback")
        st.caption("Suggest features, report bugs, or share thoughts directly with the SpreadThrift team.")

        fb_category = st.selectbox(
            "Feedback Type",
            ["Feature Request", "Bug Report", "General Feedback", "Other"],
            key="sb_fb_category",
        )

        st.markdown("**Your Rating**")
        fb_stars = st.feedback("stars", key="sb_fb_stars")
        rating_val = (fb_stars + 1) if fb_stars is not None else 5

        fb_message = st.text_area(
            "Your Message",
            placeholder="Tell us what you'd like added or what needs fixing...",
            height=90,
            key="sb_fb_message",
        )

        st.caption(f"Submitting as: `{u_email}`")

        if st.button("Send Feedback", icon=":material/send:", type="primary", width="stretch", key="sb_fb_submit_btn"):
            if not fb_message.strip():
                st.warning("Please type a short message before sending.")
            else:
                try:
                    log_feedback(
                        user_email=u_email,
                        category=fb_category,
                        rating=rating_val,
                        message=fb_message.strip(),
                        spreadsheet_id=st.session_state.spreadsheet_id,
                        sync_to_sheets=True,
                    )
                    st.toast("🎉 Thank you! Your feedback has been received.", icon=":material/favorite:")
                    st.success("✔ Feedback submitted! Recorded in Admin Hub & Google Sheets.")
                except Exception as e:
                    st.error(f"Could not submit feedback: {e}")

    st.sidebar.divider()
    with st.sidebar.expander("⚖️ Legal & Compliance"):
        st.caption(
            "**SpreadThrift** is an independent third-party tool and is not affiliated with, authorized, or endorsed by "
            "Vinted, Google LLC, or Meta Platforms, Inc. All trademarks belong to their respective owners."
        )
        col_sp, col_st = st.columns(2)
        with col_sp:
            if st.button("Privacy Policy", width="stretch", key="sb_privacy_btn"):
                show_privacy_dialog()
        with col_st:
            if st.button("Terms of Service", width="stretch", key="sb_terms_btn"):
                show_terms_dialog()

# -----------------------------------------------------------------------------
# Multi-Page Navigation
# -----------------------------------------------------------------------------
pages = [
    st.Page("app_pages/metrics.py", title="Metrics & Analytics", icon=":material/monitoring:", default=True),
    st.Page("app_pages/sync.py", title="Sync & Batch Import", icon=":material/cloud_sync:"),
]

if st.session_state.get("is_admin", False):
    pages.append(st.Page("app_pages/admin.py", title="Admin Dashboard", icon=":material/admin_panel_settings:"))

pg = st.navigation(pages, position="sidebar")
pg.run()
