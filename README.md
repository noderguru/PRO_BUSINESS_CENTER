# PRO BUSINESS CENTER — AI Chat Sessions Service

Тестове завдання на позицію AI Engineer. Backend-сервіс для окремих чат-сесій з OpenAI:
історія діалогу в PostgreSQL, облік токенів і накопиченої вартості кожної сесії.

**Стек:** Python 3.11 · FastAPI · SQLAlchemy 2.0 + Alembic · PostgreSQL 16 · pytest

## Статус за етапами

| Крок | Що | Статус |
|---|---|---|
| 1 | Стек, старт, дедлайн | ✅ погоджено (старт 26.08 12:00, дедлайн 26.08 17:00) |
| 2 | Архітектура та план — [`ARCHITECTURE.md`](./ARCHITECTURE.md) | ✅ готово |
| 3 | Базова реалізація | ⏳ очікує на крок |
| 4 | Зміна вимог | ⏳ |
| 5 | Здача + дзвінок | ⏳ |

## Документи

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — модель даних, endpoints, pricing, обробка помилок,
  припущення, питання, ETA і ризики (результат Кроку 2).
- Планування і трекінг — Linear, workspace `pro-business-center-tt`.

## Запуск (після Кроку 3)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # вписати OPENAI_API_KEY і DATABASE_URL
alembic upgrade head
uvicorn app.main:app --reload
```

Секрети в репозиторій не комітяться — тільки `.env.example`. Вхідні .docx від замовника
лежать у `docs/` і виключені через `.gitignore`.
