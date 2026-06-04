---
name: creative-positioning
version: 0.1.0
source_upstream: https://github.com/DKeken/codex-skills-alternative/tree/main/skills/creative-positioning
target_models:
  primary: zai-org/GLM-5.1
  alt: deepseek-ai/DeepSeek-V4-Pro
model_config:
  glm-5.1:
    thinking: false
    max_tokens: 3000
  deepseek-v4-pro:
    thinking: null
    max_tokens: 3000
output_format: json
retry_policy: 1_with_feedback
language: ru
status: skeleton
---

# creative-positioning

> Cloud.ru бренд-overlay поверх рекламного сообщения. Адаптация upstream `codex-skills-alternative/creative-positioning` под GLM-5.1 / DeepSeek-V4-Pro. Применяется как post-processor после `creative-ads-explorer` (на М2-M3).

## Назначение

Формализация бренд-правил Cloud.ru, валидация и patching кандидатов под:
- ToV (Tone of Voice): техничный, спокойный, без маркетингового надрыва
- Запрещённые формулировки: «революция в облаках», «лидер рынка», превосходные степени без фактов
- Обязательные элементы: продуктовый токен (`Cloud.ru` или конкретный продукт), CTA содержит действие глаголом

Реальный гайд берётся из Cloud.ru Brand Enhancer (см. `2nd brain\01 Projects\Cloud.ru Brand Enhancer`) — на M3 интегрируется живой набор правил.

## System prompt (skeleton — финализируется в M3)

```
TASK: Проверь и поправь кандидата под бренд-стандарт Cloud.ru.

INPUT: один MessageCandidate.

CHECKS:
1. Tone: технологичный, спокойный, без восклицаний и пафоса
2. Без запрещённых конструкций (см. forbidden_patterns)
3. Содержит продуктовый якорь ({brief.core.product} или Cloud.ru)
4. CTA — глагол в инфинитиве/повелительном

OUTPUT: JSON
{
  "approved": bool,
  "patched": MessageCandidate | null,   # если требовалась правка, иначе null
  "violations": [str, ...]              # пустой массив если approved=true
}

Если approved=false и patched=null — кандидат непригоден (например, тематика противоречит бренду).
ВЕРНИ ТОЛЬКО валидный JSON.
```

## Forbidden patterns (skeleton — наполнение в M3 из Cloud.ru Brand guide)

```python
FORBIDDEN_PATTERNS = [
    r"революция",
    r"лидер\s+(рынка|индустрии)",
    r"самый\s+(лучший|быстрый|надёжный)",
    r"номер\s+один",
    # ... добавляется из Cloud.ru Brand Enhancer docs
]
```

## Model-specific notes

### GLM-5.1
- thinking=False стандартно. Регуляторная задача не требует reasoning.

### DeepSeek-V4-Pro
- Прецизионнее GLM в проверках «должен содержать X». Используется как secondary при разночтениях.

## Validation criteria

- `schema_ok` >= 95%
- `false_positive_rate` (approved валидных кандидатов) — оценивается на eval-наборе M5
- `false_negative_rate` (rejected проблемных) — то же

## Changelog

- v0.1.0 (2026-06-04) — skeleton, упор на интеграцию с Cloud.ru Brand Enhancer docs.
