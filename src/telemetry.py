"""Telemetry and error tracking module for SpreadThrift Admin Dashboard.

Stores user activity metrics, session logins, and runtime errors in a local SQLite database.
"""

import os
import sqlite3
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_email TEXT NOT NULL,
                category TEXT NOT NULL,
                rating INTEGER DEFAULT 5,
                message TEXT NOT NULL,
                spreadsheet_id TEXT,
                status TEXT DEFAULT 'new',
                admin_notes TEXT DEFAULT '',
                synced_to_sheets INTEGER DEFAULT 0
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_email TEXT PRIMARY KEY,
                spreadsheet_id TEXT,
                gmail_user TEXT,
                gmail_app_password TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Ensure migration columns if table previously created without them
        try:
            conn.execute("ALTER TABLE user_preferences ADD COLUMN gmail_user TEXT;")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE user_preferences ADD COLUMN gmail_app_password TEXT;")
        except Exception:
            pass

        # Indexes for fast querying
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON user_events(timestamp);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON user_events(event_type);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_errors_status ON app_errors(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_ts ON user_feedback(timestamp);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_status ON user_feedback(status);")
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
        # 10. Feedback metrics
        c.execute("SELECT COUNT(*) FROM user_feedback WHERE status = 'new'")
        new_feedback = c.fetchone()[0] or 0
        c.execute("SELECT COUNT(*) FROM user_feedback")
        total_feedback = c.fetchone()[0] or 0

        return {
            "total_users": total_users,
            "active_today": active_today,
            "total_events": total_events,
            "unresolved_errors": unresolved_errors,
            "new_feedback": new_feedback,
            "total_feedback": total_feedback,
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
        conn.execute("DELETE FROM user_feedback")
        conn.execute("DELETE FROM user_preferences")
    conn.close()


# -----------------------------------------------------------------------------
# User Feedback & Google Sheets Storage Module
# -----------------------------------------------------------------------------
FEEDBACK_SHEET_TITLE = "User_Feedback"
FEEDBACK_HEADERS = [
    "Feedback ID",
    "Timestamp",
    "User Email",
    "Category",
    "Rating",
    "Message",
    "User Spreadsheet ID",
    "Status",
    "Admin Notes",
]


def append_feedback_to_google_sheet(
    feedback_id: int,
    timestamp: str,
    user_email: str,
    category: str,
    rating: int,
    message: str,
    spreadsheet_id: Optional[str] = None,
    status: str = "new",
    admin_notes: str = "",
    target_sheet_id: Optional[str] = None,
) -> bool:
    """Appends a feedback entry to the User_Feedback worksheet in Google Sheets."""
    try:
        from src.sheets.client import GoogleSheetsClient
        sheet_id = target_sheet_id or settings.admin_sheet_id or settings.google_sheet_id
        if not sheet_id:
            return False

        client = GoogleSheetsClient(sheet_id=sheet_id)
        spreadsheet = client.get_spreadsheet()

        # Check if User_Feedback worksheet exists; if not create it with headers
        try:
            worksheet = spreadsheet.worksheet(FEEDBACK_SHEET_TITLE)
        except Exception:
            worksheet = spreadsheet.add_worksheet(title=FEEDBACK_SHEET_TITLE, rows=500, cols=len(FEEDBACK_HEADERS))
            worksheet.append_row(FEEDBACK_HEADERS, value_input_option="USER_ENTERED")
            try:
                worksheet.format("A1:I1", {"textFormat": {"bold": True}})
            except Exception:
                pass

        # Ensure headers exist if sheet was blank
        try:
            existing_vals = worksheet.get_all_values()
            if not existing_vals:
                worksheet.append_row(FEEDBACK_HEADERS, value_input_option="USER_ENTERED")
        except Exception:
            pass

        row_values = [
            int(feedback_id),
            str(timestamp),
            str(user_email),
            str(category),
            f"{rating} / 5",
            str(message),
            str(spreadsheet_id or ""),
            str(status),
            str(admin_notes or ""),
        ]
        worksheet.append_row(row_values, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        print(f"[Telemetry Warning] Failed to append feedback to Google Sheet: {e}")
        return False


def sync_feedback_to_google_sheet(target_sheet_id: Optional[str] = None) -> Dict[str, Any]:
    """Syncs any un-synced feedback items to Google Sheets."""
    conn = _get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM user_feedback WHERE synced_to_sheets = 0 ORDER BY id ASC")
    unsynced = [dict(row) for row in c.fetchall()]
    conn.close()

    if not unsynced:
        return {
            "synced": 0,
            "total_pending": 0,
            "success": True,
            "message": "All feedback entries are already synced to Google Sheets.",
        }

    success_count = 0
    for item in unsynced:
        ok = append_feedback_to_google_sheet(
            feedback_id=item["id"],
            timestamp=item["timestamp"],
            user_email=item["user_email"],
            category=item["category"],
            rating=item["rating"],
            message=item["message"],
            spreadsheet_id=item.get("spreadsheet_id"),
            status=item.get("status", "new"),
            admin_notes=item.get("admin_notes", ""),
            target_sheet_id=target_sheet_id,
        )
        if ok:
            conn = _get_connection()
            with conn:
                conn.execute("UPDATE user_feedback SET synced_to_sheets = 1 WHERE id = ?", (item["id"],))
            conn.close()
            success_count += 1
        else:
            break

    return {
        "synced": success_count,
        "total_pending": len(unsynced),
        "success": success_count == len(unsynced),
        "message": f"Successfully synced {success_count} of {len(unsynced)} feedback entries to Google Sheets.",
    }


def log_feedback(
    user_email: str,
    category: str,
    rating: int,
    message: str,
    spreadsheet_id: Optional[str] = None,
    sync_to_sheets: bool = True,
) -> int:
    """Logs a user feedback entry to SQLite and attempts to append to Google Sheets."""
    conn = _get_connection()
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO user_feedback (timestamp, user_email, category, rating, message, spreadsheet_id, status, synced_to_sheets)
                VALUES (?, ?, ?, ?, ?, ?, 'new', 0)
                """,
                (ts, user_email.strip().lower(), category.strip(), rating, message.strip(), spreadsheet_id or ""),
            )
            feedback_id = cursor.lastrowid

            # Also log as a user event for telemetry feed
            conn.execute(
                """
                INSERT INTO user_events (timestamp, user_email, event_type, event_details, spreadsheet_id)
                VALUES (?, ?, 'feedback_submitted', ?, ?)
                """,
                (ts, user_email.strip().lower(), f"{category} ({rating}★): {message[:60]}", spreadsheet_id or ""),
            )

        # Attempt immediate Google Sheets append
        if sync_to_sheets:
            ok = append_feedback_to_google_sheet(
                feedback_id=feedback_id,
                timestamp=ts,
                user_email=user_email,
                category=category,
                rating=rating,
                message=message,
                spreadsheet_id=spreadsheet_id,
                status="new",
            )
            if ok:
                with conn:
                    conn.execute("UPDATE user_feedback SET synced_to_sheets = 1 WHERE id = ?", (feedback_id,))

        return feedback_id
    finally:
        conn.close()


def update_feedback_status(feedback_id: int, new_status: str, admin_notes: Optional[str] = None):
    """Updates status and optional notes for a feedback item."""
    conn = _get_connection()
    with conn:
        if admin_notes is not None:
            conn.execute(
                "UPDATE user_feedback SET status = ?, admin_notes = ? WHERE id = ?",
                (new_status, admin_notes, feedback_id),
            )
        else:
            conn.execute(
                "UPDATE user_feedback SET status = ? WHERE id = ?",
                (new_status, feedback_id),
            )
    conn.close()


def delete_feedback(feedback_id: int):
    """Deletes a feedback record from the database."""
    conn = _get_connection()
    with conn:
        conn.execute("DELETE FROM user_feedback WHERE id = ?", (feedback_id,))
    conn.close()


def get_feedback_list(
    status_filter: Optional[str] = None,
    category_filter: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Retrieves feedback items filtered by status or category."""
    conn = _get_connection()
    try:
        c = conn.cursor()
        query = "SELECT * FROM user_feedback WHERE 1=1"
        params = []
        if status_filter and status_filter.lower() != "all":
            query += " AND status = ?"
            params.append(status_filter.lower())
        if category_filter and category_filter.lower() != "all":
            query += " AND category = ?"
            params.append(category_filter)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        c.execute(query, tuple(params))
        return [dict(row) for row in c.fetchall()]
    finally:
        conn.close()


def get_feedback_summary() -> Dict[str, Any]:
    """Computes feedback KPIs for the Admin Dashboard."""
    conn = _get_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM user_feedback")
        total_feedback = c.fetchone()[0] or 0

        c.execute("SELECT COUNT(*) FROM user_feedback WHERE status = 'new'")
        new_feedback = c.fetchone()[0] or 0

        c.execute("SELECT AVG(rating) FROM user_feedback")
        avg_rating_raw = c.fetchone()[0]
        avg_rating = round(float(avg_rating_raw), 1) if avg_rating_raw is not None else 0.0

        c.execute("SELECT category, COUNT(*) as cnt FROM user_feedback GROUP BY category")
        by_category = {row["category"]: row["cnt"] for row in c.fetchall()}

        c.execute("SELECT rating, COUNT(*) as cnt FROM user_feedback GROUP BY rating ORDER BY rating DESC")
        rating_dist = {row["rating"]: row["cnt"] for row in c.fetchall()}

        c.execute("SELECT COUNT(*) FROM user_feedback WHERE synced_to_sheets = 0")
        unsynced_count = c.fetchone()[0] or 0

        return {
            "total_feedback": total_feedback,
            "new_feedback": new_feedback,
            "avg_rating": avg_rating,
            "by_category": by_category,
            "rating_dist": rating_dist,
            "unsynced_count": unsynced_count,
        }
    finally:
        conn.close()


def save_user_spreadsheet(user_email: str, spreadsheet_id: str):
    """Saves the user's connected spreadsheet ID for automatic reconnects."""
    try:
        conn = _get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO user_preferences (user_email, spreadsheet_id, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_email) DO UPDATE SET spreadsheet_id = excluded.spreadsheet_id, updated_at = CURRENT_TIMESTAMP
                """,
                (user_email.strip().lower(), spreadsheet_id.strip()),
            )
        conn.close()
    except Exception as e:
        print(f"[Telemetry Warning] Failed to save user spreadsheet: {e}")


def get_user_spreadsheet(user_email: str) -> Optional[str]:
    """Retrieves the user's previously connected spreadsheet ID."""
    try:
        conn = _get_connection()
        c = conn.cursor()
        c.execute("SELECT spreadsheet_id FROM user_preferences WHERE user_email = ?", (user_email.strip().lower(),))
        row = c.fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def save_user_email_credentials(user_email: str, gmail_user: str, gmail_app_password: str):
    """Persists user email credentials in local preferences for automatic reuse."""
    try:
        conn = _get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO user_preferences (user_email, gmail_user, gmail_app_password, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_email) DO UPDATE SET 
                    gmail_user = excluded.gmail_user,
                    gmail_app_password = excluded.gmail_app_password,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_email.strip().lower(), gmail_user.strip(), gmail_app_password.strip()),
            )
        conn.close()
    except Exception as e:
        print(f"[Telemetry Warning] Failed to save user email credentials: {e}")


def get_user_email_credentials(user_email: str) -> Tuple[Optional[str], Optional[str]]:
    """Retrieves saved Gmail address and App Password for a user."""
    try:
        conn = _get_connection()
        c = conn.cursor()
        c.execute(
            "SELECT gmail_user, gmail_app_password FROM user_preferences WHERE user_email = ?",
            (user_email.strip().lower(),),
        )
        row = c.fetchone()
        conn.close()
        if row:
            return (row[0] or None, row[1] or None)
        return (None, None)
    except Exception:
        return (None, None)


def clear_user_email_credentials(user_email: str):
    """Clears saved Gmail credentials for a user."""
    try:
        conn = _get_connection()
        with conn:
            conn.execute(
                "UPDATE user_preferences SET gmail_user = NULL, gmail_app_password = NULL, updated_at = CURRENT_TIMESTAMP WHERE user_email = ?",
                (user_email.strip().lower(),),
            )
        conn.close()
    except Exception as e:
        print(f"[Telemetry Warning] Failed to clear user email credentials: {e}")


def save_email_credentials_to_env(gmail_user: str, gmail_app_password: str):
    """Updates or adds GMAIL_USER and GMAIL_APP_PASSWORD in the local .env file."""
    env_path = settings.base_dir / ".env"
    try:
        lines = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()

        found_user = False
        found_pwd = False
        new_lines = []

        for line in lines:
            if line.startswith("GMAIL_USER="):
                new_lines.append(f"GMAIL_USER={gmail_user.strip()}")
                found_user = True
            elif line.startswith("GMAIL_APP_PASSWORD="):
                new_lines.append(f"GMAIL_APP_PASSWORD={gmail_app_password.strip()}")
                found_pwd = True
            else:
                new_lines.append(line)

        if not found_user:
            new_lines.append(f"GMAIL_USER={gmail_user.strip()}")
        if not found_pwd:
            new_lines.append(f"GMAIL_APP_PASSWORD={gmail_app_password.strip()}")

        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        os.environ["GMAIL_USER"] = gmail_user.strip()
        os.environ["GMAIL_APP_PASSWORD"] = gmail_app_password.strip()
    except Exception as e:
        print(f"[Warning] Failed to update .env: {e}")


