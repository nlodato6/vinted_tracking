"""Google OAuth 2.0 helper for multi-tenant Streamlit authentication."""

import os
from typing import Optional, Dict, Any, Tuple
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
import requests

from src.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def get_oauth_config() -> Optional[Dict[str, Any]]:
    """Retrieves Google OAuth Client ID and Secret from Streamlit secrets, environment, or settings."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "") or settings.google_client_id
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "") or settings.google_client_secret

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
    """Returns the configured OAuth redirect URI (defaults to http://localhost:8501).

    Priority:
    1. Explicit GOOGLE_REDIRECT_URI in Streamlit secrets or env (if not a placeholder)
    2. Auto-detected from browser context (st.context.url) on Streamlit Cloud
    3. Fallback to http://localhost:8501
    """
    uri = ""
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "GOOGLE_REDIRECT_URI" in st.secrets:
            val = str(st.secrets["GOOGLE_REDIRECT_URI"]).strip().rstrip("/")
            if val and "your-app-name" not in val and "example.com" not in val:
                uri = val

        if not uri and hasattr(st, "context") and hasattr(st.context, "url") and st.context.url:
            ctx_val = str(st.context.url).strip().rstrip("/")
            if ctx_val and ctx_val.startswith(("http://", "https://")):
                uri = ctx_val
    except Exception:
        pass

    if not uri:
        env_val = os.getenv("GOOGLE_REDIRECT_URI", "").strip().rstrip("/")
        if env_val and "your-app-name" not in env_val:
            uri = env_val

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
        autogenerate_code_verifier=False,
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
        autogenerate_code_verifier=False,
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
