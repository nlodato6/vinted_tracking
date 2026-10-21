from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Base Paths
    base_dir: Path = Path(__file__).resolve().parent.parent

    # Google Sheets
    google_service_account_file: Path = Field(
        default=Path("credentials.json"),
        description="Path to Google Cloud service account JSON credentials",
    )
    google_sheet_id: str = Field(
        default="",
        description="Google Spreadsheet ID from URL",
    )
    google_sheet_title: str = Field(
        default="Vinted Sales & Inventory Tracker",
        description="Title of spreadsheet if sheet ID is not given",
    )
    google_client_id: str = Field(
        default="",
        description="Google OAuth 2.0 Web Client ID",
    )
    google_client_secret: str = Field(
        default="",
        description="Google OAuth 2.0 Web Client Secret",
    )

    # Vinted Settings
    vinted_domain: str = Field(
        default="vinted.com",
        description="Vinted regional domain",
    )
    vinted_username: str = Field(
        default="",
        description="Vinted seller username",
    )
    # Webhook server
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8000
    webhook_secret: str = ""

    # Gmail IMAP Automation
    gmail_user: str = Field(
        default="",
        description="Gmail address where Vinted emails are sent (e.g. your-email@gmail.com)",
    )
    gmail_app_password: str = Field(
        default="",
        description="Google 16-character App Password for IMAP access",
    )

    # Admin Dashboard & Access
    admin_emails: str = Field(
        default="nicashlod@gmail.com,admin@spreadthrift.com",
        description="Comma-separated emails authorized to view Admin telemetry and error alerts",
    )
    admin_password: str = Field(
        default="PassWord123!@#",
        description="Master passcode for direct Admin portal login",
    )
    fallback_admin_email: str = Field(
        default="nicashlod@gmail.com",
        description="Fallback admin email for manual login",
    )
    fallback_admin_password: str = Field(
        default="PassWord123!@#",
        description="Password for manual login fallback",
    )
    admin_sheet_id: str = Field(
        default="1osw6Osf37GMeCEf3dXA6_TqaUPCCfsUg4VS5HIKLGWg",
        description="Google Spreadsheet ID for admin metrics, error logs, and user feedback",
    )

    @property
    def admin_email_list(self) -> list[str]:
        raw = self.admin_emails
        try:
            import streamlit as st
            if hasattr(st, "secrets") and "ADMIN_EMAILS" in st.secrets:
                raw = st.secrets["ADMIN_EMAILS"]
        except Exception:
            pass
        emails = [e.strip().lower() for e in raw.split(",") if e.strip()]
        if "nicashlod@gmail.com" in emails and "nicashlod@gmail" not in emails:
            emails.append("nicashlod@gmail")
        return emails

    @property
    def vinted_base_url(self) -> str:
        return f"https://www.{self.vinted_domain}"


settings = Settings()
