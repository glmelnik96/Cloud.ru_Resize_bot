"""Unit tests for provenance.json (блок 4, этап 1 «Язык»).

Каждый ZIP несёт паспорт происхождения: версии промптов, конфигурацию
агентов (модель + объявленная fallback-цепочка), версию манифеста шаблонов,
источник и sha256 hero-ассета, выбранные карточки с оффер-полями и
lint_flags, метафорные обоснования (metaphor_meta). Provenance — best-effort:
его сбой никогда не роняет сборку ZIP.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from PIL import Image

from graph.provenance import build_provenance
from infra.template_manifest import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROMPT_NAMES = {p.stem for p in (REPO_ROOT / "prompts").glob("*.md")}
_AGENT_IDS = {p.stem for p in (REPO_ROOT / "agents").glob("*.yaml")}


def _hero_file(tmp_path: Path, name: str = "hero.png") -> Path:
    path = tmp_path / name
    Image.new("RGBA", (8, 8), (10, 20, 30, 255)).save(path)
    return path


def _state(**over) -> dict:
    base = {
        "session_id": "s-prov",
        "ranked": [
            {
                "id": "c1",
                "slogan": "Понятный слоган",
                "body": "внутренняя заметка",
                "cta": "Попробовать",
                "hook_angle": "rational",
                "anchor": "боль: очередь на GPU",
                "desired_outcome": "обучение стартует через минуты",
                "score": 5.0,
                "reason": "узнаю себя",
                "lint_flags": ["штамп: «уникальный»"],
            }
        ],
        "metaphor_meta": [
            {
                "candidate_id": "c1",
                "metaphor": "открытая дверь",
                "rationale": "путь без очереди",
                "intended_inference": "я захожу сразу",
                "anti_reading": "дверь как выход — уместно",
            }
        ],
        "generated_heroes": [{"index": 0, "local_path": "x", "style": "photo"}],
    }
    base.update(over)
    return base


def _rendered(tmp_path: Path) -> list[dict]:
    hero = _hero_file(tmp_path)
    return [
        {
            "format": "01_photo",
            "path": str(tmp_path / "banner.png"),
            "layers": {"hero": str(hero)},
        }
    ]


def test_core_fields(tmp_path):
    prov = build_provenance(_state(), rendered_files=_rendered(tmp_path))
    assert prov["provenance_version"] == "0.1.0"
    assert prov["session_id"] == "s-prov"
    assert prov["template_manifest_version"] == load_manifest(
        REPO_ROOT / "config" / "templates.json"
    ).version
    # все реальные промпты и агенты сняты как снапшот, без выдуманных версий
    assert set(prov["prompts"]) == _PROMPT_NAMES
    assert all(v != "0.0.0" for v in prov["prompts"].values())
    assert set(prov["agents"]) == _AGENT_IDS
    for a in prov["agents"].values():
        assert a["model"]
        assert isinstance(a["fallback_chain"], list)


def test_candidates_carry_offer_fields_and_flags(tmp_path):
    prov = build_provenance(_state(), rendered_files=_rendered(tmp_path))
    (c,) = prov["candidates"]
    assert c["id"] == "c1"
    assert c["anchor"] == "боль: очередь на GPU"
    assert c["desired_outcome"] == "обучение стартует через минуты"
    assert c["lint_flags"] == ["штамп: «уникальный»"]


def test_metaphor_meta_passthrough(tmp_path):
    prov = build_provenance(_state(), rendered_files=_rendered(tmp_path))
    assert prov["metaphor_meta"][0]["candidate_id"] == "c1"
    assert prov["metaphor_meta"][0]["anti_reading"] == "дверь как выход — уместно"


def test_hero_hash_per_file(tmp_path):
    rendered = _rendered(tmp_path)
    prov = build_provenance(_state(), rendered_files=rendered)
    (f,) = prov["files"]
    assert f["format"] == "01_photo"
    hero_bytes = Path(rendered[0]["layers"]["hero"]).read_bytes()
    assert f["hero_sha256"] == hashlib.sha256(hero_bytes).hexdigest()


def test_hero_source_generated_vs_uploaded(tmp_path):
    rendered = _rendered(tmp_path)
    prov = build_provenance(_state(), rendered_files=rendered)
    assert prov["hero_source"] == "generated"
    st = _state(generated_heroes=[], image={"local_path": "x", "style": "photo"})
    prov2 = build_provenance(st, rendered_files=rendered)
    assert prov2["hero_source"] == "uploaded"


# ----- render_all packs provenance.json into the ZIP --------------------------


async def test_zip_contains_provenance(tmp_path, monkeypatch):
    from graph.nodes import render_all as mod

    monkeypatch.setattr(mod, "_ZIP_DIR", tmp_path / "zips")
    banner = tmp_path / "b.png"
    Image.new("RGB", (4, 4)).save(banner)
    hero = _hero_file(tmp_path)
    state = _state(
        rendered_files=[
            {
                "format": "01_photo",
                "path": str(banner),
                "layers": {"hero": str(hero)},
            }
        ]
    )
    out = await mod.render_all(state)
    with zipfile.ZipFile(out["rendered_zip_path"]) as zf:
        prov = json.loads(zf.read("provenance.json").decode("utf-8"))
    assert prov["session_id"] == "s-prov"
    assert prov["files"][0]["hero_sha256"] == hashlib.sha256(
        hero.read_bytes()
    ).hexdigest()


async def test_provenance_failure_is_fail_open(tmp_path, monkeypatch):
    """Паспорт — best-effort: его сбой не роняет сборку ZIP."""
    from graph.nodes import render_all as mod

    monkeypatch.setattr(mod, "_ZIP_DIR", tmp_path / "zips")

    def boom(state, *, rendered_files):
        raise RuntimeError("provenance broke")

    monkeypatch.setattr(mod, "build_provenance", boom)
    banner = tmp_path / "b.png"
    Image.new("RGB", (4, 4)).save(banner)
    state = _state(
        rendered_files=[{"format": "01_photo", "path": str(banner), "layers": {}}]
    )
    out = await mod.render_all(state)
    with zipfile.ZipFile(out["rendered_zip_path"]) as zf:
        assert "provenance.json" not in zf.namelist()
        assert "01_photo.png" in zf.namelist()
