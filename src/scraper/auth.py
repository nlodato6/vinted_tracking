"""Authentication and session management for Vinted using Playwright."""

import sys
from pathlib import Path
from rich.console import Console
from playwright.async_api import BrowserContext, Page
from src.config import settings
from src.scraper.browser import get_browser_context, human_delay

console = Console()



def parse_and_save_cookies(raw_text: str, target_domain: str = "vinted.com") -> Path:
    """
    Parses cookies from JSON (Cookie-Editor / Playwright format) or
    raw Cookie header string and writes standard Playwright storage_state JSON.
    """
    import json

    raw_text = raw_text.strip()
    cookies = []

    # 1. Try parsing JSON (Cookie-Editor export or Playwright storage state)
    try:
        data = json.loads(raw_text)
        if isinstance(data, dict) and "cookies" in data:
            cookies = data["cookies"]
        elif isinstance(data, list):
            cookies = data
    except json.JSONDecodeError:
        # 2. Try parsing raw header format: "key1=val1; key2=val2"
        pairs = [p.strip() for p in raw_text.split(";") if "=" in p]
        for pair in pairs:
            name, _, value = pair.partition("=")
            name = name.strip()
            value = value.strip()
            if name:
                cookies.append({
                    "name": name,
                    "value": value,
                    "domain": f".{target_domain}",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "Lax",
                })

    if not cookies:
        raise ValueError("Could not parse any valid cookies from input.")

    # Normalize cookie objects for Playwright
    normalized_cookies = []
    for c in cookies:
        if not isinstance(c, dict) or "name" not in c or "value" not in c:
            continue

        # Playwright strictly requires SameSite to be "Strict", "Lax", or "None"
        raw_same_site = str(c.get("sameSite") or "").lower()
        if raw_same_site in ["strict"]:
            same_site = "Strict"
        elif raw_same_site in ["none", "no_restriction"]:
            same_site = "None"
        else:
            same_site = "Lax"

        # Expiration handling for both standard "expires" and Cookie-Editor "expirationDate"
        exp_val = c.get("expires", c.get("expirationDate", -1))
        try:
            expires = float(exp_val) if exp_val is not None else -1.0
        except (ValueError, TypeError):
            expires = -1.0

        domain = str(c.get("domain") or f".{target_domain}")
        if not domain.startswith(".") and not domain.startswith("www.") and "localhost" not in domain:
            domain = f".{domain}"

        secure = bool(c.get("secure", False))
        if same_site == "None":
            secure = True

        normalized_cookies.append({
            "name": str(c["name"]),
            "value": str(c["value"]),
            "domain": domain,
            "path": str(c.get("path", "/")),
            "expires": expires,
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": secure,
            "sameSite": same_site,
        })

    storage_state = {
        "cookies": normalized_cookies,
        "origins": [
            {
                "origin": f"https://www.{target_domain}",
                "localStorage": [],
            }
        ],
    }

    target_path = settings.full_storage_state_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(storage_state, f, indent=2)

    return target_path


async def interactive_login():
    """
    Launches an interactive browser session using real Google Chrome with stealth.
    Once logged in, cookies and tokens are persisted to disk.
    """
    console.print("\n[bold cyan]🚀 Launching Google Chrome for Vinted login...[/bold cyan]")
    console.print(f"[dim]Navigating to {settings.vinted_base_url}[/dim]")

    context = await get_browser_context(headless=False, use_storage_state=False)
    page: Page = await context.new_page()

    try:
        await page.goto(settings.vinted_base_url, wait_until="domcontentloaded")
        await human_delay(1.5, 2.5)

        console.print("\n[bold yellow]👉 INSTRUCTIONS & LOGIN TIPS:[/bold yellow]")
        console.print("1. In the opened Google Chrome window, log in to your Vinted account.")
        console.print("   [dim]• If prompted, complete any CAPTCHA or 2FA challenge.[/dim]")
        console.print("   [dim]• If you use Google login, Google may restrict automated browsers ('This browser or app may not be secure').[/dim]")
        console.print("     [dim]If that happens, use Email & Password, Apple ID, or import cookies directly via 'python -m src.cli import-cookie'.[/dim]")
        console.print("2. Ensure your profile avatar / account is visible.")
        console.print("\n[bold green]Once you are fully logged in, press [ENTER] in this terminal to save your session...[/bold green]")

        # Wait for user input in terminal
        await page.wait_for_timeout(1000)
        input()

        # Save cookies and local storage tokens
        target_path = settings.full_storage_state_path
        await context.storage_state(path=str(target_path))
        console.print(f"[bold green]✅ Session successfully saved to {target_path}![/bold green]")
        console.print("[dim]Subsequent background runs will now execute headlessly using this authenticated session.[/dim]\n")
        notify_auth_success()

    except Exception as e:
        console.print(f"[bold red]❌ Error during interactive login: {e}[/bold red]")
        raise
    finally:
        await context.close()


async def verify_session() -> bool:
    """
    Checks if saved storage state exists and is still valid on Vinted.
    """
    from src.notifications import notify_auth_failure, notify_auth_success

    if not settings.full_storage_state_path.exists():
        console.print("[yellow]⚠️ No saved session found. Please run 'python -m src.cli login' or 'import-cookie' first.[/yellow]")
        notify_auth_failure("No saved Vinted session state file found (.session/vinted_state.json)")
        return False

    context = await get_browser_context(headless=True, use_storage_state=True)
    page: Page = await context.new_page()

    try:
        await page.goto(f"{settings.vinted_base_url}", wait_until="domcontentloaded")
        await human_delay(1.5, 2.5)

        current_url = page.url
        if "login" in current_url or "auth" in current_url or "signup" in current_url:
            console.print("[red]❌ Saved session has expired or is invalid.[/red]")
            notify_auth_failure("Vinted session redirected to login/signup (session expired)")
            return False

        # Check for authenticated indicators (e.g. Inbox, User Profile Menu)
        inbox_elem = await page.query_selector("a[href='/inbox'], a[href*='/inbox']")
        user_menu = await page.query_selector("[data-testid*='user-menu'], [data-testid*='profile'], a[href*='/member/']")

        if not inbox_elem and not user_menu:
            console.print("[red]❌ Session file does not contain active login credentials.[/red]")
            notify_auth_failure("Session cookies present but user is not logged in")
            return False

        console.print("[green]✅ Authenticated Vinted session is active and valid.[/green]")
        notify_auth_success()
        return True
    except Exception as e:
        console.print(f"[red]❌ Session verification failed: {e}[/red]")
        notify_auth_failure(f"Session verification exception: {e}")
        return False
    finally:
        await context.close()
