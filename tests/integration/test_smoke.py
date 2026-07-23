"""Smoke test: ping all three Cloud.ru FM models with one API key.

Skipped by default if CLOUDRU_API_KEY is not set in env. Run with:
    pytest tests/integration/test_smoke.py -v

Verifies M0 DoD: API key works, all three models respond, model-aware
thinking-toggle does not raise.
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from llm.cloudru import CloudRuClient, ModelCall, ModelName


pytestmark = pytest.mark.skipif(
    not os.environ.get("CLOUDRU_API_KEY"),
    reason="CLOUDRU_API_KEY not set; integration smoke skipped",
)


@pytest.fixture(scope="module")
def client() -> CloudRuClient:
    return CloudRuClient()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", list(ModelName))
async def test_ping_each_model(client: CloudRuClient, model: ModelName) -> None:
    """Plain text call, thinking-OFF, minimal max_tokens."""
    max_tokens = 2500 if model == ModelName.KIMI else 50
    content = await client.call(
        ModelCall(
            model=model,
            messages=[{"role": "user", "content": "Ответь одним словом: ok"}],
            thinking=False,
            max_tokens=max_tokens,
        )
    )
    assert content
    assert len(content) < 200, f"Unexpectedly long reply: {content!r}"


class _PingSchema(BaseModel):
    status: str
    note: str


@pytest.mark.asyncio
async def test_structured_output_glm(client: CloudRuClient) -> None:
    """GLM (4.7) + plain prompt + Pydantic — the response_format=json_schema route returns 404."""
    result = await client.call_structured(
        ModelCall(
            model=ModelName.GLM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        'Верни JSON по схеме {"status": "ok", "note": "smoke"}. '
                        "Без markdown-обёртки."
                    ),
                }
            ],
            thinking=False,
            max_tokens=200,
        ),
        schema=_PingSchema,
    )
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_kimi_vision_max_tokens_floor(client: CloudRuClient) -> None:
    """Kimi vision-style call respects max_tokens >= 2500 floor (text-only here, no image)."""
    # Sanity: text-only Kimi works with explicit max_tokens=2500
    content = await client.call(
        ModelCall(
            model=ModelName.KIMI,
            messages=[{"role": "user", "content": "Ответь: pong"}],
            thinking=False,
            max_tokens=2500,
        )
    )
    assert content
