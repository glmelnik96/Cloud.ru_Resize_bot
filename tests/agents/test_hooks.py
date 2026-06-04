"""Unit tests for graph/hooks/ — pure functions, no LLM.

These tests anchor the brand-guard contract from AGENTS.md §4.
If you change a hook's behavior, update both AGENTS.md and these tests.
"""

from __future__ import annotations

import pytest

from graph.hooks import HOOK_REGISTRY, Violation, run_hooks
from graph.state import CandidateSet, MessageCandidate, PersonaSet, Persona


# ---------- fixtures --------------------------------------------------------


def _candidate(
    id: str = "c1",
    slogan: str = "Спокойная инженерия",
    body: str = "Хранилище, которое работает.",
    cta: str = "Попробовать",
    hook_angle: str = "rational",
) -> MessageCandidate:
    return MessageCandidate(
        id=id, slogan=slogan, body=body, cta=cta, hook_angle=hook_angle
    )


def _set(*items: MessageCandidate) -> CandidateSet:
    """CandidateSet requires min 3 items — pad with clean fillers using unique hooks."""
    fillers = [
        _candidate(id="_pad1", hook_angle="curiosity", cta="Начать сейчас"),
        _candidate(id="_pad2", hook_angle="authority", cta="Начать сейчас"),
        _candidate(id="_pad3", hook_angle="direct_benefit", cta="Начать сейчас"),
    ]
    lst = list(items)
    while len(lst) < 3:
        lst.append(fillers[len(lst) - len(items)])
    return CandidateSet(candidates=lst)


# ---------- ban_emoji -------------------------------------------------------


def test_ban_emoji_clean_passes():
    result = _set(_candidate(), _candidate(id="c2", hook_angle="emotional"))
    spec = {"id": "ban_emoji", "fields": ["slogan", "body", "cta"], "on_violation": "fail"}
    assert HOOK_REGISTRY["ban_emoji"](result, spec) == []


def test_ban_emoji_in_slogan_fails():
    result = _set(_candidate(slogan="Спокойная инженерия 🚀"))
    spec = {"id": "ban_emoji", "fields": ["slogan", "body", "cta"], "on_violation": "fail"}
    viols = HOOK_REGISTRY["ban_emoji"](result, spec)
    assert len(viols) == 1
    assert viols[0].severity == "fail"
    assert viols[0].field == "slogan"


def test_ban_emoji_in_cta_warn_severity_respected():
    result = _set(_candidate(cta="Включить ✓"))  # check-mark in dingbats range
    spec = {"id": "ban_emoji", "fields": ["cta"], "on_violation": "warn"}
    viols = HOOK_REGISTRY["ban_emoji"](result, spec)
    assert len(viols) == 1
    assert viols[0].severity == "warn"


# ---------- text_len --------------------------------------------------------


def test_text_len_max_chars_pass_and_fail():
    short = _candidate(slogan="x" * 60)
    long = _candidate(slogan="x" * 61)
    spec = {"id": "text_len", "field": "slogan", "max": 60, "on_violation": "fail"}
    assert HOOK_REGISTRY["text_len"](_set(short), spec) == []
    fails = HOOK_REGISTRY["text_len"](_set(long), spec)
    assert len(fails) == 1
    assert fails[0].severity == "fail"


def test_text_len_word_band_cta():
    spec = {
        "id": "text_len",
        "field": "cta",
        "min_words": 2,
        "max_words": 5,
        "on_violation": "warn",
    }
    too_short = _candidate(cta="Старт")
    just_right = _candidate(cta="Начать сейчас")
    too_long = _candidate(cta="Начать использовать платформу прямо сегодня бесплатно")
    assert len(HOOK_REGISTRY["text_len"](_set(too_short), spec)) == 1
    assert HOOK_REGISTRY["text_len"](_set(just_right), spec) == []
    assert len(HOOK_REGISTRY["text_len"](_set(too_long), spec)) == 1


# ---------- hook_diversity --------------------------------------------------


def test_hook_diversity_unique_passes():
    result = _set(
        _candidate(id="c1", hook_angle="rational"),
        _candidate(id="c2", hook_angle="emotional"),
        _candidate(id="c3", hook_angle="social_proof"),
    )
    spec = {"id": "hook_diversity", "field": "hook_angle", "on_violation": "warn"}
    assert HOOK_REGISTRY["hook_diversity"](result, spec) == []


