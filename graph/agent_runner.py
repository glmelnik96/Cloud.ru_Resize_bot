"""Unified agent execution: pre-hooks → LLM → post-hooks → optional retry.

Replaces direct `client.call_structured()` calls in nodes. Configuration comes
from the agent's YAML card (`agents/<id>.yaml`).

Retry semantics:
- Pydantic ValidationError → feedback message + retry (within retry_with_feedback
  budget); budget exhausted → `AgentSchemaError` carrying the raw answer, so the
  caller can salvage the valid part instead of losing the whole run.
- Post-hook `fail` violations → feedback message + retry (within same budget).
- Post-hook `warn` violations → logged, never block.
- If budget exhausted with `fail` violations → result is still returned but
  `agent_violations_exhausted` is logged at ERROR. The caller decides whether
  to surface to the user; current default is to proceed (degraded output is
  better than no output for an interactive bot).
"""

from __future__ import annotations

from typing import TypeVar

import structlog
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from agents.registry import load_agent
from graph.hooks import Violation, run_hooks
from llm.cloudru import (  # noqa: PLC2701  intentional cross-module use
    CloudRuClient,
    ModelCall,
    ModelName,
    _strip_json_fences,
    get_shared_client,
)

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class AgentSchemaError(ValueError):
    """Модель не попала в схему за все отведённые попытки.

    Несёт сырой текст последней попытки, чтобы вызывающий узел мог спасти
    годную часть ответа. Без этого прогон умирал целиком из-за одного
    бракованного элемента списка (прод 2026-08-10): pydantic сообщал, ЧТО
    сломалось, но текст ответа оставался внутри `run_agent`.
    """

    def __init__(self, agent: str, raw: str, error: ValidationError):
        super().__init__(f"агент {agent}: ответ не прошёл схему")
        self.agent = agent
        self.raw = raw
        self.error = error

_MODEL_MAP: dict[str, ModelName] = {
    "glm-4.7": ModelName.GLM,
    "deepseek-v4-pro": ModelName.DEEPSEEK,
    "kimi-k2.6": ModelName.KIMI,
}


async def run_agent(
    agent_id: str,
    messages: list[ChatCompletionMessageParam],
    schema: type[T],
    *,
    client: CloudRuClient | None = None,
    session_id: str | None = None,
) -> T:
    """Run an agent end-to-end. Returns the validated Pydantic result."""
    card = load_agent(agent_id)
    client = client or get_shared_client()
    model = _MODEL_MAP[card.model]
    cfg = card.model_config

    attempts: list[ChatCompletionMessageParam] = list(messages)
    last_err: Exception | None = None

    for attempt in range(card.retry_with_feedback + 1):
        raw = await client.call(
            ModelCall(
                model=model,
                messages=attempts,
                thinking=bool(cfg.get("thinking", False)),
                max_tokens=int(cfg.get("max_tokens", 4000)),
                temperature=float(cfg.get("temperature", 0.7)),
            )
        )

        # 1. Pydantic validation
        try:
            result = schema.model_validate_json(_strip_json_fences(raw))
        except ValidationError as exc:
            last_err = exc
            log.warning(
                "agent_pydantic_failed",
                agent=agent_id,
                attempt=attempt,
                error=str(exc),
                session_id=session_id,
            )
            if attempt < card.retry_with_feedback:
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
                continue
            raise AgentSchemaError(agent_id, raw, exc) from exc

        # 2. Post-hooks
        violations: list[Violation] = run_hooks(result, card.post_hooks)
        fails = [v for v in violations if v.severity == "fail"]
        warns = [v for v in violations if v.severity == "warn"]

        for v in warns:
            log.info(
                "hook_violation",
                severity="warn",
                agent=agent_id,
                hook=v.hook,
                field=v.field,
                message=v.message,
                session_id=session_id,
            )
        for v in fails:
            log.warning(
                "hook_violation",
                severity="fail",
                agent=agent_id,
                hook=v.hook,
                field=v.field,
                message=v.message,
                session_id=session_id,
            )

        if not fails:
            return result

        if attempt < card.retry_with_feedback:
            feedback = "\n".join(f"- [{v.hook}/{v.field}] {v.message}" for v in fails)
            attempts.append({"role": "assistant", "content": raw})
            attempts.append(
                {
                    "role": "user",
                    "content": (
                        "Ответ нарушает требования бренда и формата:\n"
                        f"{feedback}\n\n"
                        "Сгенерируй заново, устранив эти нарушения. "
                        "Верни ТОЛЬКО валидный JSON по схеме, без markdown-обёртки."
                    ),
                }
            )
            continue

        log.error(
            "agent_violations_exhausted",
            agent=agent_id,
            n_fails=len(fails),
            session_id=session_id,
        )
        return result

    # Unreachable: loop returns or raises on every path.
    assert last_err is not None
    raise last_err
