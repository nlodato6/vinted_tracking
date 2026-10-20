"""Unit tests for notification and sync status tracking."""

import json
from unittest.mock import MagicMock, patch
import pytest

from src.notifications import (
    get_sync_status,
    save_sync_status,
    record_html_parse_event,
    record_email_parse_event,
    update_sheet_status_banner,
)


@pytest.fixture
def mock_sheets_client():
    client = MagicMock()
    mock_sh = MagicMock()
    mock_ws = MagicMock()
    client.get_spreadsheet.return_value = mock_sh
    mock_sh.worksheet.return_value = mock_ws
    return client, mock_ws


def test_get_and_save_sync_status(tmp_path, monkeypatch):
    test_file = tmp_path / ".test_sync_status.json"
    monkeypatch.setattr("src.notifications.STATUS_FILE", test_file)

    initial = get_sync_status()
    assert initial["last_html_parsed_at"] == "Not yet run"
    assert initial["last_email_parsed_at"] == "Not yet run"

    save_sync_status({"last_html_parsed_at": "2026-10-19 20:00", "last_email_parsed_at": "2026-10-19 20:15"})
    updated = get_sync_status()
    assert updated["last_html_parsed_at"] == "2026-10-19 20:00"
    assert updated["last_email_parsed_at"] == "2026-10-19 20:15"


def test_update_sheet_status_banner(mock_sheets_client):
    client, ws = mock_sheets_client
    result = update_sheet_status_banner(client=client, html_time="2026-10-19 20:00", email_time="2026-10-19 20:15")

    assert result is True
    ws.update.assert_called_once()
    args, kwargs = ws.update.call_args
    values = kwargs.get("values") or args[1]
    banner_text = values[0][0]
    assert "🟢 SYSTEM STATUS: Active" in banner_text
    assert "Last HTML Import: 2026-10-19 20:00" in banner_text
    assert "Last Email Parsed: 2026-10-19 20:15" in banner_text

    ws.format.assert_called_once()
    fmt_range, fmt_style = ws.format.call_args[0]
    assert fmt_range == "A2:H2"
    assert fmt_style["backgroundColor"]["green"] > 0.8


def test_record_html_parse_event(mock_sheets_client, tmp_path, monkeypatch):
    test_file = tmp_path / ".test_sync_status.json"
    monkeypatch.setattr("src.notifications.STATUS_FILE", test_file)
    client, ws = mock_sheets_client

    record_html_parse_event(details="Closet: closet.html (154 items)", client=client)
    status = get_sync_status()
    assert status["last_html_parsed_at"] != "Not yet run"
    assert "Closet: closet.html" in status["last_html_details"]
    ws.update.assert_called_once()


def test_record_email_parse_event(mock_sheets_client, tmp_path, monkeypatch):
    test_file = tmp_path / ".test_sync_status.json"
    monkeypatch.setattr("src.notifications.STATUS_FILE", test_file)
    client, ws = mock_sheets_client

    record_email_parse_event(details="Sale: Vintage Jacket", client=client)
    status = get_sync_status()
    assert status["last_email_parsed_at"] != "Not yet run"
    assert "Sale: Vintage Jacket" in status["last_email_details"]
    ws.update.assert_called_once()
