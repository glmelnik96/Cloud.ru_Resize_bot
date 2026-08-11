"""Что делает `run_agent`, когда модель так и не попала в схему.

До 2026-08-11 наружу летел голый pydantic `ValidationError` — без текста
ответа. Узел не мог ни спасти годную часть, ни объяснить человеку, что
случилось, и прогон умирал целиком (прод 2026-08-10, слоган в 43 символа при
лимите 42). Теперь ошибка своя и несёт сырой ответ последней попытки.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from graph.agent_runner import AgentSchemaError, run_agent


class _Tiny(BaseModel):
    slogan: str = Field(max_length=10)


class _FakeClient:
    """Отдаёт заранее заданные ответы по одному на попытку."""

    def __init__(self, replies: list[str]):
        self.replies = replies
        self.calls: list[object] = []

    async def call(self, call):
        self.calls.append(call)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


@pytest.mark.asyncio
async def test_exhausted_schema_retries_carry_the_raw_answer():
    bad = '{"slogan": "заведомо длиннее десяти символов"}'
    client = _FakeClient([bad])
    with pytest.raises(AgentSchemaError) as exc:
        await run_agent(
            "generate_message_candidates",
            messages=[{"role": "user", "content": "x"}],
            schema=_Tiny,
            client=client,  # type: ignore[arg-type]
        )
    # Бюджет карточки — одна повторная попытка: два вызова, потом сдаёмся.
    assert len(client.calls) == 2
    assert exc.value.raw == bad
    assert exc.value.agent == "generate_message_candidates"
    # Узел ловит именно ValueError-подкласс, а не что-то новое в иерархии.
    assert isinstance(exc.value, ValueError)


@pytest.mark.asyncio
async def test_second_attempt_still_wins():
    """Ретрай с фидбэком остался рабочим — сдаёмся только после него."""
    client = _FakeClient(['{"slogan": "слишком длинный ответ"}', '{"slogan": "коротко"}'])
    result = await run_agent(
        "generate_message_candidates",
        messages=[{"role": "user", "content": "x"}],
        schema=_Tiny,
        client=client,  # type: ignore[arg-type]
    )
    assert result.slogan == "коротко"
