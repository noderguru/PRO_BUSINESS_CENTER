# Крок 2 — Архітектура та план реалізації

**Сервіс:** backend для окремих чат-сесій з OpenAI з історією діалогу в БД та обліком
токенів і накопиченої вартості сесії.
**Стек:** Python 3.11 · FastAPI · SQLAlchemy 2.0 + Alembic · PostgreSQL 16 · pytest.

---

## 1. Модель даних

### `sessions`
| поле | тип | нотатки |
|---|---|---|
| `id` | UUID PK | |
| `title` | text null | необов'язкова назва |
| `model` | text | модель сесії, default `gpt-4o-mini` |
| `system_prompt` | text null | фіксується на створенні сесії |
| `status` | text | `active` / `archived` |
| `message_count` | int | денормалізований counter |
| `total_prompt_tokens` | bigint | |
| `total_completion_tokens` | bigint | |
| `total_cost_usd` | numeric(18,8) | накопичена вартість сесії |
| `created_at` / `updated_at` | timestamptz | |

### `messages`
`id` UUID PK · `session_id` UUID FK→sessions ON DELETE CASCADE · `seq` int (порядок у сесії,
UNIQUE(`session_id`,`seq`)) · `role` enum(`system`,`user`,`assistant`) · `content` text ·
`created_at` timestamptz.
Індекс: `(session_id, seq)` — за ним читається і історія, і контекст.

### `usage_records` — 1:1 з assistant-повідомленням
`id` PK · `message_id` FK UNIQUE · `session_id` FK (денормалізація для швидких сум) ·
`model` · `prompt_tokens` · `completion_tokens` · `cached_prompt_tokens` · `total_tokens` ·
`prompt_cost_usd` · `completion_cost_usd` · `total_cost_usd` numeric(18,8) ·
`pricing_snapshot` jsonb (ціни, за якими порахували) · `openai_response_id` · `latency_ms` ·
`created_at`.

### `model_pricing` — довідник цін
`id` PK · `model` · `input_usd_per_1m` · `cached_input_usd_per_1m` · `output_usd_per_1m` ·
`currency` · `effective_from` · `effective_to` null. UNIQUE(`model`,`effective_from`).
Історія цін: актуальний рядок = `effective_from <= now() < coalesce(effective_to, 'infinity')`.
Наповнюється seed-міграцією з `pricing_seed.json`.

**Чому і таблиця, і агрегат у `sessions`:** `usage_records` — джерело правди (можна перерахувати
`SUM()` будь-коли), агрегат у `sessions` — щоб `GET /sessions` не робив агрегацію на кожен запит.
Обидва пишуться в одній транзакції.

---

## 2. REST API

| метод | шлях | призначення |
|---|---|---|
| `POST` | `/sessions` | створити сесію `{title?, model?, system_prompt?}` → `201` |
| `GET` | `/sessions` | список + агрегати, пагінація `limit`/`cursor` |
| `GET` | `/sessions/{id}` | сесія + токени + `total_cost_usd` |
| `DELETE` | `/sessions/{id}` | архівувати (soft delete) |
| `POST` | `/sessions/{id}/messages` | надіслати повідомлення `{content}` → відповідь асистента + usage цього обміну + оновлені тотали сесії |
| `GET` | `/sessions/{id}/messages` | історія, `order=asc`, пагінація по `seq` |
| `GET` | `/sessions/{id}/usage` | розбивка по обмінах + сумарно |
| `GET` | `/models` | доступні моделі та їх поточні ціни |
| `GET` | `/health` | liveness + пінг БД |

Схеми запитів/відповідей — Pydantic v2, тому OpenAPI-документація генерується автоматично
(`/docs`).

---

## 3. Як нове повідомлення отримує контекст

`POST /sessions/{id}/messages` → `ChatService.send()`:

1. `SELECT ... FOR UPDATE` по сесії — серіалізує паралельні повідомлення в одну сесію
   і дає монотонний `seq`.
2. `ContextBuilder` читає історію `(role, content)` за `(session_id, seq)` і формує payload:
   `system_prompt` (завжди) + останні `CONTEXT_MAX_MESSAGES` повідомлень + новий user-запит.
3. Якщо оцінка вхідних токенів (`tiktoken`) перевищує `CONTEXT_MAX_INPUT_TOKENS` — найстаріші
   пари обрізаються, `system` не обрізається ніколи. Факт обрізання йде в лог і в поле
   `truncated` відповіді.
4. `LLMClient.chat()` → `PricingService.cost_for(usage, model)` → запис `user`-повідомлення,
   `assistant`-повідомлення, `usage_records` та інкремент агрегатів **однією транзакцією**.

---

## 4. Usage і вартість

Джерело — `response.usage` від OpenAI (`prompt_tokens`, `completion_tokens`,
`prompt_tokens_details.cached_tokens`). Власного підрахунку токенів для біллінгу немає;
`tiktoken` використовується лише для передоцінки перед обрізанням контексту.

```
cost = prompt_tokens/1e6 * input_price
     + cached_tokens/1e6 * cached_price      # якщо провайдер віддав cached
     + completion_tokens/1e6 * output_price
```
Усе в `Decimal`, квантування до 8 знаків, `ROUND_HALF_UP`. `float` не використовується.
`pricing_snapshot` зберігає застосовані ціни, тому зміна тарифів не переписує історичну
вартість.

---

## 5. Model-specific pricing без «зашивання» у роут

Ціни живуть у `model_pricing` (БД) + `app/services/pricing.py`:

