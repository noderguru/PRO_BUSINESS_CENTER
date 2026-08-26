from decimal import Decimal

from sqlalchemy import func, select

from app.errors import LLMRateLimitedError, LLMUnavailableError
from app.models import Message, Session, UsageRecord


def _create(client, **kwargs):
    response = client.post("/sessions", json=kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_session_defaults_and_rejects_unknown_model(client):
    created = _create(client, title="перша", system_prompt="ти асистент")
    assert created["model"] == "gpt-4o-mini"
    assert created["status"] == "active"
    assert Decimal(created["total_cost_usd"]) == 0

    bad = client.post("/sessions", json={"model": "gpt-nope"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "UNKNOWN_MODEL"


def test_exchange_saves_history_usage_and_totals(client, db, fake_llm):
    session_id = _create(client, system_prompt="ти асистент")["id"]

    first = client.post(f"/sessions/{session_id}/messages", json={"content": "привіт"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["message"]["role"] == "assistant"
    assert body["usage"]["prompt_tokens"] == 1000
    assert Decimal(body["usage"]["total_cost_usd"]) == Decimal("0.00043500")
    assert body["session_totals"]["message_count"] == 2

    client.post(f"/sessions/{session_id}/messages", json={"content": "друге питання"})

    detail = client.get(f"/sessions/{session_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert [m["seq"] for m in detail["messages"]] == [1, 2, 3, 4]
    assert detail["message_count"] == 4
    assert detail["total_prompt_tokens"] == 2000

    # агрегат сесії сходиться з джерелом правди до останнього знаку
    summed = db.execute(
        select(func.sum(UsageRecord.total_cost_usd)).where(UsageRecord.session_id == session_id)
    ).scalar()
    assert Decimal(detail["total_cost_usd"]) == summed == Decimal("0.00087000")


def test_second_message_carries_previous_history(client, fake_llm):
    session_id = _create(client, system_prompt="ти асистент")["id"]
    client.post(f"/sessions/{session_id}/messages", json={"content": "мене звати Олег"})
    client.post(f"/sessions/{session_id}/messages", json={"content": "як мене звати?"})

    second_payload = fake_llm.calls[1]
    contents = [m["content"] for m in second_payload]
    assert contents[0] == "ти асистент"
    assert "мене звати Олег" in contents  # не лише останнє повідомлення
    assert contents[-1] == "як мене звати?"


def test_provider_failure_saves_nothing(client, db, fake_llm):
    session_id = _create(client)["id"]
    fake_llm.error = LLMUnavailableError()

    response = client.post(f"/sessions/{session_id}/messages", json={"content": "привіт"})
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "LLM_UNAVAILABLE"

    assert db.execute(select(func.count()).select_from(Message)).scalar() == 0
    assert db.execute(select(func.count()).select_from(UsageRecord)).scalar() == 0
    session = db.execute(select(Session)).scalar_one()
    assert session.message_count == 0 and session.total_cost_usd == 0


def test_rate_limit_maps_to_429_with_retry_after(client, fake_llm):
    session_id = _create(client)["id"]
    fake_llm.error = LLMRateLimitedError(retry_after=7)

    response = client.post(f"/sessions/{session_id}/messages", json={"content": "привіт"})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
    assert response.json()["error"]["code"] == "LLM_RATE_LIMITED"


def test_unknown_session_is_404(client):
    missing = "11111111-1111-1111-1111-111111111111"
    assert client.get(f"/sessions/{missing}").status_code == 404
    response = client.post(f"/sessions/{missing}/messages", json={"content": "привіт"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_input_validation(client):
    session_id = _create(client)["id"]

    blank = client.post(f"/sessions/{session_id}/messages", json={"content": "   "})
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "VALIDATION_ERROR"

    missing_field = client.post(f"/sessions/{session_id}/messages", json={})
    assert missing_field.status_code == 422
    assert missing_field.json()["error"]["code"] == "VALIDATION_ERROR"

    broken_uuid = client.get("/sessions/not-a-uuid")
    assert broken_uuid.status_code == 422


def test_every_response_carries_request_id(client):
    response = client.get("/health")
    assert response.headers["X-Request-ID"]
