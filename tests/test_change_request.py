"""Крок 4, change request: reset сесії та вибір моделі на рівні повідомлення."""

from decimal import Decimal

from sqlalchemy import func, select

from app.models import Message, UsageRecord


def _create(client, **kwargs):
    response = client.post("/sessions", json=kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def test_reset_keeps_session_id_and_clears_active_context(client, db):
    session_id = _create(client, system_prompt="ти асистент")["id"]
    client.post(f"/sessions/{session_id}/messages", json={"content": "мене звати Олег"})

    reset = client.post(f"/sessions/{session_id}/reset")
    assert reset.status_code == 200, reset.text
    assert reset.json()["id"] == session_id  # той самий ID
    assert reset.json()["generation"] == 1

    detail = client.get(f"/sessions/{session_id}").json()
    assert detail["messages"] == []
    assert detail["message_count"] == 0
    assert Decimal(detail["total_cost_usd"]) == 0
    assert detail["total_prompt_tokens"] == 0

    # архів: повідомлення і облік витрат лишилися в БД, просто не в активному контексті
    assert db.execute(select(func.count()).select_from(Message)).scalar() == 2
    assert db.execute(select(func.count()).select_from(UsageRecord)).scalar() == 1


def test_after_reset_model_does_not_see_previous_context(client, fake_llm):
    session_id = _create(client, system_prompt="ти асистент")["id"]
    client.post(f"/sessions/{session_id}/messages", json={"content": "мене звати Олег"})
    client.post(f"/sessions/{session_id}/reset")
    client.post(f"/sessions/{session_id}/messages", json={"content": "як мене звати?"})

    payload = [m["content"] for m in fake_llm.calls[-1]]
    assert "мене звати Олег" not in payload  # старий контекст не поїхав у провайдера
    assert payload == ["ти асистент", "як мене звати?"]  # system-промпт reset не чіпає

    detail = client.get(f"/sessions/{session_id}").json()
    assert [m["content"] for m in detail["messages"]] == ["як мене звати?", "відповідь моделі"]
    assert detail["message_count"] == 2


def test_reset_starts_cost_from_zero_and_counts_again(client):
    session_id = _create(client)["id"]
    client.post(f"/sessions/{session_id}/messages", json={"content": "привіт"})
    client.post(f"/sessions/{session_id}/reset")
    client.post(f"/sessions/{session_id}/messages", json={"content": "ще раз привіт"})

    detail = client.get(f"/sessions/{session_id}").json()
    # рівно одна вартість після reset, а не сума з попереднім контекстом
    assert Decimal(detail["total_cost_usd"]) == Decimal("0.00076400")


def test_message_model_overrides_session_default_and_drives_pricing(client, db, fake_llm):
    session_id = _create(client)["id"]  # default: gpt-5.6-luna

    response = client.post(
        f"/sessions/{session_id}/messages",
        json={"content": "привіт", "model": "gpt-5.6-terra"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert fake_llm.models[-1] == "gpt-5.6-terra"  # у провайдера пішла саме ця модель
    assert body["usage"]["model"] == "gpt-5.6-terra"
    # terra рівно в 10 разів дорожча за luna на тому самому usage
    assert Decimal(body["usage"]["total_cost_usd"]) == Decimal("0.00764000")

    record = db.execute(select(UsageRecord)).scalar_one()
    assert record.model == "gpt-5.6-terra"
    assert record.pricing_snapshot["model"] == "gpt-5.6-terra"
    # default сесії не змінився
    assert client.get(f"/sessions/{session_id}").json()["model"] == "gpt-5.6-luna"


def test_message_without_model_uses_session_default(client, fake_llm):
    session_id = _create(client, model="gpt-4o-mini")["id"]
    body = client.post(f"/sessions/{session_id}/messages", json={"content": "привіт"}).json()

    assert fake_llm.models[-1] == "gpt-4o-mini"
    assert body["usage"]["model"] == "gpt-4o-mini"


def test_unsupported_model_is_rejected_before_calling_provider(client, db, fake_llm):
    session_id = _create(client)["id"]

    response = client.post(
        f"/sessions/{session_id}/messages", json={"content": "привіт", "model": "gpt-nope"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_MODEL"
    assert response.json()["error"]["details"]["model"] == "gpt-nope"

    assert fake_llm.calls == []  # запит до провайдера не пішов
    assert db.execute(select(func.count()).select_from(Message)).scalar() == 0


def test_reset_of_unknown_session_is_404(client):
    missing = "11111111-1111-1111-1111-111111111111"
    response = client.post(f"/sessions/{missing}/reset")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"