```python
class PricingService:
    def price_for(model: str, at: datetime) -> ModelPrice   # кеш у пам'яті, TTL
    def cost_for(usage: Usage, model: str) -> CostBreakdown
```

- Роут і контролер не бачать ні коефіцієнтів, ні формули — лише `CostBreakdown`.
- Невідома модель → `UnknownModelError` → `400 UNKNOWN_MODEL`. Вартість **ніколи** не
  вважається нулем «по замовчуванню» — це головна причина тихого розходження грошей.
- Нова модель або новий тариф = рядок у таблиці (`effective_from`), без деплою коду.
- Список моделей у `POST /sessions` валідується проти цього ж довідника.

---

## 6. Обробка помилок

Уніфікований конверт: `{"error": {"code": "...", "message": "...", "details": {...}}}`,
плюс `X-Request-ID` у кожній відповіді.

| ситуація | код | HTTP |
|---|---|---|
| сесія не існує / архівована | `SESSION_NOT_FOUND` | 404 |
| порожній / надто довгий `content`, битий UUID | `VALIDATION_ERROR` | 422 |
| модель не в довіднику цін | `UNKNOWN_MODEL` | 400 |
| контекст не влазить у ліміт моделі | `CONTEXT_TOO_LONG` | 400 |
| OpenAI rate limit | `LLM_RATE_LIMITED` | 429 + `Retry-After` |
| OpenAI timeout / мережа | `LLM_UNAVAILABLE` | 504 / 503 |
| невалідний OpenAI ключ | `LLM_CONFIG_ERROR` | 502 (деталі — лише в лог) |
| БД недоступна, deadlock | `STORAGE_UNAVAILABLE` | 503 |

- Ретраї: `tenacity`, експоненційний backoff з jitter, максимум 2 повтори, **лише** на
  429/5xx/timeout. Ретраїв на 4xx-валідації немає.
- Транзакційність: user-повідомлення, відповідь і usage комітяться разом. Якщо виклик OpenAI
  впав — не зберігається нічого, сесія лишається консистентною, клієнт може повторити запит.
- Назовні не летить ні stacktrace, ні тіло помилки провайдера, ні фрагменти ключа.

---

## 7. Розбиття на модулі

```
app/
  main.py             # створення app, роутери, exception handlers, request-id middleware
  config.py           # pydantic-settings, читання .env
  db.py               # engine, sessionmaker, FastAPI dependency
  models.py           # SQLAlchemy ORM
  schemas.py          # Pydantic request/response
  errors.py           # доменні винятки + handlers + коди
  api/
    sessions.py       # роути сесій та повідомлень
    models.py         # GET /models
  services/
    chat.py           # оркестрація обміну (єдине місце, що знає весь сценарій)
    llm.py            # OpenAI SDK, ретраї, нормалізований Usage DTO
    pricing.py        # довідник цін + розрахунок вартості
    context.py        # збірка та обрізання контексту
    repository.py     # запити до БД, без ORM-логіки в роутах
migrations/           # alembic + seed цін
tests/                # pytest, OpenAI-клієнт замоканий
```
Межа проведена так, щоб `services/llm.py` можна було підмінити іншим провайдером, а
`services/pricing.py` — оновити без дотику до API-шару.

---

## 8. Припущення

1. Сервіс однокористувацький, авторизації немає — Крок 1 прямо це дозволяє. `user_id` не
   вводиться, але `sessions` легко розширюється колонкою.
2. Відповіді **не** streaming: при streaming `usage` приходить окремим чанком і облік
   ускладнюється, а вимога етапу — показати вартість, не UX.
3. Валюта — USD, як у прайсі OpenAI. Конвертації немає.
4. Одна сесія = одна модель. Зміна моделі посеред сесії заборонена — інакше «вартість сесії»
   стає сумою різних тарифів без явної точки перемикання.
5. Ціни в seed беруться з офіційного прайсу OpenAI на дату реалізації і фіксуються в міграції.
6. Ліміти («не більше N токенів на сесію») не реалізуються — не було у вимогах.
7. Rate limiting самого сервісу і кешування відповідей — поза скоупом.

## 9. Питання до уточнення

1. Який перелік моделей має бути доступний — достатньо `gpt-4o-mini` + `gpt-4o`, чи потрібен
   ширший довідник?
2. Streaming-відповіді потрібні на цьому етапі, чи достатньо синхронних?
3. Вартість показувати тільки в USD, чи ще в іншій валюті?
4. Чи потрібен hard-limit вартості/токенів на сесію з відмовою при перевищенні?
5. Чи очікується Docker Compose для запуску, чи достатньо локального Postgres + `.env.example`?

## 10. Дедлайн, ETA, ризики

- Початковий фінальний дедлайн **не змінився**: 26.08, 17:00.
- ETA на наступний технічний етап (Крок 3, базова реалізація): **2 години 30 хвилин** —
  каркас і міграції ~40 хв, чат-цикл ~40 хв, usage/pricing ~35 хв, помилки і тести ~35 хв.
- Ризики:
  1. **Тарифи.** Прайс OpenAI змінюється; тому ціни в довіднику з `effective_from`, а не в коді.
  2. **Ліміт 5 годин.** Docker, авторизація і CI не робляться, бо крок їх не вимагає.
  3. **Зміна вимог на Кроці 4.** Найдешевші до зміни місця — `pricing.py` і `context.py`;
     тому вони ізольовані від API-шару.
  4. **Квота/доступність OpenAI-ключа** — тести не залежать від мережі, клієнт мокається.
