# PRO BUSINESS CENTER — AI Chat Sessions Service

Backend-сервіс для окремих чат-сесій з OpenAI: історія діалогу в PostgreSQL, передача
попереднього контексту в кожен наступний запит, облік токенів і накопиченої вартості сесії.

**Стек:** Python 3.11 · FastAPI · SQLAlchemy 2.0 + Alembic · PostgreSQL 16 · pytest

Архітектурний план — [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## Запуск

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt        # requirements.txt — тільки runtime

createdb pbc_chat                          # або власна база
cp .env.example .env                       # вписати OPENAI_API_KEY і DATABASE_URL

alembic upgrade head                       # схема + seed цін
uvicorn app.main:app --reload
```

Документація OpenAPI — `http://localhost:8000/docs`.

### Змінні оточення

| змінна | за замовчуванням | нотатки |
|---|---|---|
| `OPENAI_API_KEY` | — | обов'язкова, без неї застосунок не стартує |
| `OPENAI_BASE_URL` | порожньо | порожньо → `api.openai.com`. Непорожнє значення передається в SDK як `base_url` |
| `OPENAI_DEFAULT_MODEL` | `gpt-5.6-luna` | модель сесії, якщо не передана явно |
| `DATABASE_URL` | — | обов'язкова |
| `CONTEXT_MAX_MESSAGES` | `20` | скільки останніх повідомлень іде в контекст |
| `CONTEXT_MAX_INPUT_TOKENS` | `8000` | ліміт, після якого найстаріші пари обрізаються. Контекст самої моделі значно більший — це стеля вартості запиту, а не технічна межа |

---

## API

| метод | шлях | призначення |
|---|---|---|
| `POST` | `/sessions` | створити сесію → `201` |
| `POST` | `/sessions/{id}/messages` | надіслати повідомлення (`model` необов'язковий), отримати відповідь, usage і вартість |
| `POST` | `/sessions/{id}/reset` | почати чистий контекст, зберігши той самий session ID |
| `GET` | `/sessions/{id}` | сесія, **активна історія** і накопичена вартість контексту |
| `GET` | `/health` | liveness + пінг БД |

### Приклади

**1. Створити сесію**

```bash
curl -s -X POST localhost:8000/sessions \
  -H 'Content-Type: application/json' \
  -d '{"title":"демо","model":"gpt-5.6-luna","system_prompt":"Ти лаконічний асистент."}'
```

```json
{"id":"6f1d…","model":"gpt-5.6-luna","status":"active","message_count":0,
 "total_prompt_tokens":0,"total_completion_tokens":0,"total_cost_usd":"0.00000000"}
```

**2. Надіслати повідомлення**

```bash
SESSION=6f1d…
curl -s -X POST localhost:8000/sessions/$SESSION/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"Мене звати Олег. Запам'\''ятай."}'
```

```json
{"message":{"seq":2,"role":"assistant","content":"Запам'ятав, Олеже."},
 "usage":{"prompt_tokens":24,"completion_tokens":8,"cached_prompt_tokens":0,
          "total_tokens":32,"model":"gpt-5.6-luna","prompt_cost_usd":"0.00000480",
          "completion_cost_usd":"0.00000960","total_cost_usd":"0.00001440","currency":"USD"},
 "session_totals":{"message_count":2,"total_cost_usd":"0.00001440"},
 "context_truncated":false}
```

**3. Історія і накопичена вартість**

```bash
# друге повідомлення спирається на контекст першого
curl -s -X POST localhost:8000/sessions/$SESSION/messages \
  -H 'Content-Type: application/json' -d '{"content":"Як мене звати?"}'

curl -s localhost:8000/sessions/$SESSION
```

Відповідь містить повідомлення активного контексту в порядку `seq` і `total_cost_usd` —
суму вартості обмінів цього контексту.

**4. Обрати модель для окремого повідомлення**

```bash
curl -s -X POST localhost:8000/sessions/$SESSION/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"Розберись зі складним питанням","model":"gpt-5.6-terra"}'
```

Без поля `model` береться модель сесії. Вартість завжди рахується за тарифом тієї моделі,
яка реально відпрацювала — вона ж повертається в `usage.model` і пишеться в `usage_records`.
Невідома модель відхиляється **до** виклику провайдера:

```bash
curl -s -X POST localhost:8000/sessions/$SESSION/messages \
  -H 'Content-Type: application/json' -d '{"content":"привіт","model":"gpt-nope"}'
```

```json
{"error":{"code":"UNKNOWN_MODEL",
          "message":"Model is not present in the pricing catalog",
          "details":{"model":"gpt-nope"}}}
```

**5. Скинути контекст, не змінюючи session ID**

```bash
curl -s -X POST localhost:8000/sessions/$SESSION/reset
curl -s localhost:8000/sessions/$SESSION
```

Після reset: `id` той самий, `generation` збільшився на 1, `messages` порожній,
`total_cost_usd` активного контексту — `0.00000000`. Наступне повідомлення йде в модель
без попередньої історії; `system_prompt` сесії reset не чіпає.

Попередні повідомлення **не видаляються**, вони лишаються в БД як архів попереднього
покоління — на них посилаються `usage_records`, джерело правди по витратах. Тому вартість
за весь час життя сесії лишається обчислюваною:

```sql
SELECT SUM(total_cost_usd) FROM usage_records WHERE session_id = '...';
```

---

## Модель і тарифи

Модель тесту — **`gpt-5.6-luna`**. Розрахунок ведеться за публічним прайсом, зафіксованим
у `pricing_seed.json` на дату реалізації (26.08.2026) і залитим у `model_pricing`
seed-міграцією.

| модель | input / 1M | cached input / 1M | output / 1M |
|---|---|---|---|
| **`gpt-5.6-luna`** (за замовчуванням) | **$0.20** | **$0.02** | **$1.20** |
| `gpt-5.6-terra` | $2.00 | $0.20 | $12.00 |
| `gpt-4o-mini` | $0.15 | $0.075 | $0.60 |

Ціни живуть у БД з `effective_from`, а не в коді: нова модель або новий тариф — це рядок
у таблиці, без деплою. Кожен `usage_records` зберігає `pricing_snapshot` — ціни, за якими
порахували, тому зміна тарифів не переписує історичну вартість.

Формула (усе в `Decimal`, квантування до 8 знаків, `ROUND_HALF_UP`; `float` не використовується):

```
cost = (prompt_tokens - cached_tokens)/1e6 * input_price
     + cached_tokens/1e6                   * cached_price
     + completion_tokens/1e6               * output_price
```

> **Відхилення від плану.** У `ARCHITECTURE.md` §4 формула брала `prompt_tokens` цілком
> **і** `cached_tokens` зверху. Провайдер віддає `cached_tokens` як частину `prompt_tokens`,
> тому кешовані токени оплачувалися б двічі. Виправлено: за повним тарифом іде тільки
> некешований залишок. Закріплено тестом `test_cached_tokens_are_not_billed_twice`.

### Категорії usage: що враховано і що ні

Враховуються `prompt_tokens`, `completion_tokens` і `prompt_tokens_details.cached_tokens`.

**`gpt-5.6-luna` — reasoning-модель**, тому у відповіді приходить іще
`completion_tokens_details.reasoning_tokens`. Окремою ставкою вони **не** тарифікуються
і окремим полем не показуються — і не мусять: reasoning-токени входять до
`completion_tokens`, тобто вже оплачені за output-ставкою в межах наведеної формули.
Розбивка «скільки з відповіді пішло на міркування» тут не виводиться; якщо знадобиться —
це поле в `usage_records`, не зміна формули. Закріплено тестом
`test_reasoning_tokens_stay_inside_completion_tokens`.

Свідомо не враховуються категорії, які для обраної моделі не виникають: аудіо-токени,
`rejected_prediction_tokens`, окремі ставки за запис у кеш (`input_cache_write`) і за
web-search виклики. Так само не підтримується **ступінчастий тариф** — у провайдера
ціна зростає для запитів довших за 272k токенів вхідного контексту; при
`CONTEXT_MAX_INPUT_TOKENS=8000` цей поріг недосяжний, а підтримка вимагала б додати
в `model_pricing` межу застосування ставки.

Якщо провайдер узагалі не поверне блок `usage`, у базу піде явний нуль із записом у лог,
а не вигадані токени.

---

## Як влаштовано

```
app/
  main.py             застосунок, X-Request-ID, /health
  config.py           pydantic-settings
  db.py               engine, sessionmaker, залежність FastAPI
  models.py           sessions, messages, usage_records, model_pricing
  schemas.py          Pydantic-контракти
  errors.py           доменні винятки, єдиний конверт помилки
  api/sessions.py     роути
  services/
    chat.py           сценарій обміну цілком
    llm.py            єдина точка, що знає про OpenAI SDK
    pricing.py        довідник цін і розрахунок вартості
    context.py        збірка та обрізання контексту
    repository.py     запити до БД
migrations/           alembic + seed цін
tests/                pytest, клієнт провайдера замоканий
```

**Контекст.** Кожне нове повідомлення йде в модель разом із системним промптом і попередньою
історією сесії, а не саме по собі. Якщо історія перевищує ліміт — відкидаються найстаріші
пари, системний промпт не обрізається ніколи, факт обрізання повертається полем
`context_truncated`.

**Транзакційність.** Повідомлення користувача, відповідь асистента, `usage_records` та
інкремент агрегатів сесії комітяться однією транзакцією. Якщо виклик провайдера впав —
не зберігається нічого, сесія лишається консистентною, запит можна повторити.
`SELECT ... FOR UPDATE` по сесії серіалізує паралельні повідомлення і дає монотонний `seq`.

**Джерело правди по грошах** — `usage_records`. Агрегати в `sessions` існують, щоб не робити
`SUM()` на кожне читання; обидва пишуться в одній транзакції і зводяться до останнього знаку
(перевіряється тестом).

---

## Помилки

Уніфікований конверт `{"error": {"code", "message", "details"}}` і `X-Request-ID` у кожній
відповіді. Назовні не летить ні stacktrace, ні тіло помилки провайдера, ні фрагмент ключа.

| ситуація | код | HTTP |
|---|---|---|
| сесія не існує або архівована | `SESSION_NOT_FOUND` | 404 |
| порожній / надто довгий `content`, битий UUID | `VALIDATION_ERROR` | 422 |
| модель не в довіднику цін | `UNKNOWN_MODEL` | 400 |
| контекст не влазить у ліміт | `CONTEXT_TOO_LONG` | 400 |
| rate limit провайдера | `LLM_RATE_LIMITED` | 429 + `Retry-After` |
| таймаут / мережевий збій | `LLM_UNAVAILABLE` | 504 |
| невалідний ключ провайдера | `LLM_CONFIG_ERROR` | 502 |
| БД недоступна | `STORAGE_UNAVAILABLE` | 503 |

Ретраї — `tenacity`, експоненційний backoff з jitter, максимум 2 повтори, лише на
429/5xx/timeout. На помилках валідації ретраїв немає.

---

## Тести

```bash
pytest -q          # 27 тестів, жоден не ходить у мережу
ruff check app tests
```

Потрібна база `pbc_chat_test` (`createdb pbc_chat_test`). Якщо локальні креденшели інші —
`TEST_DATABASE_URL=postgresql+psycopg://user:pass@host/db pytest -q`. Клієнт провайдера підмінений
фікстурою, `usage` підставляється вручну. Покрито: точність `Decimal` і подвійна оплата
кешу, обрізання контексту зі збереженням системного промпту, happy-path обміну зі зведенням
агрегатів, передача історії в другий запит, відкат при збої провайдера, маппінг помилок,
нормалізація usage для reasoning-моделі.

Change request Кроку 4 (`tests/test_change_request.py`): reset зберігає session ID і чистить
активний контекст, архів повідомлень і `usage_records` після reset лишається в БД, у провайдера
не їде старий контекст, вартість після reset рахується з нуля, модель із запиту перекриває
default сесії й визначає тариф, невідома модель відхиляється до мережевого виклику.

---

## Свідомо не зроблено (таймбокс Кроку 3)

Обов'язковий функціонал Кроку 3 закритий повністю. Поза scope залишено те, чого крок
не вимагав:

- **`GET /sessions` (список) і `DELETE /sessions/{id}` (архівування)** — не входять до
  мінімального API. Поле `status` і перевірка `active` у коді вже є, роут — рядок роботи.
- **Окремий `GET /sessions/{id}/messages` з курсорною пагінацією** — історія віддається
  цілком у `GET /sessions/{id}`, як вимагає крок. Для дуже довгих сесій знадобиться пагінація.
- **`GET /sessions/{id}/usage` і `GET /models`** — розбивка по обмінах і читання довідника
  цін назовні. Дані для них у БД є.
- **Streaming-відповіді** — при streaming `usage` приходить окремим чанком і облік
  ускладнюється; вимога кроку — показати вартість, не UX.
- **Авторизація, rate limiting самого сервісу, Docker, CI** — крок прямо звільняє від
  production-інфраструктури.
- **Ліміти на сесію** (стеля вартості чи токенів з відмовою) — не було у вимогах.

Припущення зафіксовані в [`ARCHITECTURE.md`](./ARCHITECTURE.md) §8.

---

Секрети в репозиторій не комітяться — тільки `.env.example`. Вхідні .docx від замовника
лежать у `docs/` і виключені через `.gitignore`.
