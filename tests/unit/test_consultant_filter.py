"""Unit tests for the consultant-mode detector and verdict downweighting.

Covers:
- graph.hooks.is_consultant_mode — regex detector fires on RU advisory
  phrasing and stays silent on normal persona speech,
- _verdict_weight — CONSULTANT_MODE_WEIGHT applied per Verdict,
- _aggregate — a candidate backed by consultant-mode verdicts loses the
  ranking against an otherwise-equal candidate,
- evaluate_as_persona_loop node — end-to-end with stubbed run_agent:
  consultant_mode_detected is logged and the clean candidate wins.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from graph.hooks import is_consultant_mode
from graph.nodes import evaluate_as_persona_loop as mod
from graph.state import MessageCandidate, Persona, Verdict

# ----- detector: fires on consultant phrasing --------------------------------

CONSULTANT_PHRASES = [
    "Рекомендую сделать слоган короче.",
    "стоит улучшить CTA, он слишком общий",
    "Стоит добавить цифры в заголовок.",
    "Следует добавить конкретику про цену.",
    "Я бы посоветовал другой заголовок.",
    "я бы посоветовала переформулировать оффер",
    "Нужно доработать текст, он сырой.",
    "надо доработать оффер",
    "Совет: усильте заголовок.",
    "Как маркетолог скажу — слабовато.",
    "можно улучшить подачу выгоды",
    "Можно было бы усилить CTA.",
    "С точки зрения маркетинга оффер размыт.",
    "Здесь рекомендуется уточнить условия.",
    "Советую переписать первый абзац.",
    "СТОИТ УЛУЧШИТЬ ОФФЕР",  # case-insensitivity
]

# ----- detector: silent on normal persona speech -----------------------------

PERSONA_PHRASES = [
    "Мне не нравится, слишком сложно написано.",
    "Слишком дорого для меня, я столько не плачу.",
    "Не стоит того, я пас.",
    "Друзья рекомендовали этот сервис, но я не верю рекламе.",
    "Я не понял что предлагают.",
    "Стоит 500 рублей в месяц — для меня дорого.",
    "О, это прямо про мою боль с дедлайнами, кликнул бы.",
    "Звучит как очередная реклама, пролистаю.",
    "",
]


@pytest.mark.parametrize("text", CONSULTANT_PHRASES)
def test_detector_fires_on_consultant_phrases(text: str):
    assert is_consultant_mode(text), f"should detect consultant mode: {text!r}"


@pytest.mark.parametrize("text", PERSONA_PHRASES)
def test_detector_silent_on_persona_speech(text: str):
    assert not is_consultant_mode(text), f"false positive on: {text!r}"


def test_detector_none_is_not_consultant():
    assert not is_consultant_mode(None)


# ----- _verdict_weight --------------------------------------------------------


def _verdict(
    candidate_id: str,
    segment: str = "seg",
    resonance: int = 8,
    clarity: int = 8,
    action_intent: int = 8,
    reaction: str = "Нравится, это про меня.",
    friction: str | None = None,
) -> Verdict:
    return Verdict(
        candidate_id=candidate_id,
        persona_segment=segment,
        resonance=resonance,
        clarity=clarity,
        action_intent=action_intent,
        free_form_reaction=reaction,
        main_friction=friction,
    )


def test_verdict_weight_normal():
    assert mod._verdict_weight(_verdict("c1")) == 1.0


def test_verdict_weight_consultant_reaction():
    v = _verdict("c1", reaction="Рекомендую улучшить слоган.")
    assert mod._verdict_weight(v) == mod.CONSULTANT_MODE_WEIGHT


def test_verdict_weight_consultant_friction_only():
    v = _verdict("c1", friction="стоит добавить CTA с конкретикой")
    assert mod._verdict_weight(v) == mod.CONSULTANT_MODE_WEIGHT


# ----- _aggregate: downweight changes the ranking -----------------------------


def _candidate(cid: str) -> MessageCandidate:
    return MessageCandidate(
        id=cid,
        slogan=f"slogan-{cid}",
        body="body",
        cta="cta",
        hook_angle="rational",
    )


def _persona(segment: str = "seg") -> Persona:
    return Persona(
        segment=segment,
        age_range="25-35",
        pain_points=["p"],
        motivations=["m"],
        objections=["o"],
        communication_style="прямой",
    )


def test_aggregate_clean_verdicts_keep_full_score():
    """No consultant verdicts: score == 0.4*res + 0.3*ai + 0.3*clarity."""
    cands = [_candidate("c1")]
    personas = [_persona()]
    verdicts = [_verdict("c1", resonance=8, clarity=6, action_intent=7)]
    scores = mod._aggregate(verdicts, cands, personas, persona_priority=0)
    assert scores["c1"] == pytest.approx(0.4 * 8 + 0.3 * 7 + 0.3 * 6)


def test_aggregate_consultant_candidate_loses_at_equal_scores():
    """Equal raw scores, but candidate B's verdict is consultant-mode → B loses."""
    cands = [_candidate("cA"), _candidate("cB")]
    personas = [_persona()]
    verdicts = [
        _verdict("cA", reaction="Это прямо про меня, кликнул бы."),
        _verdict("cB", reaction="Рекомендую улучшить слоган и добавить CTA."),
    ]
    scores = mod._aggregate(verdicts, cands, personas, persona_priority=0)
    assert scores["cA"] == pytest.approx(8.0)
    assert scores["cB"] == pytest.approx(8.0 * mod.CONSULTANT_MODE_WEIGHT)
    assert max(scores, key=lambda k: scores[k]) == "cA"


