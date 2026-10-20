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

    @property
    def vinted_base_url(self) -> str:
        return f"https://www.{self.vinted_domain}"


settings = Settings()
