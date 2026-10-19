import json
from pathlib import Path
from typing import Optional
import gspread
from google.oauth2.service_account import Credentials

from src.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSheetsClient:
    """Wrapper around gspread for managing the Vinted Google Sheet."""

    def __init__(self, credentials_path: Optional[Path] = None, sheet_id: Optional[str] = None):
        self.credentials_path = credentials_path or settings.google_service_account_file
        self.sheet_id = sheet_id or settings.google_sheet_id
        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None

    @property
    def service_account_email(self) -> Optional[str]:
        """Extracts the client email from the credentials file if it exists."""
        if not self.credentials_path.exists():
            return None
        try:
            with open(self.credentials_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("client_email")
        except Exception:
            return None

    def authenticate(self) -> gspread.Client:
        """Authenticates with Google APIs using the Service Account credentials."""
        if self._client:
            return self._client

        if not self.credentials_path.exists():
            raise FileNotFoundError(
                f"Google Service Account credentials file not found at: {self.credentials_path.resolve()}.\n"
                f"Please place your Google Cloud 'credentials.json' in the project directory."
            )

        creds = Credentials.from_service_account_file(
            str(self.credentials_path),
            scopes=SCOPES,
        )
        self._client = gspread.authorize(creds)
        return self._client

    def get_spreadsheet(self) -> gspread.Spreadsheet:
        """Opens and returns the target spreadsheet by ID or Title."""
        if self._spreadsheet:
            return self._spreadsheet

        client = self.authenticate()

        if self.sheet_id:
            try:
                self._spreadsheet = client.open_by_key(self.sheet_id)
                return self._spreadsheet
            except Exception as e:
                email = self.service_account_email or "your service account"
                raise RuntimeError(
                    f"Could not open spreadsheet by ID '{self.sheet_id}'. "
                    f"Ensure you shared the Google Sheet with editor permissions to: {email}\n"
                    f"Details: {e}"
                )

        try:
            self._spreadsheet = client.open(settings.google_sheet_title)
            return self._spreadsheet
        except Exception as e:
            email = self.service_account_email or "your service account"
            raise RuntimeError(
                f"Could not open spreadsheet with title '{settings.google_sheet_title}'. "
                f"Ensure the sheet exists and is shared with: {email}\n"
                f"Or set GOOGLE_SHEET_ID in your .env.\n"
                f"Details: {e}"
            )
