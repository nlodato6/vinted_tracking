"""Notification system for Vinted authentication alerts via Google Sheets banner and GitHub Issues."""

import os
import json
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional, Dict, Any, List
from rich.console import Console
from src.sheets.client import GoogleSheetsClient
from src.sheets.schemas import TAB_ANALYTICS_DASHBOARD

console = Console()

GITHUB_REPO_DEFAULT = "nlodato6/vinted_tracking"
AUTH_ISSUE_TITLE = "⚠️ Vinted Authentication Expired — Re-login Required"
AUTH_ISSUE_LABEL = "authentication"


def update_sheet_auth_status(client: Optional[GoogleSheetsClient] = None, is_valid: bool = True, error_message: str = "") -> bool:
    """
    Updates the prominent System Status Banner in Row 2 of Analytics_Dashboard:
    - Green banner when Vinted session is active and healthy.
    - Red alert banner when authentication is required or session expired.
    """
    try:
        if not client:
            client = GoogleSheetsClient()
        sh = client.get_spreadsheet()
        ws_dash = sh.worksheet(TAB_ANALYTICS_DASHBOARD)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        if is_valid:
            status_text = f"🟢 SYSTEM STATUS: Active & Authenticated | Cloud Automation Healthy (Verified: {now_str})"
            bg_color = {"red": 0.90, "green": 0.97, "blue": 0.92}  # Soft mint green
            text_color = {"red": 0.08, "green": 0.40, "blue": 0.20} # Dark green
        else:
            err_snippet = f" ({error_message})" if error_message else ""
            status_text = f"🔴 AUTHENTICATION REQUIRED: Vinted session expired{err_snippet}. Run 'python -m src.cli login' on computer | Alert: {now_str}"
            bg_color = {"red": 0.99, "green": 0.90, "blue": 0.90}  # Soft warning rose
            text_color = {"red": 0.65, "green": 0.10, "blue": 0.10} # Dark red

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


def _get_github_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Vinted-Tracking-Bot",
    }


