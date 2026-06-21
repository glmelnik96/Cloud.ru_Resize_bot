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

# Prefix the gateway mounts this sub-app under (for asset/link URLs in the page).
_PREFIX = "/creatives"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = await get_current_user(request)
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="creatives.html",
        context={"email": user.email, "prefix": _PREFIX},
    )
