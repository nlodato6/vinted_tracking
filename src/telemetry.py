"""Telemetry and error tracking module for SpreadThrift Admin Dashboard.

Stores user activity metrics, session logins, and runtime errors in a local SQLite database.
"""

import os
import sqlite3
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

from src.config import settings

DB_DIR = settings.base_dir / "data"
DB_PATH = DB_DIR / "telemetry.db"


def _get_connection() -> sqlite3.Connection:
    """Returns a SQLite connection with WAL journal mode enabled."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_telemetry_db():
    """Initializes tables for sessions, user events, and error tracking."""
    conn = _get_connection()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email TEXT NOT NULL,
                user_name TEXT,
                login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                auth_provider TEXT DEFAULT 'google',
                ip_client TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_email TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_details TEXT,
                spreadsheet_id TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_email TEXT,
                page TEXT,
                error_type TEXT,
                error_message TEXT,
                stack_trace TEXT,
                status TEXT DEFAULT 'unresolved'
            );
        """)
        # Indexes for fast querying
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON user_events(timestamp);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON user_events(event_type);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_errors_status ON app_errors(status);")
    conn.close()


# Ensure DB exists on import
init_telemetry_db()


def log_session(user_email: str, user_name: str = "", auth_provider: str = "google", ip_client: str = ""):
    """Records a user login event."""
    try:
        conn = _get_connection()
        with conn:
            conn.execute(
                "INSERT INTO user_sessions (user_email, user_name, auth_provider, ip_client) VALUES (?, ?, ?, ?)",
                (user_email.strip().lower(), user_name.strip(), auth_provider, ip_client),
            )
            # Also log a login event
            conn.execute(
                "INSERT INTO user_events (user_email, event_type, event_details) VALUES (?, 'user_login', ?)",
                (user_email.strip().lower(), f"Signed in via {auth_provider}"),
            )
        conn.close()
    except Exception as e:
        print(f"[Telemetry Warning] Failed to log session: {e}")


def log_event(user_email: str, event_type: str, details: Optional[str] = None, spreadsheet_id: Optional[str] = None):
    """Records an interaction event (e.g. html_import, lot_created, supply_saved, email_sync)."""
    try:
        conn = _get_connection()
        with conn:
            conn.execute(
                "INSERT INTO user_events (user_email, event_type, event_details, spreadsheet_id) VALUES (?, ?, ?, ?)",
                (user_email.strip().lower(), event_type, details or "", spreadsheet_id or ""),
            )
        conn.close()
    except Exception as e:
        print(f"[Telemetry Warning] Failed to log event {event_type}: {e}")


def log_error(user_email: Optional[str], page: str, error: Any, stack_trace: Optional[str] = None):
    """Logs an application error or exception for admin dashboard notifications."""
    try:
        err_msg = str(error)
        err_type = type(error).__name__ if isinstance(error, Exception) else "AppError"
        if not stack_trace and isinstance(error, Exception):
            stack_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))

        conn = _get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO app_errors (user_email, page, error_type, error_message, stack_trace, status)
                VALUES (?, ?, ?, ?, ?, 'unresolved')
                """,
                (user_email or "anonymous", page, err_type, err_msg, stack_trace or ""),
            )
        conn.close()
    except Exception as e:
        print(f"[Telemetry Warning] Failed to log error: {e}")


def get_telemetry_summary() -> Dict[str, Any]:
    """Compiles dashboard telemetry: active users, event counts, time series, and error states."""
    conn = _get_connection()
    try:
        # 1. Total unique users
        c = conn.cursor()
        c.execute("SELECT COUNT(DISTINCT user_email) FROM user_events")
        total_users = c.fetchone()[0] or 0

        # 2. Active users today (last 24h)
        one_day_ago = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("SELECT COUNT(DISTINCT user_email) FROM user_events WHERE timestamp >= ?", (one_day_ago,))
        active_today = c.fetchone()[0] or 0

        # 3. Total events count
        c.execute("SELECT COUNT(*) FROM user_events")
        total_events = c.fetchone()[0] or 0

        # 4. Unresolved errors
        c.execute("SELECT COUNT(*) FROM app_errors WHERE status = 'unresolved'")
        unresolved_errors = c.fetchone()[0] or 0

        # 5. Events breakdown by type
        c.execute("SELECT event_type, COUNT(*) as cnt FROM user_events GROUP BY event_type ORDER BY cnt DESC")
        event_breakdown = {row["event_type"]: row["cnt"] for row in c.fetchall()}

        # 6. Daily activity timeline (last 30 days)
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        c.execute("""
            SELECT date(timestamp) as dt, COUNT(*) as cnt
            FROM user_events
            WHERE date(timestamp) >= ?
            GROUP BY date(timestamp)
            ORDER BY dt ASC
        """, (thirty_days_ago,))
        daily_timeline = [{"date": row["dt"], "events": row["cnt"]} for row in c.fetchall()]

        # 7. Recent events list (last 100)
        c.execute("""
            SELECT id, timestamp, user_email, event_type, event_details, spreadsheet_id
            FROM user_events
            ORDER BY timestamp DESC
            LIMIT 100
        """)
        recent_events = [dict(row) for row in c.fetchall()]

        # 8. All recent errors (last 50)
        c.execute("""
            SELECT id, timestamp, user_email, page, error_type, error_message, stack_trace, status
            FROM app_errors
            ORDER BY timestamp DESC
            LIMIT 50
        """)
        recent_errors = [dict(row) for row in c.fetchall()]

        # 9. User leaderboard
        c.execute("""
            SELECT user_email, MAX(timestamp) as last_seen, COUNT(*) as event_count
            FROM user_events
            GROUP BY user_email
            ORDER BY event_count DESC
            LIMIT 50
        """)
        users_list = [dict(row) for row in c.fetchall()]

        return {
            "total_users": total_users,
            "active_today": active_today,
            "total_events": total_events,
            "unresolved_errors": unresolved_errors,
            "event_breakdown": event_breakdown,
            "daily_timeline": daily_timeline,
            "recent_events": recent_events,
            "recent_errors": recent_errors,
            "users_list": users_list,
        }
    finally:
        conn.close()


def resolve_error(error_id: int):
    """Marks a specific error as resolved."""
    conn = _get_connection()
    with conn:
        conn.execute("UPDATE app_errors SET status = 'resolved' WHERE id = ?", (error_id,))
    conn.close()


def resolve_all_errors():
    """Marks all unresolved errors as resolved."""
    conn = _get_connection()
    with conn:
        conn.execute("UPDATE app_errors SET status = 'resolved' WHERE status = 'unresolved'")
    conn.close()


def delete_error(error_id: int):
    """Deletes an error entry from the database."""
    conn = _get_connection()
    with conn:
        conn.execute("DELETE FROM app_errors WHERE id = ?", (error_id,))
    conn.close()


def clear_all_telemetry():
    """Resets all telemetry data tables."""
    conn = _get_connection()
    with conn:
        conn.execute("DELETE FROM user_sessions")
        conn.execute("DELETE FROM user_events")
        conn.execute("DELETE FROM app_errors")
    conn.close()