def test_aggregate_consultant_verdict_not_dropped():
    """Downweighted verdict still contributes — score > 0."""
    cands = [_candidate("cB")]
    personas = [_persona()]
    verdicts = [_verdict("cB", reaction="стоит улучшить заголовок")]
    scores = mod._aggregate(verdicts, cands, personas, persona_priority=0)
    assert scores["cB"] > 0.0


def test_aggregate_multi_persona_partial_downweight():
    """Consultant verdict from one persona drags the candidate below a clean rival."""
    cands = [_candidate("cA"), _candidate("cB")]
    personas = [_persona("seg1"), _persona("seg2")]
    verdicts = [
        _verdict("cA", segment="seg1"),
        _verdict("cA", segment="seg2"),
        _verdict("cB", segment="seg1"),
        _verdict("cB", segment="seg2", reaction="Следует добавить конкретику."),
    ]
    scores = mod._aggregate(verdicts, cands, personas, persona_priority=0)
    assert scores["cA"] == pytest.approx(8.0)
    # B: seg1 full 8.0, seg2 downweighted 4.0, equal persona weights → 6.0
    assert scores["cB"] == pytest.approx(6.0)
    assert scores["cB"] < scores["cA"]


# ----- node end-to-end (stubbed run_agent) ------------------------------------


class _LogRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))


def _node_state(candidates: list[MessageCandidate]) -> dict:
    return {
        "session_id": "s-test",
        "brief": {
            "product": "Cloud.ru Evolution",
            "goal": "consideration",
            "audience_raw": "DevOps в финтехе",
            "channel": "tg_post",
            "formats": ["banner_300x250"],
            "constraints": [],
            "age_rating": "0+",
        },
        "candidates": [c.model_dump() for c in candidates],
        "personas": [_persona().model_dump()],
        "persona_priority": 0,
        "revise_round": 0,
    }


async def test_node_logs_detection_and_clean_candidate_wins(monkeypatch):
    cand_a = _candidate("candA")
    cand_b = _candidate("candB")
    reactions = {
        "candA": "О, это про мою боль, я бы кликнул.",
        "candB": "Рекомендую улучшить слоган и добавить конкретики.",
    }

    monkeypatch.setattr(mod, "load_skill", lambda name: SimpleNamespace(body=""))
    monkeypatch.setattr(mod, "_extract_section", lambda body, header: "tpl")
    # Render stub: user message carries candidate.id so run_agent stub can
    # pick the right canned reaction; system message renders to "".
    monkeypatch.setattr(
        mod, "_render", lambda tpl, **kw: str(kw.get("candidate.id", ""))
    )
    monkeypatch.setattr(
        mod,
        "load_agent",
        lambda agent_id: SimpleNamespace(
            extra={
                "concurrency": {"semaphore": 2},
                "revise": {"threshold": 0.0, "max_rounds": 2},
            }
        ),
    )
    monkeypatch.setattr(mod, "get_shared_client", lambda: None)

    async def fake_run_agent(agent_id, *, messages, schema, client=None, session_id=None):
        cand_id = messages[1]["content"]
        return Verdict(
            candidate_id=cand_id,
            persona_segment="seg",
            resonance=8,
            clarity=8,
            action_intent=8,
            free_form_reaction=reactions[cand_id],
            main_friction=None,
        )

    monkeypatch.setattr(mod, "run_agent", fake_run_agent)
    recorder = _LogRecorder()
    monkeypatch.setattr(mod, "log", recorder)

    out = await mod.evaluate_as_persona_loop(_node_state([cand_a, cand_b]))

    assert out["winner"] is not None
    assert out["winner"]["id"] == "candA"
    assert out["candidate_scores"]["candA"] == pytest.approx(8.0)
    assert out["candidate_scores"]["candB"] == pytest.approx(
        8.0 * mod.CONSULTANT_MODE_WEIGHT
    )

    detected = [kw for ev, kw in recorder.events if ev == "consultant_mode_detected"]
    assert len(detected) == 1
    assert detected[0]["candidate_id"] == "candB"
    assert detected[0]["persona_segment"] == "seg"
    assert detected[0]["agent"] == mod._AGENT_ID
    assert detected[0]["session_id"] == "s-test"
