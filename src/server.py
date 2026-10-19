"""FastAPI webhook and automation server for instant Vinted alerts."""

import re
import asyncio
from typing import Optional, Dict, Any
from fastapi import FastAPI, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel
from rich.console import Console

from src.config import settings
from src.sheets import GoogleSheetsClient, SheetsSyncEngine
from src.scraper import scrape_closet_listings, scrape_vinted_orders, verify_session

console = Console()
app = FastAPI(title="Vinted Automation Webhook Server", version="0.1.0")


class SyncRequest(BaseModel):
    sync_type: str = "all"  # listings, sales, or all


class EmailWebhookPayload(BaseModel):
    subject: Optional[str] = ""
    body: Optional[str] = ""
    sender: Optional[str] = ""
    recipient: Optional[str] = ""


async def run_sync_task(sync_type: str):
    """Background task to run scrapers and update Google Sheets."""
    console.print(f"[bold cyan]⚡ Running background sync task: '{sync_type}'...[/bold cyan]")
    try:
        client = GoogleSheetsClient()
        engine = SheetsSyncEngine(client)

        if sync_type in ("listings", "all"):
            listings = await scrape_closet_listings()
            res = engine.sync_listings(listings)
            console.print(f"[green]Listings sync complete: {res}[/green]")

        if sync_type in ("sales", "all"):
            orders = await scrape_vinted_orders()
            res = engine.sync_orders(orders)
            console.print(f"[green]Orders sync complete: {res}[/green]")

        if sync_type in ("lots", "all"):
            lot_res = engine.sync_lots()
            console.print(f"[green]Lots sync complete: {lot_res}[/green]")

    except Exception as e:
        console.print(f"[bold red]❌ Background sync error: {e}[/bold red]")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    session_valid = await verify_session()
    return {
        "status": "online",
        "domain": settings.vinted_domain,
        "vinted_session_active": session_valid,
        "sheet_id": settings.google_sheet_id or "auto-detected",
    }


@app.post("/sync")
async def trigger_sync(payload: SyncRequest, background_tasks: BackgroundTasks):
    """Manual sync trigger via HTTP API."""
    if payload.sync_type not in ("listings", "sales", "lots", "all"):
        raise HTTPException(status_code=400, detail="sync_type must be 'listings', 'sales', 'lots', or 'all'")

    background_tasks.add_task(run_sync_task, payload.sync_type)
    return {
        "status": "queued",
        "sync_type": payload.sync_type,
        "message": f"Sync job '{payload.sync_type}' queued successfully.",
    }


@app.post("/webhook/vinted-email")
async def receive_vinted_email(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_secret: Optional[str] = Header(None),
):
    """
    Inbound webhook endpoint for Vinted email alerts (e.g. from SendGrid Inbound Parse,
    Mailgun, Cloudflare Email Routing, or Zapier/Make).
    """
    if settings.webhook_secret and settings.webhook_secret != "your_optional_webhook_secret_here":
        if x_webhook_secret != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    # Read payload (either JSON or Form Data)
    content_type = request.headers.get("content-type", "")
    subject = ""
    body = ""

    if "application/json" in content_type:
        data = await request.json()
        subject = data.get("subject", "")
        body = data.get("body", "") or data.get("text", "")
    else:
        form = await request.form()
        subject = str(form.get("subject", ""))
        body = str(form.get("text", "") or form.get("html", "") or form.get("body-plain", ""))

    console.print(f"[bold magenta]📩 Inbound email webhook received:[/bold magenta] '{subject}'")

    # Detect sale or order confirmation patterns
    is_sale_event = bool(
        re.search(r"(you'?ve made a sale|item sold|order confirmed|buyer bought)", subject, re.IGNORECASE)
        or re.search(r"(congratulations on your sale|has been purchased)", body, re.IGNORECASE)
    )

    if is_sale_event:
        console.print("[bold green]🎯 Sale alert detected! Queuing instant order sync...[/bold green]")
        background_tasks.add_task(run_sync_task, "all")
        return {"status": "accepted", "event": "sale_detected", "action": "sync_queued"}

    return {"status": "ignored", "event": "non_sale_email", "message": "Email did not match sale triggers"}
