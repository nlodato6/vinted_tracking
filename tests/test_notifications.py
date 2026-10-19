"""Unit tests for Vinted authentication failure alerts and notifications."""

import json
from unittest.mock import MagicMock, patch
import pytest

from src.notifications import (
    update_sheet_auth_status,
    find_open_github_auth_issue,
    create_or_update_github_issue,
    resolve_github_auth_issue,
    notify_auth_failure,
    notify_auth_success,
    AUTH_ISSUE_TITLE,
)


@pytest.fixture
def mock_sheets_client():
    client = MagicMock()
    mock_sh = MagicMock()
    mock_ws = MagicMock()
    client.get_spreadsheet.return_value = mock_sh
    mock_sh.worksheet.return_value = mock_ws
    return client, mock_ws


def test_update_sheet_auth_status_valid(mock_sheets_client):
    client, ws = mock_sheets_client
    result = update_sheet_auth_status(client=client, is_valid=True)

    assert result is True
    ws.update.assert_called_once()
    args, kwargs = ws.update.call_args
    assert kwargs.get("range_name") == "A2" or args[0] == "A2"
    values = kwargs.get("values") or args[1]
    assert "🟢 SYSTEM STATUS: Active & Authenticated" in values[0][0]

    ws.format.assert_called_once()
    fmt_range, fmt_style = ws.format.call_args[0]
    assert fmt_range == "A2:H2"
    assert fmt_style["backgroundColor"]["green"] > 0.9  # Green tint


def test_update_sheet_auth_status_invalid(mock_sheets_client):
    client, ws = mock_sheets_client
    result = update_sheet_auth_status(client=client, is_valid=False, error_message="Session expired")

    assert result is True
    ws.update.assert_called_once()
    values = ws.update.call_args[1].get("values") or ws.update.call_args[0][1]
    assert "🔴 AUTHENTICATION REQUIRED" in values[0][0]
    assert "Session expired" in values[0][0]

    fmt_range, fmt_style = ws.format.call_args[0]
    assert fmt_range == "A2:H2"
    assert fmt_style["backgroundColor"]["red"] > 0.9  # Red tint


@patch("urllib.request.urlopen")
def test_find_open_github_auth_issue_api(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([
        {"number": 42, "title": "Other issue"},
        {"number": 99, "title": AUTH_ISSUE_TITLE},
    ]).encode()
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    issue_num = find_open_github_auth_issue("test/repo", token="ghp_test")
    assert issue_num == 99


@patch("urllib.request.urlopen")
def test_find_open_github_auth_issue_not_found(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([
        {"number": 42, "title": "Other issue"},
    ]).encode()
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    issue_num = find_open_github_auth_issue("test/repo", token="ghp_test")
    assert issue_num is None


@patch("src.notifications.find_open_github_auth_issue", return_value=None)
@patch("urllib.request.urlopen")
def test_create_github_issue_when_none_open(mock_urlopen, mock_find, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "mock_token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "test/repo")

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"number": 101}).encode()
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    success = create_or_update_github_issue("Cookies invalid")
    assert success is True
    assert mock_urlopen.call_count == 1
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://api.github.com/repos/test/repo/issues"
    body = json.loads(req.data.decode())
    assert body["title"] == AUTH_ISSUE_TITLE
    assert "Cookies invalid" in body["body"]


@patch("src.notifications.find_open_github_auth_issue", return_value=101)
@patch("urllib.request.urlopen")
def test_update_github_issue_when_already_open(mock_urlopen, mock_find, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "mock_token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "test/repo")

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    success = create_or_update_github_issue("Session still dead")
    assert success is True
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://api.github.com/repos/test/repo/issues/101/comments"
    body = json.loads(req.data.decode())
    assert "Session still dead" in body["body"]


@patch("src.notifications.find_open_github_auth_issue", return_value=101)
@patch("urllib.request.urlopen")
def test_resolve_github_auth_issue_closes_issue(mock_urlopen, mock_find, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "mock_token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "test/repo")

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    success = resolve_github_auth_issue()
    assert success is True
    # Two calls: 1 to comment, 1 to close issue
    assert mock_urlopen.call_count == 2


@patch("src.notifications.update_sheet_auth_status")
@patch("src.notifications.create_or_update_github_issue")
def test_notify_auth_failure_dispatches(mock_gh, mock_sheet):
    notify_auth_failure("Test error")
    mock_sheet.assert_called_once_with(is_valid=False, error_message="Test error")
    mock_gh.assert_called_once_with(error_message="Test error")


@patch("src.notifications.update_sheet_auth_status")
@patch("src.notifications.resolve_github_auth_issue")
def test_notify_auth_success_dispatches(mock_gh, mock_sheet):
    notify_auth_success()
    mock_sheet.assert_called_once_with(is_valid=True)
    mock_gh.assert_called_once()
