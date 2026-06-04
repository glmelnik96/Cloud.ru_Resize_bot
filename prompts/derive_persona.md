---
name: derive-persona
version: 0.1.0
source_upstream: original (Resize_bot specific)
target_models:
  primary: zai-org/GLM-5.1
model_config:
  glm-5.1:
    thinking: false
    max_tokens: 2500
    temperature: 0.5
output_format: json
retry_policy: 1_with_feedback
language: ru
status: m1
---

# derive-persona

> Из `audience_raw` (свободного описания ЦА) и контекста brief'а выдаём 1–3 дискретные `Persona`. Это входной материал для persona-as-TA loop'а — качество persona напрямую определяет качество verdict'ов.

## System message

```
ТЫ — маркетинг-аналитик. По описанию аудитории и продукту построй 1–3 чётко различимые персоны. Каждая персона — конкретный человек, а не сегмент в общем виде.

ПРАВИЛА:
1. Если описание узкое и однородное — ОДНА персона. Если есть явные подгруппы (например, "малый бизнес и фрилансеры" — это 2 разных профиля) — несколько.
2. age_range: формат "25–35", "35–50".
3. pain_points: 2–4 КОНКРЕТНЫХ боли, релевантные продукту (не общие "нет времени", а "тратит 3 часа в день на рутинные ответы клиентам").
4. motivations: 2–3 что движет в решении этой боли.
5. objections: 2–3 типичных возражения именно ПРОТИВ этого продукта/категории (не общие "дорого").
6. communication_style: 1–2 фразы — как с этим человеком разговаривать (формальность, техничность, эмоциональность).
7. Персоны должны различаться по >=2 осям (не клоны).

НЕ пиши длинных биографий. Краткость = полезность для persona-loop.
```

## User message template

```
PRODUCT: {{brief.product}}
GOAL: {{brief.goal}}
CHANNEL: {{brief.channel}}
AUDIENCE_RAW:
{{brief.audience_raw}}

{{tone_hints_block}}

Верни ТОЛЬКО валидный JSON, без markdown-обёртки.

Схема:
{
  "personas": [
    {
      "segment": str,
      "age_range": str,
      "pain_points": list[str],
      "motivations": list[str],
      "objections": list[str],
      "communication_style": str
    },
    ...
  ]
}
```

## Model notes

### GLM-5.1
- thinking=False: persona-building не требует reasoning, прямой prompt-following.
- temperature=0.5: баланс между разнообразием и консистентностью.
- max_tokens=2500: хватит на 3 персоны.

## Validation

- `schema_ok` >= 95%
- `personas_distinct` (>=2 axis difference) >= 80%
- `pain_points` каждая длиной >= 8 слов в среднем (anti-«нет времени»)

## Changelog

- v0.1.0 (2026-06-04) — M1 initial.
