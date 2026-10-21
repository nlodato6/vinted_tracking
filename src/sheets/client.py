import json
from pathlib import Path
from typing import Optional, Any, Dict
import gspread
from google.oauth2.service_account import Credentials

from src.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSheetsClient:
    """Wrapper around gspread for managing the Vinted Google Sheet."""

    def __init__(
        self,
        credentials_path: Optional[Path] = None,
        sheet_id: Optional[str] = None,
        credentials: Optional[Any] = None,
    ):
        self.credentials_path = credentials_path or settings.google_service_account_file
        self.sheet_id = sheet_id or settings.google_sheet_id
        self._credentials = credentials
        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None

    @property
    def service_account_email(self) -> Optional[str]:
        """Extracts client or service account email if available."""
        if hasattr(self._credentials, "service_account_email"):
            return getattr(self._credentials, "service_account_email")
        if isinstance(self._credentials, dict):
            return self._credentials.get("client_email")
        if not self.credentials_path or not self.credentials_path.exists():
            return None
        try:
            with open(self.credentials_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("client_email")
        except Exception:
            return None

    def authenticate(self) -> gspread.Client:
        """Authenticates with Google APIs using OAuth, Streamlit secrets, or Service Account credentials."""
        if self._client:
            return self._client

        # 1. Custom credentials passed directly (OAuth user credentials or dict)
        if self._credentials:
            if isinstance(self._credentials, dict):
                creds = Credentials.from_service_account_info(self._credentials, scopes=SCOPES)
            else:
                creds = self._credentials
            self._client = gspread.authorize(creds)
            return self._client

        # 2. Check for Streamlit secrets if running inside Streamlit Cloud
        try:
            import streamlit as st
            if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
                creds = Credentials.from_service_account_info(dict(st.secrets["gcp_service_account"]), scopes=SCOPES)
                self._client = gspread.authorize(creds)
                return self._client
        except Exception:
            pass

        # 3. Local service account JSON file
        if not self.credentials_path or not self.credentials_path.exists():
            raise FileNotFoundError(
                f"Google Service Account credentials file not found at: {self.credentials_path.resolve() if self.credentials_path else 'None'}.\n"
                f"Please place your Google Cloud 'credentials.json' in the project directory or sign in via OAuth."
            )

        creds = Credentials.from_service_account_file(
            str(self.credentials_path),
            scopes=SCOPES,
        )
        self._client = gspread.authorize(creds)
        return self._client

    def clone_template(self, template_id: str, new_title: str) -> gspread.Spreadsheet:
        """Duplicates a master template spreadsheet into the user's Google Drive."""
        client = self.authenticate()
        new_sheet = client.copy(template_id, title=new_title)
        self._spreadsheet = new_sheet
        self.sheet_id = new_sheet.id
        return new_sheet

    def find_sheet_by_title(self, title_query: str = "Vinted Sales") -> Optional[gspread.Spreadsheet]:
        """Searches the user's accessible Google Drive for an existing tracker spreadsheet."""
        client = self.authenticate()
        try:
            sheets = client.openall()
            for s in sheets:
                if title_query.lower() in s.title.lower():
                    self._spreadsheet = s
                    self.sheet_id = s.id
                    return s
        except Exception:
            pass
        return None

    def get_spreadsheet(self) -> gspread.Spreadsheet:
        """Opens and returns the target spreadsheet by ID or Title."""
        if self._spreadsheet:
            return self._spreadsheet

        client = self.authenticate()

        import time
        for attempt in range(5):
            try:
                if self.sheet_id:
                    self._spreadsheet = client.open_by_key(self.sheet_id)
                else:
                    self._spreadsheet = client.open(settings.google_sheet_title)
                return self._spreadsheet
            except Exception as e:
                if "429" in str(e) and attempt < 4:
                    time.sleep(12 * (attempt + 1))
                    continue
                email = self.service_account_email or "your service account"
                target_desc = f"ID '{self.sheet_id}'" if self.sheet_id else f"title '{settings.google_sheet_title}'"
                raise RuntimeError(
                    f"Could not open spreadsheet by {target_desc}. "
                    f"Ensure you shared the Google Sheet with editor permissions to: {email}\n"
                    f"Details: {e}"
                )
