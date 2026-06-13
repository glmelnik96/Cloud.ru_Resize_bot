"""Banner scenario renderer (M4 /banner pipeline).

Framework-agnostic core that the /banner wizard + graph call to turn one
hero + a handful of slot texts into every format of a scenario. No LLM, no
persona/eval machinery — that is the whole point of /banner vs /new.

Pipeline per scenario:
  1. load the banner manifest (config/banner_templates.json),
  2. for each format slug: apply the chosen variant, compose the canvas
     (static plate + hero_cutout + slot texts) via infra.composer,
  3. save each PNG, return [{format, variant, path}, ...],
  4. optionally bundle everything into one ZIP.

Compose is CPU-bound PIL work, so formats render in worker threads and the
event loop stays responsive (same approach as fill_templates_per_format).
The hero is passed as bytes so each thread opens its own PIL.Image — no
Image object is shared across threads.
"""

from __future__ import annotations

import asyncio
import zipfile
from datetime import datetime
from pathlib import Path

import structlog

from infra.composer import compose
from infra.template_manifest import (
    ScenarioSpec,
    TemplateManifest,
    VariantSpec,
    apply_variant,
    load_manifest,
)

log = structlog.get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BANNER_MANIFEST_PATH = _REPO_ROOT / "config" / "banner_templates.json"


def load_banner_manifest(path: Path | None = None) -> TemplateManifest:
    return load_manifest(path or _BANNER_MANIFEST_PATH)


def _with_derived_slots(texts: dict[str, str]) -> dict[str, str]:
    """Compute composite slots the wizard doesn't collect directly.

    ``datetime`` = "<date> в <time>" — webinar formats are inconsistent: some
    bake date and time into one line (story), others split them. The wizard
    collects ``date`` + ``time`` once; formats pick whichever slot they need.
    A caller-supplied datetime wins over the derived one.
    """
    out = dict(texts)
    if "datetime" not in out:
        date = out.get("date", "").strip()
        time = out.get("time", "").strip()
        if date and time:
            out["datetime"] = f"{date} в {time}"
        elif date:
            out["datetime"] = date
        elif time:
            out["datetime"] = time
    # ``speaker`` = "<name>\n<role>" — SMM speaker/announce covers print the
    # speaker's name and role as one stacked block.
    if "speaker" not in out:
        name = out.get("speaker_name", "").strip()
        role = out.get("speaker_role", "").strip()
        if name and role:
            out["speaker"] = f"{name}\n{role}"
        elif name:
            out["speaker"] = name
        elif role:
            out["speaker"] = role
    return out


def _variants_to_render(scenario: ScenarioSpec) -> list[VariantSpec | None]:
    """A scenario with no variants renders once (None = base spec); a scenario
    with variants renders every variant (all go into the ZIP/message per the
    M4 decision 'render all variants')."""
    return list(scenario.variants) if scenario.variants else [None]


