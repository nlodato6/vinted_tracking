"""FastAPI webhook and automation server for instant Vinted alerts."""

import re
import asyncio
from typing import Optional, Dict, Any
from fastapi import FastAPI, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel
from rich.console import Console

from src.config import settings
from src.sheets import GoogleSheetsClient, SheetsSyncEngine

console = Console()
app = FastAPI(title="Vinted Automation Webhook Server", version="0.1.0")


class SyncRequest(BaseModel):
    sync_type: str = "lots"  # lots or supplies


class EmailWebhookPayload(BaseModel):
    subject: Optional[str] = ""
    body: Optional[str] = ""
    sender: Optional[str] = ""
    recipient: Optional[str] = ""


async def run_sync_task(sync_type: str):
    """Background task to synchronize lots in Google Sheets."""
    console.print(f"[bold cyan]⚡ Running background sync task: '{sync_type}'...[/bold cyan]")
    try:
        client = GoogleSheetsClient()
        engine = SheetsSyncEngine(client)
        lot_res = engine.sync_lots()
        console.print(f"[green]Lots sync complete: {lot_res}[/green]")
    except Exception as e:
        console.print(f"[bold red]❌ Background sync error: {e}[/bold red]")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "online",
        "domain": settings.vinted_domain,
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
    Directly updates Google Sheets without web scraping.
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
        body = data.get("body", "") or data.get("text", "") or data.get("html", "")
    else:
        form = await request.form()
        subject = str(form.get("subject", ""))
        body = str(form.get("text", "") or form.get("html", "") or form.get("body-plain", ""))

    console.print(f"[bold magenta]📩 Inbound email webhook received:[/bold magenta] '{subject}'")

    from src.email_parser import parse_vinted_email
    event = parse_vinted_email(subject, body)

    if event.event_type in ("sale", "favorite", "offer"):
        console.print(f"[bold green]🎯 {event.event_type.upper()} event detected! Updating Google Sheets directly...[/bold green]")
        client = GoogleSheetsClient()
        engine = SheetsSyncEngine(client)
        result = engine.process_email_event(event)
        console.print(f"[green]✔ Sheet update complete: {result}[/green]")
        return {
            "status": "success",
            "event": "sale_detected" if event.event_type == "sale" else f"{event.event_type}_detected",
            "action": "sync_queued",
            "event_type": event.event_type,
            "details": result,
        }

    return {"status": "ignored", "event": "non_sale_email", "message": "Email did not match sale or favorite patterns"}