def find_open_github_auth_issue(repo: str, token: Optional[str]) -> Optional[int]:
    """Finds existing open authentication issue number if one exists."""
    if token:
        try:
            url = f"https://api.github.com/repos/{repo}/issues?state=open&per_page=20"
            req = urllib.request.Request(url, headers=_get_github_headers(token))
            with urllib.request.urlopen(req, timeout=10) as resp:
                issues = json.loads(resp.read().decode())
                for issue in issues:
                    if AUTH_ISSUE_TITLE.lower() in issue.get("title", "").lower():
                        return issue.get("number")
        except Exception:
            pass

    # Fallback to local gh CLI if available
    try:
        cmd = ["gh", "issue", "list", "--repo", repo, "--state", "open", "--json", "number,title"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            issues = json.loads(result.stdout)
            for issue in issues:
                if AUTH_ISSUE_TITLE.lower() in issue.get("title", "").lower():
                    return issue.get("number")
    except Exception:
        pass

    return None


def create_or_update_github_issue(error_message: str = "") -> bool:
    """
    Creates an issue on the GitHub repository to trigger email and mobile push notifications.
    If an open issue already exists, leaves an update comment instead of creating duplicates.
    """
    repo = os.environ.get("GITHUB_REPOSITORY", GITHUB_REPO_DEFAULT)
    token = os.environ.get("GITHUB_TOKEN")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

    existing_issue_num = find_open_github_auth_issue(repo, token)

    if existing_issue_num:
        comment_body = f"⚠️ Automated sync re-check detected authentication still required on `{timestamp}`.\n*Details:* `{error_message or 'Session token expired'}`"
        if token:
            try:
                url = f"https://api.github.com/repos/{repo}/issues/{existing_issue_num}/comments"
                data = json.dumps({"body": comment_body}).encode()
                req = urllib.request.Request(url, data=data, headers=_get_github_headers(token))
                with urllib.request.urlopen(req, timeout=10):
                    console.print(f"[yellow]Updated open GitHub issue #{existing_issue_num} with latest check timestamp.[/yellow]")
                    return True
            except Exception:
                pass
        try:
            cmd = ["gh", "issue", "comment", str(existing_issue_num), "--repo", repo, "--body", comment_body]
            subprocess.run(cmd, capture_output=True, text=True, check=False)
            return True
        except Exception:
            return False

    issue_body = f"""## ⚠️ Vinted Authentication Required

The automated sales & inventory sync detected that your Vinted authentication session has expired or requires security verification.

* **Alert Timestamp:** `{timestamp}`
* **Error Detail:** `{error_message or 'Session storage state expired or redirected to login'}`

---

### 🛠️ How to Reconnect (Takes ~1 minute):

1. Open your terminal on your Mac:
   ```bash
   cd /Users/nicol/Desktop/vinted_tracking
   source venv/bin/activate
   python -m src.cli login
   ```
2. Log in through the opened browser window and press **[ENTER]**.
3. Upload your renewed session tokens to GitHub Secrets:
   ```bash
   python -m src.cli upload-session
   ```
4. **Done!** This issue will automatically close on your next successful sync.
"""

    if token:
        try:
            url = f"https://api.github.com/repos/{repo}/issues"
            payload = {
                "title": AUTH_ISSUE_TITLE,
                "body": issue_body,
                "labels": [AUTH_ISSUE_LABEL],
            }
            data = json.dumps(payload).encode()
            req = urllib.request.Request(url, data=data, headers=_get_github_headers(token))
            with urllib.request.urlopen(req, timeout=10) as resp:
                created = json.loads(resp.read().decode())
                console.print(f"[bold red]📢 Created GitHub Issue #{created.get('number')} to notify owner via email and mobile push.[/bold red]")
                return True
        except Exception as e:
            console.print(f"[dim]GitHub API issue creation error: {e}[/dim]")

    # Fallback to local gh CLI
    try:
        cmd = [
            "gh", "issue", "create",
            "--repo", repo,
            "--title", AUTH_ISSUE_TITLE,
            "--body", issue_body,
            "--label", AUTH_ISSUE_LABEL,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            console.print(f"[bold red]📢 Created GitHub Issue via gh CLI: {result.stdout.strip()}[/bold red]")
            return True
    except Exception as e:
        console.print(f"[dim]gh CLI issue creation error: {e}[/dim]")

    return False


def resolve_github_auth_issue() -> bool:
    """Closes any open authentication issue on GitHub once session is renewed."""
    repo = os.environ.get("GITHUB_REPOSITORY", GITHUB_REPO_DEFAULT)
    token = os.environ.get("GITHUB_TOKEN")

    existing_issue_num = find_open_github_auth_issue(repo, token)
    if not existing_issue_num:
        return False

    comment_body = "✅ **Vinted authentication session successfully refreshed and verified!** Automated sync resumed."

    if token:
        try:
            # Post comment
            url_c = f"https://api.github.com/repos/{repo}/issues/{existing_issue_num}/comments"
            req_c = urllib.request.Request(url_c, data=json.dumps({"body": comment_body}).encode(), headers=_get_github_headers(token))
            urllib.request.urlopen(req_c, timeout=10)

            # Close issue
            url_i = f"https://api.github.com/repos/{repo}/issues/{existing_issue_num}"
            req_i = urllib.request.Request(url_i, data=json.dumps({"state": "closed"}).encode(), headers=_get_github_headers(token))
            req_i.get_method = lambda: "PATCH"
            urllib.request.urlopen(req_i, timeout=10)

            console.print(f"[bold green]✔ Automatically closed resolved GitHub Issue #{existing_issue_num}.[/bold green]")
            return True
        except Exception:
            pass

    try:
        subprocess.run(["gh", "issue", "comment", str(existing_issue_num), "--repo", repo, "--body", comment_body], check=False)
        subprocess.run(["gh", "issue", "close", str(existing_issue_num), "--repo", repo], check=False)
        console.print(f"[bold green]✔ Automatically closed resolved GitHub Issue #{existing_issue_num} via gh CLI.[/bold green]")
        return True
    except Exception:
        return False


def notify_auth_failure(error_message: str = "") -> None:
    """Dispatches authentication failure alerts to both Google Sheet banner and GitHub Issue."""
    console.print(f"[bold red]🚨 Triggering authentication alert: {error_message}[/bold red]")
    update_sheet_auth_status(is_valid=False, error_message=error_message)
    create_or_update_github_issue(error_message=error_message)


def notify_auth_success() -> None:
    """Marks Google Sheet status healthy and closes any open GitHub auth issues."""
    update_sheet_auth_status(is_valid=True)
    resolve_github_auth_issue()
