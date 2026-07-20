"""HTML page route: the /creatives sub-app's own page + canon header.

The gateway strips the /creatives prefix, so this serves at "/" internally.
The page is self-contained (canon topbar with is-active on "Креативы",
links absolute to the gateway prefixes /images /slides /creatives). The
APP_PREFIX is passed to the template so static/asset URLs resolve through
the gateway.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth.deps import get_current_user

router = APIRouter(tags=["pages"])

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = await get_current_user(request)
    # Prefix the gateway mounts this sub-app under (asset/API URLs in the page).
    # Default "/creatives"; "" for a local run with no gateway.
    cfg = getattr(request.app.state, "settings", {}) or {}
    prefix = cfg.get("prefix", "/creatives")
    retention_hours = int(cfg.get("retention_ttl_sec", 24 * 3600)) // 3600
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="creatives.html",
        context={"email": user.email, "prefix": prefix, "retention_hours": retention_hours},
    )


@router.get("/webinar", response_class=HTMLResponse)
async def webinar_page(request: Request):
    """Webinar resizes: form + manual canvas fit engine (no LLM/HITL)."""
    user = await get_current_user(request)
    cfg = getattr(request.app.state, "settings", {}) or {}
    prefix = cfg.get("prefix", "/creatives")
    retention_hours = int(cfg.get("retention_ttl_sec", 24 * 3600)) // 3600
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="webinar.html",
        context={"email": user.email, "prefix": prefix, "retention_hours": retention_hours},
    )
