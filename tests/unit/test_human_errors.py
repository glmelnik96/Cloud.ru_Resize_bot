"""Что человек читает, когда прогон падает.

До 2026-08-11 в панель уходил `f"{type(exc).__name__}: {exc}"` — на проде это
означало кусок дампа pydantic со ссылкой на errors.pydantic.dev. По такому
сообщению нельзя понять ни что случилось, ни что делать дальше.
"""

from __future__ import annotations

import pytest

from app.tasks.errors import human_error
from graph.agent_runner import AgentSchemaError
from graph.errors import HumanFacingError


def _schema_error() -> AgentSchemaError:
    from pydantic import BaseModel, Field, ValidationError

    class _M(BaseModel):
        slogan: str = Field(max_length=3)

    try:
        _M.model_validate({"slogan": "длинновато"})
    except ValidationError as exc:
        return AgentSchemaError("generate_message_candidates", "{}", exc)
    raise AssertionError("схема неожиданно пропустила значение")


def test_schema_failure_reads_as_a_sentence():
    msg = human_error(_schema_error())
    assert "pydantic" not in msg.lower()
    assert "ValidationError" not in msg
    assert "формат" in msg.lower()
    assert "заново" in msg.lower(), "человеку нужно сказать, что делать"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("RateLimitError", "частот"),
        ("APIConnectionError", "связ"),
        ("APITimeoutError", "связ"),
        ("InternalServerError", "стороне"),
        ("PermissionDeniedError", "недоступ"),
        ("HeroGenUnavailable", "картин"),
    ],
)
def test_known_failures_get_their_own_sentence(name, expected):
    """Классы моделей и App1 распознаём по имени, а не по isinstance: тянуть в
    app/ SDK ради except-ветки запрещено границей импортов (см. contract-тест
    OPENAI_ALLOWED)."""
    exc = type(name, (Exception,), {})("технический текст")
    assert expected in human_error(exc).lower()


def test_our_own_message_goes_through_verbatim():
    """`HumanFacingError` для того и заведён: его текст уже написан человеку."""
    msg = "Модель вернула слишком мало пригодных вариантов текста."
    assert human_error(HumanFacingError(msg)) == msg


def test_unknown_failure_still_names_itself():
    """Неизвестную ошибку не прячем совсем: человеку нужно, что показать
    разработчику, но без стены трейсбека."""
    msg = human_error(KeyError("ratio"))
    assert "KeyError" in msg
    assert "заново" in msg.lower()


def test_message_is_one_line():
    """Панель прогресса и строка в списке задач — однострочные."""
    for exc in (_schema_error(), KeyError("x"), HumanFacingError("Текст")):
        assert "\n" not in human_error(exc)