def test_hook_diversity_dupes_warn():
    result = _set(
        _candidate(id="c1", hook_angle="rational"),
        _candidate(id="c2", hook_angle="rational"),
        _candidate(id="c3", hook_angle="emotional"),
    )
    spec = {"id": "hook_diversity", "field": "hook_angle", "on_violation": "warn"}
    viols = HOOK_REGISTRY["hook_diversity"](result, spec)
    assert len(viols) == 1
    assert viols[0].severity == "warn"
    assert "rational" in viols[0].message


# ---------- no_exclamation_in_slogan ----------------------------------------


def test_exclamation_in_slogan_warn():
    result = _set(_candidate(slogan="Это работает!"))
    spec = {"id": "no_exclamation_in_slogan", "on_violation": "warn"}
    viols = HOOK_REGISTRY["no_exclamation_in_slogan"](result, spec)
    assert len(viols) == 1
    assert viols[0].severity == "warn"


def test_no_exclamation_in_cta_is_allowed():
    # The hook only checks slogan; CTA with `!` must not trigger.
    result = _set(_candidate(slogan="Это работает", cta="Старт!"))
    spec = {"id": "no_exclamation_in_slogan", "on_violation": "warn"}
    assert HOOK_REGISTRY["no_exclamation_in_slogan"](result, spec) == []


# ---------- brand_stopwords -------------------------------------------------


@pytest.mark.parametrize(
    "text,expected_fail",
    [
        # Group A
        ("epic cinematic shot", True),
        ("Hyperdetailed render", True),
        ("Neon glow background", True),
        # Group B fail
        ("Революционное решение", True),
        ("Инновационная платформа", True),
        ("Передовые технологии", True),
        ("Облачные решения нового поколения", True),
        ("Цифровая трансформация бизнеса", True),
        # Group D
        ("Vintage аналог", True),
        ("Mid-century дизайн", True),
        # clean
        ("Спокойная инженерия для надёжной работы", False),
        ("Хранилище объектов, S3-совместимое", False),
        ("Старт за 3 клика", False),
    ],
)
def test_brand_stopwords_fail_patterns(text: str, expected_fail: bool):
    result = _set(_candidate(slogan=text))
    spec = {
        "id": "brand_stopwords",
        "groups": ["A", "B", "C", "D"],
        "fields": ["slogan"],
        "on_violation": "fail",
    }
    fails = [v for v in HOOK_REGISTRY["brand_stopwords"](result, spec) if v.severity == "fail"]
    assert bool(fails) is expected_fail, f"text={text!r}, viols={fails}"


def test_brand_stopwords_ekosistema_is_warn_not_fail():
    """`экосистем*` is in group B as warn-only — must not block."""
    result = _set(_candidate(body="Облачная экосистема для разработчиков."))
    spec = {
        "id": "brand_stopwords",
        "groups": ["A", "B", "C", "D"],
        "fields": ["body"],
        "on_violation": "fail",
    }
    viols = HOOK_REGISTRY["brand_stopwords"](result, spec)
    fails = [v for v in viols if v.severity == "fail"]
    warns = [v for v in viols if v.severity == "warn"]
    assert fails == []
    assert len(warns) == 1


# ---------- run_hooks dispatcher --------------------------------------------


def test_run_hooks_combines_results():
    result = _set(
        _candidate(slogan="x" * 100, body="Революционное решение"),  # 2 fails
    )
    specs = [
        {"id": "text_len", "field": "slogan", "max": 60, "on_violation": "fail"},
        {
            "id": "brand_stopwords",
            "groups": ["B"],
            "fields": ["body"],
            "on_violation": "fail",
        },
    ]
    viols = run_hooks(result, specs)
    assert len(viols) == 2
    hooks = {v.hook for v in viols}
    assert hooks == {"text_len", "brand_stopwords"}


def test_run_hooks_unknown_id_raises():
    with pytest.raises(KeyError):
        run_hooks(_set(_candidate()), [{"id": "does_not_exist"}])


# ---------- iter_items: wrapper-list vs single ------------------------------


def test_hooks_work_on_personaset_wrapper():
    """Wrapper detection: PersonaSet has `personas` list — hooks should iterate it."""
    pset = PersonaSet(
        personas=[
            Persona(
                segment="A",
                age_range="25-35",
                pain_points=["x"],
                motivations=["y"],
                objections=["z"],
                communication_style="formal",
            ),
            Persona(
                segment="A",  # duplicate segment
                age_range="36-45",
                pain_points=["x"],
                motivations=["y"],
                objections=["z"],
                communication_style="casual",
            ),
        ]
    )
    spec = {"id": "hook_diversity", "field": "segment", "on_violation": "warn"}
    viols = HOOK_REGISTRY["hook_diversity"](pset, spec)
    assert len(viols) == 1
    assert "A" in str(viols[0].message)
