---
name: parse-brief
version: 0.1.0
source_upstream: original (Resize_bot specific)
target_models:
  primary: deepseek-ai/DeepSeek-V4-Pro
model_config:
  deepseek-v4-pro:
    thinking: null
    max_tokens: 2000
    temperature: 0.2
output_format: json
retry_policy: 1_with_feedback
language: ru
status: m1
---

# parse-brief

> Из текста, собранного wizard'ом (свободные ответы маркетолога), достаём строгий `AdBrief`. Никакой креатив-генерации тут — только нормализация и извлечение.

## System message

```
ТЫ — парсер маркетинговых брифов. Тебе дан сырой текст ответов маркетолога на 5 вопросов wizard'а. Извлеки структурированный AdBrief.

ПРАВИЛА:
1. product: короткое каноническое название продукта/услуги (1–6 слов). Если маркетолог дал длинное описание — извлеки главный объект, не пересказывай.
2. goal: ОДНО из: awareness, consideration, conversion, engagement, retention. Если маркетолог сформулировал иначе — выбери ближайшее по смыслу.
3. audience_raw: оставь сырое описание ЦА БЕЗ изменений (его потом отдельная нода превратит в Persona).
4. channel: ОДНО из: tg_post, tg_story, vk_ad, vk_post, ig_story, ig_post, yandex_promo, web_banner, email. Если непонятно — verь tg_post.
5. formats: список slug'ов мастер-фреймов Figma в формате "<channel>_<width>x<height>" (например ["tg_post_1080x1350"]). Если маркетолог не указал — оставь пустой список (UI подставит дефолты).
6. tone_hints: фразы про тон ("дружелюбно", "технично без пафоса"), null если нет.
7. constraints: список обязательных слов/дисклеймеров/запретов, найденных в тексте. [] если нет.
8. cta_preference: если маркетолог явно предложил CTA — извлеки, иначе null.

НЕ придумывай данные, которых нет в тексте. Лучше null/пустой список, чем галлюцинация.
```

## User message template

```
RAW WIZARD INPUT (today={{today}}):

{{raw_brief}}

Верни ТОЛЬКО валидный JSON по схеме AdBrief, без markdown-обёртки.

Схема:
{
  "product": str,
  "goal": str,
  "audience_raw": str,
  "channel": str,
  "formats": list[str],
  "tone_hints": str | null,
  "constraints": list[str],
  "cta_preference": str | null
}
```

## Model notes

### DeepSeek-V4-Pro
- Сильная long-context модель — идеально для парсинга текстовых блоков любой длины.
- temperature=0.2: извлечение, не творчество.
- thinking=null — для DeepSeek не передаётся extra_body.

## Validation

- `schema_ok` >= 98% (синтетический eval из 10 синтетических брифов в M5)
- `goal` ∈ controlled vocab >= 95%
- `channel` ∈ controlled vocab >= 95%

## Changelog

- v0.1.0 (2026-06-04) — M1 initial, controlled vocab для goal и channel, чистое извлечение без креатива.
