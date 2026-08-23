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
_STATIC = Path(__file__).resolve().parent.parent / "static"


def _asset_version() -> str:
    """Метка кэша, посчитанная по самим файлам статики.

    Раньше это была строка «20260823v1», прописанная в трёх шаблонах девять
    раз руками. Такую метку забывают: правка CSS без правки метки — это выкатка,
    после которой вернувшийся браузер продолжает показывать старый экран, и
    отличить это от «не задеплоилось» нельзя ничем. Здесь метка не может
    разойтись ни с файлами, ни между страницами: она одна и считается из
    времени последней правки статики."""
    stamps = [p.stat().st_mtime_ns for p in _STATIC.glob("*.*")]
    return format(max(stamps) if stamps else 0, "x")


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
        context={"email": user.email, "prefix": prefix,
                 "retention_hours": retention_hours, "v": _asset_version()},
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
        context={"email": user.email, "prefix": prefix,
                 "retention_hours": retention_hours, "v": _asset_version()},
    )


@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request):
    """Библиотека знаний: карточки продуктов, история версий, роли."""
    user = await get_current_user(request)
    cfg = getattr(request.app.state, "settings", {}) or {}
    prefix = cfg.get("prefix", "/creatives")
    return _TEMPLATES.TemplateResponse(
        request=request,
        name="library.html",
        context={"email": user.email, "prefix": prefix, "v": _asset_version()},
    )
