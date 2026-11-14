"""Shared template setup, so every page renders the same way."""

from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.db import connect
from app.ops import get_metrics

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


def utc_filter(epoch) -> str:
    """Server-side fallback for times. The browser script replaces it with local time."""
    if epoch is None:
        return ""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%d %b %H:%M UTC")


templates.env.filters["utc"] = utc_filter


def pending_count() -> int:
    conn = connect()
    try:
        return get_metrics(conn)["pending_review"]
    finally:
        conn.close()


def render(
    request: Request, name: str, context: dict | None = None, status_code: int = 200
):
    """Render a page that has the sidebar. Adds the review-queue count to every page."""
    ctx = dict(context or {})
    ctx.setdefault("nav_pending", pending_count())
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
