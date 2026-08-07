"""Unit tests for the lint_candidates node (блок 3, этап 1 «Язык»).

Линт стоит между select_by_persona и hitl_text_approve. Он НИЧЕГО не
отбраковывает и не переписывает — только вешает флажки на карточки
(lint_flags), которые уходят в HITL как есть. Два контура:

- код-фильтры (детерминированные): стоп-лист штампов (референс — brandkit
  Bannerzila) и «цифра без источника» (число, которого нет ни в фактах о
  продукте, ни в брифе);
- инверсионный судья (LLM ДРУГОГО семейства — DeepSeek при генераторе GLM):
  читает ТОЛЬКО то, что видно на баннере (slogan+cta), говорит, что понял,
  и сверяется с обещанным результатом. Fail-open: упал судья — флажков от
  судьи нет, конвейер живёт.
"""

from __future__ import annotations

import pytest

from graph.nodes import lint_candidates as mod
from graph.state import JudgeReading, JudgeReadingSet


def _card(cid: str, **over) -> dict:
    base = {
        "id": cid,
        # без цифр: цифра в слогане без источника — сама по себе флажок
        "slogan": "Понятный слоган без цифр",
        "body": f"body {cid}",
        "cta": "Попробовать",
        "hook_angle": "rational",
        "anchor": "боль: очередь на GPU",
        "desired_outcome": "обучение стартует через минуты",
        "score": 5.0,
        "reason": "узнаю себя",
    }
    base.update(over)
    return base


def _state(ranked: list[dict]) -> dict:
    return {
        "session_id": "s-lint",
        "brief": {
            "product": "Evolution ML Inference",
            "audience_raw": "ML-инженеры",
            "emotion": "спокойная уверенность",
            "notes": "",
        },
        "product": {
            "canonical_name": "Evolution ML Inference",
            "what_it_is": "Управляемый инференс моделей на GPU в облаке.",
            "proof_points": ["SLA 99.9%", "старт за 5 минут"],
        },
        "personas": [
            {
                "segment": "ML-инженер",
                "age_range": "25-40",
                "pain_points": ["очередь на GPU"],
                "motivations": ["скорость итераций"],
                "objections": ["дорого"],
                "communication_style": "прямой",
            }
        ],
        "ranked": ranked,
    }


def _ok_judge(ranked: list[dict]) -> JudgeReadingSet:
    return JudgeReadingSet(
        readings=[
            JudgeReading(
                candidate_id=c["id"],
                understood="понял ровно то, что обещано",
                matches_offer=True,
            )
            for c in ranked
        ]
    )


@pytest.fixture
def stub_judge(monkeypatch):
    calls: list[dict] = []

    def install(result=None, exc: Exception | None = None):
        async def fake_run_agent(agent_id, *, messages, schema, session_id=None):
            calls.append({"agent_id": agent_id, "messages": messages})
            if exc is not None:
                raise exc
            return result

        monkeypatch.setattr(mod, "run_agent", fake_run_agent)
        return calls

    return install


async def test_clean_candidates_get_empty_flags(stub_judge):
    ranked = [_card("c1"), _card("c2")]
    stub_judge(_ok_judge(ranked))
    out = await mod.lint_candidates(_state(ranked))
    assert [c["id"] for c in out["ranked"]] == ["c1", "c2"]
    assert all(c["lint_flags"] == [] for c in out["ranked"])


async def test_stopword_in_slogan_is_flagged(stub_judge):
    ranked = [_card("c1", slogan="Инновационный инференс")]
    stub_judge(_ok_judge(ranked))
    out = await mod.lint_candidates(_state(ranked))
    flags = out["ranked"][0]["lint_flags"]
    assert any("штамп" in f and "инновационный" in f.lower() for f in flags)


async def test_number_without_source_is_flagged(stub_judge):
    ranked = [_card("c1", desired_outcome="ускорение в 10 раз")]
    stub_judge(_ok_judge(ranked))
    out = await mod.lint_candidates(_state(ranked))
    flags = out["ranked"][0]["lint_flags"]
    assert any("10" in f and "источник" in f for f in flags)


async def test_number_from_proof_points_is_allowed(stub_judge):
    ranked = [_card("c1", slogan="SLA 99.9% без сюрпризов"[:42])]
    stub_judge(_ok_judge(ranked))
    out = await mod.lint_candidates(_state(ranked))
    assert out["ranked"][0]["lint_flags"] == []


async def test_judge_mismatch_is_flagged(stub_judge):
    ranked = [_card("c1"), _card("c2")]
    judge = JudgeReadingSet(
        readings=[
            JudgeReading(
                candidate_id="c1",
                understood="что мне продают железо",
                matches_offer=False,
            ),
            JudgeReading(
                candidate_id="c2",
                understood="понял как обещано",
                matches_offer=True,
            ),
        ]
    )
    stub_judge(judge)
    out = await mod.lint_candidates(_state(ranked))
    assert any("что мне продают железо" in f for f in out["ranked"][0]["lint_flags"])
    assert out["ranked"][1]["lint_flags"] == []


async def test_judge_failure_is_fail_open(stub_judge):
    ranked = [_card("c1", slogan="Уникальный сервис")]
    stub_judge(exc=RuntimeError("LLM down"))
    out = await mod.lint_candidates(_state(ranked))
    # код-фильтры отработали, судейских флажков нет, узел не упал
    flags = out["ranked"][0]["lint_flags"]
    assert any("штамп" in f for f in flags)
    assert not any("понял" in f for f in flags)


async def test_judge_sees_only_banner_surface(stub_judge):
    """Инверсионный тест честен, только если судья читает то, что видит
    зритель: slogan и cta. body — внутренняя заметка, её в вводе быть не
    должно (обещанный результат передаётся отдельно для сверки)."""
    ranked = [_card("c1", body="СЕКРЕТНАЯ внутренняя заметка")]
    calls = stub_judge(_ok_judge(ranked))
    await mod.lint_candidates(_state(ranked))
    user_msg = calls[0]["messages"][1]["content"]
    assert "СЕКРЕТНАЯ внутренняя заметка" not in user_msg
    assert "Понятный слоган без цифр" in user_msg


async def test_empty_ranked_returns_empty_and_skips_judge(stub_judge):
    calls = stub_judge(_ok_judge([]))
    out = await mod.lint_candidates(_state([]))
    assert out == {}
    assert calls == []


def test_builder_wires_lint_between_select_and_hitl():
    from graph.builder import build_text_graph

    g = build_text_graph()
    assert "lint_candidates" in g.nodes
    edges = {(e[0], e[1]) for e in g.edges}
    assert ("select_by_persona", "lint_candidates") in edges
    assert ("lint_candidates", "hitl_text_approve") in edges
    assert ("select_by_persona", "hitl_text_approve") not in edges
