"""Cloud.ru Foundation Models client.

Model-aware thinking-toggle and retry-with-feedback. Plain prompt + Pydantic
parsing instead of response_format=json_schema (the json_schema route returns
404 on Cloud.ru — empirical finding from Cloud.ru Slides Bot).

Empirical traps respected here:
- GLM (zai-org/GLM-4.7): thinking enabled by default; on input >= 10K tokens it
  burns the budget in reasoning_content and returns content=None. Force
  thinking=False for non-reasoning calls (parse_brief, message_gen).
- Kimi-K2.6 vision: thinking toggle ignored, max_tokens must be >= 2500.
- DeepSeek-V4-Pro: non-reasoning by default, no extra_body needed.

Model availability is per-project: a model can be in the public catalog yet
return 403/404 for this service account (e.g. GLM-5.1 was revoked 2026-07-23).
``call`` therefore walks a permitted fallback chain (``_FALLBACKS``) instead of
failing the whole /new run when the primary model is unavailable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

import structlog
from openai import (
    APIConnectionError,
    AsyncOpenAI,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

# Errors that mean "this model didn't give a usable answer" — retry the next
# candidate in the fallback chain rather than failing the whole run:
#   403 PermissionDenied  — model not enabled for this project
#   404 NotFound          — model removed from the catalog
#   >=500 InternalServer  — upstream/model backend failure
#   429 RateLimit         — capacity exhausted; another model may have headroom
#   APIConnection/Timeout — network failure reaching this model
# Deliberately NOT included: 400/401/422 (our request/auth is wrong — retrying a
# different model would just repeat the same client-side error).
_FALLBACK_ERRORS = (
    PermissionDeniedError,
    NotFoundError,
    InternalServerError,
    RateLimitError,
    APIConnectionError,
)


class ModelName(str, Enum):
    # GLM-5.1 was revoked for this project 2026-07-23 ("model is not available
    # for this project"); GLM-4.7 is the permitted same-family replacement and
    # honours the identical enable_thinking toggle.
    GLM = "zai-org/GLM-4.7"
    DEEPSEEK = "deepseek-ai/DeepSeek-V4-Pro"
    KIMI = "moonshotai/Kimi-K2.6"


@dataclass(frozen=True)
class ModelCall:
    """Parameters for a single LLM call."""

    model: ModelName
    messages: list[ChatCompletionMessageParam]
    thinking: bool = False
    max_tokens: int = 4000
    temperature: float = 0.7


# Ordered fallback chain per primary model, tried when the primary is
# unavailable for this project (403 PermissionDenied / 404 NotFound — the model
# is in the public catalog but not enabled for the service account). Only models
# confirmed permitted for THIS project belong here. Kimi-K2.6 is the sole
# permitted multimodal model, so vision calls have no alternative (empty chain).
_FALLBACKS: dict[ModelName, tuple[ModelName, ...]] = {
    ModelName.GLM: (ModelName.DEEPSEEK,),
    ModelName.DEEPSEEK: (ModelName.GLM,),
    ModelName.KIMI: (),
}


def _extra_body(model: ModelName, thinking: bool, has_vision: bool) -> dict[str, Any]:
    """Build extra_body for thinking-toggle per model.

    Vision calls on Kimi always reason (toggle is ignored upstream).
    """
    if model == ModelName.GLM:
        return {"chat_template_kwargs": {"enable_thinking": thinking}}
    if model == ModelName.KIMI and not has_vision:
        return {"thinking": {"type": "enabled" if thinking else "disabled"}}
    return {}


def _has_vision_content(messages: list[ChatCompletionMessageParam]) -> bool:
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


class CloudRuClient:
    """Async OpenAI-compatible client for Cloud.ru FM."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ["CLOUDRU_API_KEY"]
        self.base_url = base_url or os.environ.get(
            "CLOUDRU_BASE_URL", "https://foundation-models.api.cloud.ru/v1"
        )
        self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    async def call(self, params: ModelCall) -> str:
        """Single call, returns raw content string.

        Walks the primary model's permitted fallback chain: if a model is
        unavailable for this project (403/404), the next candidate is tried
        transparently. Vision calls never fall back to a text-only model.
        """
        has_vision = _has_vision_content(params.messages)
        if has_vision and params.model != ModelName.KIMI:
            raise ValueError(
                f"Model {params.model} does not support vision; only Kimi-K2.6 is multimodal"
            )
        max_tokens = params.max_tokens
        if has_vision and max_tokens < 2500:
            log.warning("kimi_vision_max_tokens_floor", requested=max_tokens, applied=2500)
            max_tokens = 2500

        chain: list[ModelName] = [params.model, *_FALLBACKS.get(params.model, ())]
        if has_vision:
            # Only Kimi is multimodal; drop text-only fallbacks for image calls.
            chain = [m for m in chain if m == ModelName.KIMI]

        last_exc: Exception | None = None
        for idx, model in enumerate(chain):
            extra = _extra_body(model, params.thinking, has_vision)
            has_next = idx < len(chain) - 1
            log.info(
                "cloudru_call",
                model=model.value,
                thinking=params.thinking,
                has_vision=has_vision,
                max_tokens=max_tokens,
                n_messages=len(params.messages),
                fallback=idx > 0,
            )
            try:
                resp = await self._client.chat.completions.create(
                    model=model.value,
                    messages=params.messages,
                    max_tokens=max_tokens,
                    temperature=params.temperature,
                    extra_body=extra if extra else None,
                )
            except _FALLBACK_ERRORS as exc:
                # Unavailable / server-side / capacity / network failure — the
                # model gave no usable answer, so try the next candidate.
                last_exc = exc
                log.warning(
                    "cloudru_model_unavailable",
                    model=model.value,
                    error=type(exc).__name__,
                    status=getattr(exc, "status_code", None),
                    fallbacks_left=[m.value for m in chain[idx + 1 :]],
                )
                continue

            content = resp.choices[0].message.content
            if content is None:
                # Known GLM trap: thinking-ON on large input burns the budget in
                # reasoning_content and returns content=None. Treat as a soft
                # failure — a non-reasoning fallback (e.g. DeepSeek) may answer.
                # Only surface the error if no candidate is left.
                log.error(
                    "cloudru_null_content",
                    model=model.value,
                    thinking=params.thinking,
                    finish_reason=resp.choices[0].finish_reason,
                    fallbacks_left=[m.value for m in chain[idx + 1 :]],
                )
                last_exc = RuntimeError(
                    f"Model {model.value} returned content=None "
                    f"(finish_reason={resp.choices[0].finish_reason}). "
                    "If thinking=True on >=10K input — disable thinking and retry."
                )
                if has_next:
                    continue
                raise last_exc

            if idx > 0:
                log.info(
                    "cloudru_fallback_succeeded",
                    primary=params.model.value,
                    used=model.value,
                )
            return content

        # Every candidate failed (unavailable / server error / no content).
        assert last_exc is not None
        raise last_exc

    async def call_structured(
        self,
        params: ModelCall,
        schema: type[T],
        retry: int = 1,
    ) -> T:
        """Plain-prompt structured output with Pydantic validation + 1 retry-with-feedback.

        json_schema route returns 404 on Cloud.ru — instead, embed the schema in
        the prompt and validate locally. On ValidationError, append a feedback
        message and retry once.
        """
        attempts: list[ChatCompletionMessageParam] = list(params.messages)
        last_err: Exception | None = None
        for attempt in range(retry + 1):
            raw = await self.call(
                ModelCall(
                    model=params.model,
                    messages=attempts,
                    thinking=params.thinking,
                    max_tokens=params.max_tokens,
                    temperature=params.temperature,
                )
            )
            try:
                return schema.model_validate_json(_strip_json_fences(raw))
            except ValidationError as exc:
                last_err = exc
                log.warning(
                    "cloudru_validation_failed",
                    model=params.model.value,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt >= retry:
                    break
                attempts.append({"role": "assistant", "content": raw})
                attempts.append(
                    {
                        "role": "user",
                        "content": (
                            "Ответ не прошёл валидацию схемы. Ошибки:\n"
                            f"{exc}\n\n"
                            "Верни ТОЛЬКО валидный JSON по схеме, без markdown-обёртки."
                        ),
                    }
                )
        assert last_err is not None
        raise last_err


_shared_client: CloudRuClient | None = None


def get_shared_client() -> CloudRuClient:
    """Process-wide CloudRuClient singleton.

    AsyncOpenAI keeps an httpx connection pool per instance; creating a fresh
    client per agent run forces a new TCP+TLS handshake on every LLM call
    (8-20 calls per /new). Sharing one instance reuses connections.
    """
    global _shared_client
    if _shared_client is None:
        _shared_client = CloudRuClient()
    return _shared_client


def _strip_json_fences(s: str) -> str:
    """Strip ```json ... ``` fences if model wrapped output in markdown."""
    s = s.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()
