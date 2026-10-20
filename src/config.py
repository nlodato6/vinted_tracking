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
    headless: bool = Field(
        default=True,
        description="Run Playwright in headless mode (set to false for interactive login)",
    )
    session_dir: Path = Field(
        default=Path(".session"),
        description="Directory to store persistent browser state/cookies",
    )
    storage_state_file: str = "vinted_state.json"

    # Webhook server
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8000
    webhook_secret: str = ""

    # Scheduler
    sync_mode: str = "cron"  # "cron" or "interval"
    sync_schedule_hour: int = 8
    sync_schedule_minute: int = 0
    sync_interval_hours: int = 24

    @property
    def full_storage_state_path(self) -> Path:
        p = Path(self.session_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p / self.storage_state_file

    @property
    def vinted_base_url(self) -> str:
        return f"https://www.{self.vinted_domain}"


settings = Settings()
