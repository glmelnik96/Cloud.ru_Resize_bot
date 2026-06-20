"""Hero-image generation channel for App3.

Replaces the Telegram bot-to-bot Phygital path (bot/graph_runner b2b) with a
pluggable web generator. The orchestrator's "generate" decision calls a
``HeroGenerator``; the default is a Null generator that is unavailable (the UI
then offers only manual upload). The real Phygital adapter is a thin wrapper
activated on the VM once the Phygital client package + session.json are present.

Dependency injection mirrors App1's GenerationService(runners=...): tests pass
a fake generator, production wires the Phygital one.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class HeroGenUnavailable(Exception):
    """Raised when generation is requested but no generator is configured."""


class HeroGenerator(Protocol):
    available: bool

    async def generate(self, *, prompt: str, style: str, dest: Path) -> Path:
        """Generate a hero image for the prompt and write it to ``dest``.
        Returns the path written. Raises on failure (caller falls back to
        manual upload)."""
        ...


class NullHeroGenerator:
    """No generation backend configured. The wizard offers manual upload only."""

    available = False

    async def generate(self, *, prompt: str, style: str, dest: Path) -> Path:
        raise HeroGenUnavailable("hero generation backend not configured")


class PhygitalHeroGenerator:
    """Web Phygital adapter (channel switch target).

    Activated only when the Phygital client package is importable and a
    session file exists. Uses ImageGenWorkflow (text→image) like App1; the
    cutout/cover ratio decision can be threaded through ``style`` later.

    NOTE: requires vendoring the Phygital ``client/`` + ``workflows/`` packages
    onto the VM and a valid session.json (owner account). Until then App3 runs
    with NullHeroGenerator and the manual-upload path.
    """

    available = True

    def __init__(self, session_file: Path, *, ratio: str = "r_3_4", resolution: str = "k2") -> None:
        self.session_file = Path(session_file)
        self.ratio = ratio
        self.resolution = resolution

    async def generate(self, *, prompt: str, style: str, dest: Path) -> Path:
        # Lazy imports: these packages live on the VM deployment, not in the
        # bot repo. Import errors surface as HeroGenUnavailable so the caller
        # falls back to manual upload instead of crashing the task.
        try:
            from app.services.upstream import Upstream  # vendored on VM
            from workflows.image_gen import ImageGenWorkflow
        except Exception as exc:  # noqa: BLE001
            raise HeroGenUnavailable(f"phygital client not installed: {exc}") from exc

        upstream = Upstream(self.session_file)
        client = upstream.client()
        async with client:
            wf = ImageGenWorkflow(
                client, model_name="v3_1", ratio=self.ratio, resolution=self.resolution
            )
            job = await wf.run(prompt=prompt)
        if getattr(job, "status", None) not in {"done", "completed", "success"} or not getattr(
            job, "result_urls", None
        ):
            raise HeroGenUnavailable(getattr(job, "error", None) or "generation failed")

        import httpx

        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.get(job.result_urls[0])
            r.raise_for_status()
            Path(dest).write_bytes(r.content)
        return dest
