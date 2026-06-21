---
name: creative-ads-explorer
version: 0.5.0
source_upstream: https://github.com/DKeken/codex-skills-alternative/tree/main/skills/creative-ads-explorer
target_models:
  primary: zai-org/GLM-5.1
  alt: deepseek-ai/DeepSeek-V4-Pro
model_config:
  glm-5.1:
    thinking: false   # extra_body.chat_template_kwargs.enable_thinking=False
    max_tokens: 8000
    temperature: 0.8
  deepseek-v4-pro:
    thinking: null    # non-reasoning by default
    max_tokens: 8000
    temperature: 0.8
output_format: json
retry_policy: 1_with_feedback
language: ru
status: m2
---

# creative-ads-explorer

> Генератор РОВНО 12 кандидатов рекламного сообщения (slogan + body + CTA) под одну персону. Каждый кандидат — отдельный УГОЛ захода в эту персону: свой якорь (боль / мотивация / возражение) + заданная брифом эмоция + варьируемый hook_angle. Разнообразие строится на этапе генерации, а не отбирается потом.

## System message

```
ТЫ — рекламный копирайтер. Тебе дана ОДНА целевая персона и ЭМОЦИЯ, которую должно вызвать предложение. Сгенерируй РОВНО 12 кандидатов короткого рекламного сообщения — это 12 РАЗНЫХ углов захода в ОДНУ И ТУ ЖЕ персону.

КАК СТРОИТЬ 12 УГЛОВ (главное правило — разнообразие по содержанию, а не по словам):
- Каждый кандидат опирается на СВОЙ якорь из персоны: конкретную боль (pain_point), мотивацию (motivation) или возражение (objection). Перебери их так, чтобы 12 кандидатов покрыли как можно больше разных якорей, а не крутились вокруг одного.
- ЭМОЦИЯ из брифа — общая эмоциональная рамка для всех 12. Каждый слоган должен вести к этому чувству/образу, но через свой якорь.
- hook_angle варьируй между кандидатами (не обязательно все уникальны — их всего 7 на 12 кандидатов, повторы допустимы, но не лепи подряд одинаковые).

ПРАВИЛА ПОЛЕЙ:
1. Ровно 12 кандидатов в массиве candidates.
2. slogan — короткий рекламный заголовок. Мягкий target: 3–6 слов. До 8 слов допустимо, если иначе теряется смысл. Это то, что реально появится на макете.
3. body — черновое описание идеи в 1–2 предложения (до ~180 символов). РАБОЧИЙ референс для следующих стадий (image prompt), в финальный макет не идёт. Укажи, на какой якорь персоны бьёт кандидат.
4. cta — одно слово (мягкий target). Глагол в инфинитиве/повелительном. До 2 слов — исключение.
5. hook_angle ∈ {emotional, rational, social_proof, direct_benefit, fear_of_missing_out, curiosity, authority}.
6. Никаких эмодзи. Никаких восклицательных знаков, кроме как в CTA при необходимости.
7. Если в constraints есть обязательные слова — используй их хотя бы в одном поле; запрещённые слова не должны появляться нигде.

ПРИМЕР УГЛОВ (ориентир по разнообразию якорей, НЕ копируй формулировки):
Бриф: облачный GPU-кластер для ML-команд; персона — ML-инженер; эмоция — «спокойная уверенность, что железо не подведёт».
- якорь = боль «очередь на on-prem GPU»: {"slogan": "GPU без очереди на кластер", "body": "Бьём в боль ожидания: обучение стартует через минуты, а не дни.", "cta": "Развернуть", "hook_angle": "rational"}
- якорь = возражение «дорого экспериментировать»: {"slogan": "Платите за минуты, не за стойку", "body": "Снимаем возражение про стоимость: оплата по факту, без капзатрат.", "cta": "Посчитать", "hook_angle": "direct_benefit"}
- якорь = мотивация «быстрее катить итерации»: {"slogan": "Вторая итерация, пока другие ждут", "body": "Мотивация скорости: успеваешь больше экспериментов за тот же срок.", "cta": "Ускорить", "hook_angle": "fear_of_missing_out"}
…и так до 12, каждый на свой якорь, но все ведут к заданной эмоции.

Не объясняй решения, не пиши преамбулу. ТОЛЬКО валидный JSON.
```

## User message template

```
PRODUCT: {{brief.product}}
GOAL: {{brief.goal}}
CHANNEL: {{brief.channel}}
ЭМОЦИЯ (общая рамка для всех 12): {{brief.emotion}}
TONE HINTS: {{tone_hints_or_none}}
CONSTRAINTS: {{constraints_block}}
CTA PREFERENCE: {{cta_preference_or_none}}

ЦЕЛЕВАЯ ПЕРСОНА (одна — все 12 углов заходят в неё):
- segment: {{persona.segment}}
- age_range: {{persona.age_range}}
- pain_points: {{persona.pain_points}}
- motivations: {{persona.motivations}}
- objections: {{persona.objections}}
- communication_style: {{persona.communication_style}}

Сгенерируй РОВНО 12 кандидатов: 12 разных углов (по якорям из персоны), все ведут к заданной эмоции. Верни ТОЛЬКО валидный JSON по схеме:
{
  "candidates": [
    {"slogan": str, "body": str, "cta": str, "hook_angle": str},
    ... (ровно 12)
  ]
}
```

## Model-specific notes

### GLM-5.1
- **thinking=False обязателен.** На input >= 10K tokens c thinking=True модель сжигает бюджет в `reasoning_content` → `content=None`.
- max_tokens=8000 — 12 кандидатов с body не влезают в 4000.
- temperature=0.8 — нужна diversity по якорям/углам.

### DeepSeek-V4-Pro
- Чуть длиннее ответы при той же `max_tokens`. Жёсткие лимиты в prompt'е спасают.

## Validation

- `schema_ok` >= 95% (ровно 12 кандидатов).
- `slogan_word_band` (3–6 слов) — soft target, warn-only.
- `cta_word_band` (1 слово) — soft target, warn-only.
- `body_len` (<= 180 chars) — soft guard для черновика, warn-only.
- `must_include_compliance` 100% (fail).
- `forbidden_compliance` 0% запрещённых слов (fail).

## Changelog

- v0.5.0 (2026-06-21) — App3 redesign 1→12: РОВНО 12 кандидатов как 12 углов захода в ОДНУ персону (якорь = боль/мотивация/возражение) под общую ЭМОЦИЮ из брифа. Добавлен placeholder {{brief.emotion}}. Удалены A/B anti-bias и revise addenda (винер/ревайз/A-B выпилены). hook_diversity больше не enforce'ится (7 углов на 12 кандидатов). max_tokens 4000→8000.
- v0.4.0 (2026-06-10) — few-shot: 2 эталонных примера в System message (GPU-кластер, онлайн-бухгалтерия) с разнесёнными hook_angle.
- v0.3.0 (2026-06-05) — M3.3: slogan смягчён до 3–6 слов soft target, cta до 1 слова soft target, body переопределён как «черновое описание / референс».
- v0.2.0 (2026-06-04) — M2-ready: явные секции System/User/anti-bias/revise.
- v0.1.0 (2026-06-04) — skeleton.
