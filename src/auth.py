"""Google OAuth 2.0 helper for multi-tenant Streamlit authentication."""

import os
from typing import Optional, Dict, Any, Tuple
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
import requests

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def get_oauth_config() -> Optional[Dict[str, Any]]:
    """Retrieves Google OAuth Client ID and Secret from Streamlit secrets or environment."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")

    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            client_id = st.secrets.get("GOOGLE_CLIENT_ID", client_id)
            client_secret = st.secrets.get("GOOGLE_CLIENT_SECRET", client_secret)
            if not client_id and "oauth" in st.secrets:
                client_id = st.secrets["oauth"].get("client_id", "")
                client_secret = st.secrets["oauth"].get("client_secret", "")
    except Exception:
        pass

    if client_id and client_secret:
        return {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
    return None


def is_oauth_configured() -> bool:
    """Checks whether Google OAuth client credentials are available."""
    return get_oauth_config() is not None


def get_redirect_uri() -> str:
    """Returns the configured OAuth redirect URI (defaults to http://localhost:8501)."""
    uri = os.getenv("GOOGLE_REDIRECT_URI", "")
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            uri = st.secrets.get("GOOGLE_REDIRECT_URI", uri)
    except Exception:
        pass
    return uri or "http://localhost:8501"


def get_authorization_url(redirect_uri: Optional[str] = None) -> Tuple[str, str]:
    """Generates the Google OAuth authorization URL and state token."""
    redirect_uri = redirect_uri or get_redirect_uri()
    if redirect_uri.startswith("http://"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    config = get_oauth_config()
    if not config:
        raise ValueError("Google OAuth credentials (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) not configured.")

    flow = Flow.from_client_config(
        config,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return auth_url, state


def exchange_code_for_credentials(code: str, redirect_uri: Optional[str] = None, state: Optional[str] = None) -> Tuple[Credentials, Dict[str, Any]]:
    """Exchanges an authorization code for user Credentials and retrieves basic user info."""
    redirect_uri = redirect_uri or get_redirect_uri()
    if redirect_uri.startswith("http://"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    config = get_oauth_config()
    if not config:
        raise ValueError("Google OAuth credentials not configured.")

    flow = Flow.from_client_config(
        config,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        state=state,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Fetch user profile info
    user_info = {}
    try:
        resp = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            user_info = resp.json()
    except Exception:
        pass

    return creds, user_info
