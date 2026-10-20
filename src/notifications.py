"""Notification and sync status tracking for HTML imports and email parsing."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from rich.console import Console
from src.sheets.client import GoogleSheetsClient
from src.sheets.schemas import TAB_ANALYTICS_DASHBOARD

console = Console()

STATUS_FILE = Path(__file__).resolve().parent.parent / ".sync_status.json"


def get_sync_status() -> Dict[str, str]:
    """Reads the persistent sync timestamp tracking state."""
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "last_html_parsed_at": "Not yet run",
        "last_html_details": "",
        "last_email_parsed_at": "Not yet run",
        "last_email_details": "",
    }


def save_sync_status(data: Dict[str, str]) -> None:
    """Saves sync timestamps to persistent local file."""
    try:
        STATUS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        console.print(f"[dim]Warning: Could not save .sync_status.json: {e}[/dim]")


def update_sheet_status_banner(
    client: Optional[GoogleSheetsClient] = None,
    html_time: Optional[str] = None,
    email_time: Optional[str] = None,
) -> bool:
    """
    Updates the prominent System Status Banner in Row 2 of Analytics_Dashboard:
    Displays the timestamp of the last HTML parser run and last email parsed.
    """
    try:
        if not client:
            client = GoogleSheetsClient()
        sh = client.get_spreadsheet()
        ws_dash = sh.worksheet(TAB_ANALYTICS_DASHBOARD)

        status_data = get_sync_status()
        h_time = html_time or status_data.get("last_html_parsed_at") or "Not yet run"
        e_time = email_time or status_data.get("last_email_parsed_at") or "Not yet run"

        status_text = f"🟢 SYSTEM STATUS: Active | Last HTML Import: {h_time} | Last Email Parsed: {e_time}"
        bg_color = {"red": 0.90, "green": 0.97, "blue": 0.92}  # Soft mint green
        text_color = {"red": 0.08, "green": 0.40, "blue": 0.20} # Dark green

        ws_dash.update(range_name="A2", values=[[status_text, "", "", "", "", "", "", ""]])
        ws_dash.format(
            "A2:H2",
            {
                "backgroundColor": bg_color,
                "textFormat": {
                    "foregroundColor": text_color,
                    "bold": True,
                    "fontSize": 10,
                },
                "horizontalAlignment": "CENTER",
            },
        )
        return True
    except Exception as e:
        console.print(f"[dim]Warning: Failed to update Google Sheet status banner: {e}[/dim]")
        return False


def record_html_parse_event(details: str = "", client: Optional[GoogleSheetsClient] = None) -> None:
    """Records an HTML import event timestamp and updates the spreadsheet banner."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    status = get_sync_status()
    status["last_html_parsed_at"] = now_str
    status["last_html_details"] = details
    save_sync_status(status)
    update_sheet_status_banner(client=client, html_time=now_str)
    console.print(f"[dim]Recorded HTML parser timestamp: {now_str}[/dim]")


def record_email_parse_event(details: str = "", client: Optional[GoogleSheetsClient] = None) -> None:
    """Records an email parsing event timestamp and updates the spreadsheet banner."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    status = get_sync_status()
    status["last_email_parsed_at"] = now_str
    status["last_email_details"] = details
    save_sync_status(status)
    update_sheet_status_banner(client=client, email_time=now_str)
    console.print(f"[dim]Recorded email parser timestamp: {now_str}[/dim]")


# Backward compatibility aliases
def notify_auth_failure(error_message: str = "") -> None:
    pass

def notify_auth_success() -> None:
    pass

def update_sheet_auth_status(client: Optional[GoogleSheetsClient] = None, is_valid: bool = True, error_message: str = "") -> bool:
    return update_sheet_status_banner(client=client)
