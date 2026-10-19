"""Authentication and session management for Vinted using Playwright."""

import sys
from pathlib import Path
from rich.console import Console
from playwright.async_api import BrowserContext, Page
from src.config import settings
from src.scraper.browser import get_browser_context, human_delay

console = Console()


async def interactive_login():
    """
    Launches an interactive (headed) browser session for the user to log in
    to Vinted. Once logged in, cookies and tokens are persisted to disk.
    """
    console.print("\n[bold cyan]🚀 Launching interactive browser for Vinted login...[/bold cyan]")
    console.print(f"[dim]Navigating to {settings.vinted_base_url}[/dim]")

    context = await get_browser_context(headless=False, use_storage_state=False)
    page: Page = await context.new_page()

    try:
        await page.goto(settings.vinted_base_url, wait_until="domcontentloaded")
        await human_delay(1.5, 2.5)

        console.print("\n[bold yellow]👉 INSTRUCTIONS:[/bold yellow]")
        console.print("1. In the opened browser window, log in to your Vinted account.")
        console.print("2. Complete any 2FA or CAPTCHA security challenges if prompted.")
        console.print("3. Ensure your profile avatar / account is visible.")
        console.print("\n[bold green]Once you are fully logged in, press [ENTER] in this terminal to save your session...[/bold green]")

        # Wait for user input in terminal
        await page.wait_for_timeout(1000)
        input()

        # Save cookies and local storage tokens
        target_path = settings.full_storage_state_path
        await context.storage_state(path=str(target_path))
        console.print(f"[bold green]✅ Session successfully saved to {target_path}![/bold green]")
        console.print("[dim]Subsequent background runs will now execute headlessly using this authenticated session.[/dim]\n")

    except Exception as e:
        console.print(f"[bold red]❌ Error during interactive login: {e}[/bold red]")
        raise
    finally:
        await context.close()


async def verify_session() -> bool:
    """
    Checks if saved storage state exists and is still valid on Vinted.
    """
    if not settings.full_storage_state_path.exists():
        console.print("[yellow]⚠️ No saved session found. Please run 'python -m src.cli login' first.[/yellow]")
        return False

    context = await get_browser_context(headless=True, use_storage_state=True)
    page: Page = await context.new_page()

    try:
        await page.goto(f"{settings.vinted_base_url}/member/items", wait_until="domcontentloaded")
        await human_delay(1.5, 2.0)

        current_url = page.url
        if "login" in current_url or "auth" in current_url:
            console.print("[red]❌ Saved session has expired or is invalid.[/red]")
            return False

        console.print("[green]✅ Authenticated Vinted session is active and valid.[/green]")
        return True
    except Exception as e:
        console.print(f"[red]❌ Session verification failed: {e}[/red]")
        return False
    finally:
        await context.close()