async def render_scenario(
    *,
    scenario_id: str,
    hero: bytes | Path | None,
    texts: dict[str, str],
    out_dir: Path,
    session_id: str = "nosession",
    manifest: TemplateManifest | None = None,
    assets_root: Path = _REPO_ROOT,
    formats: list[str] | None = None,
) -> list[dict]:
    """Render every (format × variant) of a scenario.

    Args:
        scenario_id: key into manifest.scenarios.
        hero: hero image bytes/path, or None for hero-less scenarios
            (text-only TG covers). Cutout/background removal is expected to
            have happened already upstream (Phygital removebg).
        texts: slot -> string (title/date/speaker_*). Missing slots render
            empty; extra keys are ignored.
        out_dir: directory for the PNGs (created if missing).
        session_id: used in filenames + logs.
        manifest: pre-loaded manifest (defaults to the banner manifest).
        assets_root: project root for resolving plate ImageLayer.path.
        formats: optional subset of the scenario's formats to render (e.g. a
            single chosen SMM archetype). Must be a subset of scenario.formats;
            None renders all of them.

    Returns:
        [{format, variant, path}, ...] — one entry per successfully rendered
        (format, variant). Per-format isolation: a single failure is logged
        and skipped, never aborts the batch.
    """
    manifest = manifest or load_banner_manifest()
    scenario = manifest.scenarios.get(scenario_id)
    if scenario is None:
        raise KeyError(f"unknown scenario {scenario_id!r}")

    if formats is None:
        render_formats = list(scenario.formats)
    else:
        unknown = [f for f in formats if f not in scenario.formats]
        if unknown:
            raise ValueError(f"formats not in scenario {scenario_id!r}: {unknown}")
        render_formats = list(formats)

    texts = _with_derived_slots(texts)

    hero_bytes: bytes | None
    if hero is None:
        hero_bytes = None
    elif isinstance(hero, Path):
        hero_bytes = hero.read_bytes()
    else:
        hero_bytes = hero

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    variants = _variants_to_render(scenario)

    jobs: list[tuple[str, str]] = []  # (format, variant_id)
    coros = []
    for fmt in render_formats:
        base_spec = manifest.templates[fmt]
        for variant in variants:
            spec = apply_variant(base_spec, variant) if variant else base_spec
            variant_id = variant.id if variant else "base"
            jobs.append((fmt, variant_id))
            coros.append(
                asyncio.to_thread(
                    _render_one_sync,
                    spec,
                    fmt,
                    variant_id,
                    hero_bytes,
                    texts,
                    out_dir,
                    session_id,
                    ts,
                    assets_root,
                )
            )

    start = datetime.utcnow()
    results = await asyncio.gather(*coros)
    out = [r for r in results if r is not None]
    log.info(
        "banner_render_done",
        session_id=session_id,
        scenario=scenario_id,
        n_total=len(out),
        n_requested=len(jobs),
        latency_ms=int((datetime.utcnow() - start).total_seconds() * 1000),
    )
    return out


def _render_one_sync(
    spec,
    fmt: str,
    variant_id: str,
    hero_bytes: bytes | None,
    texts: dict[str, str],
    out_dir: Path,
    session_id: str,
    ts: str,
    assets_root: Path,
) -> dict | None:
    """Compose + save one (format, variant). Runs in a worker thread."""
    fmt_start = datetime.utcnow()
    try:
        canvas = compose(
            spec,
            hero=hero_bytes,
            texts=texts,
            assets_root=assets_root,
            slug=fmt,
        )
        suffix = "" if variant_id == "base" else f"_{variant_id}"
        path = out_dir / f"{session_id}_{fmt}{suffix}_{ts}.png"
        canvas.save(path, format="PNG", optimize=True)
        log.info(
            "banner_format_ok",
            session_id=session_id,
            slug=fmt,
            variant=variant_id,
            latency_ms=int((datetime.utcnow() - fmt_start).total_seconds() * 1000),
        )
        return {"format": fmt, "variant": variant_id, "path": str(path)}
    except Exception as exc:  # noqa: BLE001 — single-format isolation
        log.error(
            "banner_format_error",
            session_id=session_id,
            slug=fmt,
            variant=variant_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


async def build_zip(files: list[dict], zip_path: Path) -> Path:
    """Bundle rendered PNGs into one ZIP. Archive names are
    '<format>[_<variant>].png' so duplicates across variants stay distinct."""
    if not files:
        raise ValueError("build_zip: no files to archive")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_build_zip_sync, zip_path, files)
    log.info("banner_zip_ok", zip_path=str(zip_path), n_files=len(files))
    return zip_path


def _build_zip_sync(zip_path: Path, files: list[dict]) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in files:
            src = Path(entry["path"])
            variant = entry.get("variant", "base")
            suffix = "" if variant == "base" else f"_{variant}"
            zf.write(src, arcname=f"{entry['format']}{suffix}{src.suffix}")
