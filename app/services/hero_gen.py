"""Hero-image generation channel for App3.

Replaces the Telegram bot-to-bot Phygital path (bot/graph_runner b2b) with a
pluggable web generator behind the ``HeroGenerator`` seam. Per the platform
decision (2026-06-21), App3 does NOT talk to Phygital directly: it delegates
generation to App1 over an internal loopback endpoint, so a single Phygital
account/session lives with the owner (App1) and App3 has no Phygital egress.

Backends:
  - NullHeroGenerator: unavailable → the UI offers manual upload only.
  - App1HeroGenerator: POST the prompt to App1's /internal/hero (loopback),
    receive the rendered hero bytes, write them to dest.

Dependency injection mirrors App1's GenerationService(runners=...): tests pass
a fake generator, production selects one via ``make_hero_generator``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class HeroGenUnavailable(Exception):
    """Raised when generation is requested but no backend is configured/reachable."""


class HeroGenerator(Protocol):
    available: bool

    async def generate(
        self,
        *,
        prompt: str,
        style: str,
        dest: Path,
        user_id: str | None = None,
        email: str | None = None,
    ) -> Path:
        """Generate a hero image for the prompt and write it to ``dest``.
        Returns the path written. Raises on failure (caller falls back to
        manual upload). ``user_id``/``email`` identify the END user so App1 can
        lane hero generation per user (App1 commit 2010463)."""
        ...


class NullHeroGenerator:
    """No generation backend configured. The wizard offers manual upload only."""

    available = False

    async def generate(
        self,
        *,
        prompt: str,
        style: str,
        dest: Path,
        user_id: str | None = None,
        email: str | None = None,
    ) -> Path:
        raise HeroGenUnavailable("hero generation backend not configured")


class App1HeroGenerator:
    """Delegate hero generation to App1 over an internal loopback endpoint.

    Contract: POST ``{url}`` with JSON
    ``{"prompt": str, "style": str, "scenario": str}`` → response is the
    rendered image bytes (image/* body) or a JSON ``{"url": "..."}`` to
    download. ``scenario`` ∈ {render, photo} selects App1's brand pipeline
    (render = isometric brand_t2i render variant + Photoroom bg-removal →
    transparent cutout; photo = full-bleed photo with background). In the
    12-banner flow ``style`` already carries the per-proposition scenario, so
    we forward it as ``scenario`` too. App3 trusts the loopback (127.0.0.1).

    End-user identity is forwarded as ``X-User-Id`` / ``X-User-Email`` headers
    so App1 lanes hero generation per end user (App1 commit 2010463) — without
    them a multi-user burst serialises into one shared lane and hits App1's
    per-lane limit (503 queue full). Omitted when no identity is supplied
    (back-compat: App1 falls back to the shared lane).
    """

    available = True

    def __init__(self, url: str, *, timeout_s: float = 600.0) -> None:
        self.url = url
        self.timeout_s = timeout_s

    async def generate(
        self,
        *,
        prompt: str,
        style: str,
        dest: Path,
        user_id: str | None = None,
        email: str | None = None,
    ) -> Path:
        import httpx

        scenario = style if style in {"render", "photo"} else "photo"
        headers: dict[str, str] = {}
        if user_id:
            headers["X-User-Id"] = str(user_id)
        if email:
            headers["X-User-Email"] = email
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                resp = await c.post(
                    self.url,
                    json={"prompt": prompt, "style": style, "scenario": scenario},
                    headers=headers or None,
                )
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                if ctype.startswith("image/"):
                    data = resp.content
                else:
                    # JSON {"url": ...} → download the result
                    payload = resp.json()
                    img_url = payload.get("url") or payload.get("result_url")
                    if not img_url:
                        raise HeroGenUnavailable("App1 hero response had no image/url")
                    dl = await c.get(img_url)
                    dl.raise_for_status()
                    data = dl.content
        except HeroGenUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — surface as fallback-to-manual
            raise HeroGenUnavailable(f"App1 hero request failed: {exc}") from exc

        Path(dest).write_bytes(data)
        return dest


def make_hero_generator(*, backend: str, app1_hero_url: str) -> HeroGenerator:
    """Select the hero generator by config. Defaults to Null (manual upload)
    until App1's /internal/hero endpoint is confirmed live in COORDINATION."""
    if backend == "app1":
        return App1HeroGenerator(app1_hero_url)
    return NullHeroGenerator()
