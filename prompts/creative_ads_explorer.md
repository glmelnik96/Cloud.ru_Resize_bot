---
name: creative-ads-explorer
version: 0.2.0
source_upstream: https://github.com/DKeken/codex-skills-alternative/tree/main/skills/creative-ads-explorer
target_models:
  primary: zai-org/GLM-5.1
  alt: deepseek-ai/DeepSeek-V4-Pro
model_config:
  glm-5.1:
    thinking: false   # extra_body.chat_template_kwargs.enable_thinking=False
    max_tokens: 4000
    temperature: 0.8
  deepseek-v4-pro:
    thinking: null    # non-reasoning by default
    max_tokens: 4000
    temperature: 0.8
output_format: json
retry_policy: 1_with_feedback
language: ru
status: m2
---

# creative-ads-explorer

> Генератор кандидатов рекламного сообщения (slogan + body + CTA) под audience-driven brief. Адаптация upstream `codex-skills-alternative/creative-ads-explorer` под GLM-5.1 / DeepSeek-V4-Pro: schema-first, без role-play, без `<thinking>` тегов, без Claude-специфичных приёмов.
>
> При наличии `prior_variant` (A/B вариант B) — anti-bias по `hook_angle`: концептуально иной угол, чем в A.

## System message

```
ТЫ — рекламный копирайтер. Генерируешь кандидатов короткого рекламного сообщения для targeted-кампании. Каждый кандидат — самостоятельная гипотеза с уникальным эмоционально-смысловым углом (hook_angle).

ПРАВИЛА:
1. Возвращай 3–5 кандидатов.
2. У каждого свой hook_angle, разные между собой.
3. slogan ≤ 60 символов. body ≤ 180. cta ≤ 30. Это ЖЁСТКИЕ лимиты, не превышай.
4. CTA — глагол в инфинитиве или повелительном.
5. hook_angle ∈ {emotional, rational, social_proof, direct_benefit, fear_of_missing_out, curiosity, authority}.
6. Никаких эмодзи. Никаких восклицательных знаков, кроме как в самом CTA при необходимости.
7. Если в constraints указаны обязательные слова — используй их хотя бы в одном поле.
8. Запрещённые слова из constraints не должны появляться нигде.

Не объясняй решения, не пиши преамбулу. ТОЛЬКО валидный JSON.
```

## User message template

```
PRODUCT: {{brief.product}}
GOAL: {{brief.goal}}
CHANNEL: {{brief.channel}}
TONE HINTS: {{tone_hints_or_none}}
CONSTRAINTS: {{constraints_block}}
CTA PREFERENCE: {{cta_preference_or_none}}

ЦЕЛЕВАЯ ПЕРСОНА (priority):
- segment: {{persona.segment}}
- age_range: {{persona.age_range}}
- pain_points: {{persona.pain_points}}
- motivations: {{persona.motivations}}
- objections: {{persona.objections}}
- communication_style: {{persona.communication_style}}

{{anti_bias_block}}

{{revise_block}}

Верни ТОЛЬКО валидный JSON по схеме:
{
  "candidates": [
    {"slogan": str, "body": str, "cta": str, "hook_angle": str},
    ...
  ]
}
```

## A/B anti-bias addendum

Вставляется в `anti_bias_block`, если в state есть `prior_variant`:

```
A/B ANTI-BIAS — это вариант B. Предыдущий A-вариант использовал:
- slogan: "{{prior_variant.slogan}}"
- hook_angle: {{prior_variant.hook_angle}}

В B-варианте ОБЯЗАТЕЛЬНО:
- ни один candidate не использует hook_angle="{{prior_variant.hook_angle}}";
- избегай той же смысловой рамки (если A был про скорость — B про надёжность/деньги/комьюнити);
- стиль формулировок должен ощущаться концептуально иным, не косметической перефразировкой.
```

## Revise addendum

Вставляется в `revise_block`, если это revise-итерация (revise_round > 0):

```
REVISE-ИТЕРАЦИЯ {{revise_round}}/2. Предыдущие кандидаты получили слабые verdict'ы от персоны со следующими friction'ами:
{{frictions_bullets}}

Сгенерируй новые кандидаты, которые СНИМАЮТ эти friction'ы. Не повторяй прошлые формулировки.
```

## Model-specific notes

### GLM-5.1
- **thinking=False обязателен.** На input >= 10K tokens c thinking=True модель сжигает бюджет в `reasoning_content` → `content=None`. Для creative-ads brief обычно < 5K, но дисциплина важна.
- temperature=0.8 — нужна diversity по hook_angle.

### DeepSeek-V4-Pro
- Чуть длиннее ответы при той же `max_tokens`. Жёсткие лимиты в prompt'е спасают.

## Validation

- `schema_ok` >= 95%
- `hook_diversity` (uniq hook_angle ≥ min(N, 3)) >= 90%
- `length_compliance` (slogan/body/cta в лимитах) 100% — иначе retry-with-feedback
- `must_include_compliance` 100%
- `forbidden_compliance` 0% запрещённых слов

## Changelog

- v0.2.0 (2026-06-04) — M2-ready: явные секции System/User/anti-bias/revise (плейсхолдеры рендерятся нодой, без Jinja `{% if %}`).
- v0.1.0 (2026-06-04) — skeleton.
