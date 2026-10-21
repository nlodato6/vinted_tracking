"""Unit tests for telemetry, event logging, and error alert tracking."""

import pytest
from pathlib import Path
from src.config import settings
from src.telemetry import (
    log_session,
    log_event,
    log_error,
    log_feedback,
    get_feedback_summary,
    get_feedback_list,
    update_feedback_status,
    delete_feedback,
    sync_feedback_to_google_sheet,
    get_telemetry_summary,
    resolve_error,
    resolve_all_errors,
    delete_error,
    clear_all_telemetry,
    save_user_spreadsheet,
    get_user_spreadsheet,
)


@pytest.fixture(autouse=True)
def clean_telemetry():
    """Ensure clean telemetry state for each test."""
    clear_all_telemetry()
    yield
    clear_all_telemetry()


def test_log_session_and_events():
    log_session("tester@spreadthrift.com", "Test User", auth_provider="google")
    log_event("tester@spreadthrift.com", "html_import", "Uploaded 25 items", spreadsheet_id="test_sheet_123")
    log_event("tester@spreadthrift.com", "lot_created", "Created LOT-001", spreadsheet_id="test_sheet_123")

    summary = get_telemetry_summary()
    assert summary["total_users"] == 1
    assert summary["total_events"] == 3  # 1 login + 2 events
    assert "html_import" in summary["event_breakdown"]
    assert "lot_created" in summary["event_breakdown"]
    assert summary["event_breakdown"]["html_import"] == 1


def test_log_and_resolve_errors():
    # Log two distinct errors
    log_error("user1@example.com", "sync.py", ValueError("Invalid price format"))
    log_error("user2@example.com", "metrics.py", RuntimeError("Timeout connecting to Google Drive"))

    summary = get_telemetry_summary()
    assert summary["unresolved_errors"] == 2
    assert len(summary["recent_errors"]) == 2

    # Resolve first error
    err_id = summary["recent_errors"][0]["id"]
    resolve_error(err_id)

    summary_after = get_telemetry_summary()
    assert summary_after["unresolved_errors"] == 1

    # Bulk resolve
    resolve_all_errors()
    summary_final = get_telemetry_summary()
    assert summary_final["unresolved_errors"] == 0


def test_admin_config_email_list():
    assert "nicashlod@gmail.com" in settings.admin_email_list
    assert "admin@spreadthrift.com" in settings.admin_email_list
    assert len(settings.admin_email_list) >= 2


def test_log_feedback_and_summary():
    fid1 = log_feedback("seller1@vinted.com", "Feature Request", 5, "Add automated label printing", sync_to_sheets=False)
    fid2 = log_feedback("seller2@vinted.com", "Bug Report", 3, "Image upload took two clicks", sync_to_sheets=False)

    summary = get_feedback_summary()
    assert summary["total_feedback"] == 2
    assert summary["new_feedback"] == 2
    assert summary["avg_rating"] == 4.0
    assert summary["by_category"]["Feature Request"] == 1
    assert summary["by_category"]["Bug Report"] == 1
    assert summary["unsynced_count"] == 2

    telemetry_summary = get_telemetry_summary()
    assert telemetry_summary["new_feedback"] == 2
    assert telemetry_summary["total_feedback"] == 2


def test_feedback_status_filtering_and_notes():
    fid = log_feedback("user@example.com", "General Feedback", 5, "Loving the new SpreadThrift branding!", sync_to_sheets=False)
    items = get_feedback_list(status_filter="new")
    assert len(items) == 1
    assert items[0]["id"] == fid

    # Update status to reviewed with note
    update_feedback_status(fid, "reviewed", admin_notes="Included in monthly newsletter shoutout")

    reviewed_items = get_feedback_list(status_filter="reviewed")
    assert len(reviewed_items) == 1
    assert reviewed_items[0]["admin_notes"] == "Included in monthly newsletter shoutout"

    # Category filter
    feat_items = get_feedback_list(category_filter="Feature Request")
    assert len(feat_items) == 0

    gen_items = get_feedback_list(category_filter="General Feedback")
    assert len(gen_items) == 1

    # Delete
    delete_feedback(fid)
    assert len(get_feedback_list()) == 0


def test_sync_feedback_to_google_sheet(monkeypatch):
    log_feedback("offline_user@vinted.com", "Feature Request", 4, "Sync while offline", sync_to_sheets=False)
    
    # Mock Google Sheets append to avoid network calls in unit tests
    def mock_append(*args, **kwargs):
        return True

    monkeypatch.setattr("src.telemetry.append_feedback_to_google_sheet", mock_append)

    res = sync_feedback_to_google_sheet()
    assert res["success"] is True
    assert res["synced"] == 1
    assert res["total_pending"] == 1

    summary = get_feedback_summary()
    assert summary["unsynced_count"] == 0


def test_user_preferences_spreadsheet():
    # Initially None
    assert get_user_spreadsheet("new_reseller@example.com") is None

    # Save preference
    save_user_spreadsheet("new_reseller@example.com", "sheet_xyz_789")
    assert get_user_spreadsheet("new_reseller@example.com") == "sheet_xyz_789"
    # Case insensitivity check
    assert get_user_spreadsheet("NEW_RESELLER@EXAMPLE.COM") == "sheet_xyz_789"

    # Update preference
    save_user_spreadsheet("new_reseller@example.com", "sheet_updated_999")
    assert get_user_spreadsheet("new_reseller@example.com") == "sheet_updated_999"


def test_user_email_credentials_persistence():
    from src.telemetry import (
        save_user_email_credentials,
        get_user_email_credentials,
        clear_user_email_credentials,
    )
    user = "email_user@example.com"
    # Initially None
    u, p = get_user_email_credentials(user)
    assert u is None and p is None

    # Save
    save_user_email_credentials(user, "tsavoie90@gmail.com", "1234 5678 9012 3456")
    u, p = get_user_email_credentials(user)
    assert u == "tsavoie90@gmail.com"
    assert p == "1234 5678 9012 3456"

    # Clear
    clear_user_email_credentials(user)
    u_after, p_after = get_user_email_credentials(user)
    assert u_after is None and p_after is None


