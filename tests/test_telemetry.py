"""Unit tests for telemetry, event logging, and error alert tracking."""

import pytest
from pathlib import Path
from src.config import settings
from src.telemetry import (
    log_session,
    log_event,
    log_error,
    get_telemetry_summary,
    resolve_error,
    resolve_all_errors,
    delete_error,
    clear_all_telemetry,
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
    assert "tsavoie90@gmail.com" in settings.admin_email_list
    assert "nicashlod@gmail.com" in settings.admin_email_list
    assert len(settings.admin_email_list) >= 2
