"""Запити до БД. ORM-логіки в роутах немає."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.errors import SessionNotFoundError
from app.models import Message, Session, UsageRecord


def create_session(db: DbSession, *, title, model, system_prompt) -> Session:
    session = Session(title=title, model=model, system_prompt=system_prompt)
    db.add(session)
    db.flush()
    return session


def get_session(db: DbSession, session_id: uuid.UUID) -> Session:
    session = db.get(Session, session_id)
    if session is None:
        raise SessionNotFoundError(details={"session_id": str(session_id)})
    return session


def lock_session(db: DbSession, session_id: uuid.UUID) -> Session:
    """SELECT ... FOR UPDATE: серіалізує паралельні повідомлення в одну сесію."""
    session = db.execute(
        select(Session).where(Session.id == session_id).with_for_update()
    ).scalar_one_or_none()
    if session is None:
        raise SessionNotFoundError(details={"session_id": str(session_id)})
    if session.status != "active":
        raise SessionNotFoundError(
            "Session is archived", details={"session_id": str(session_id), "status": session.status}
        )
    return session


def history_pairs(db: DbSession, session_id: uuid.UUID) -> list[tuple[str, str]]:
    rows = db.execute(
        select(Message.role, Message.content)
        .where(Message.session_id == session_id)
        .order_by(Message.seq)
    ).all()
    return [(r.role, r.content) for r in rows]


def next_seq(db: DbSession, session_id: uuid.UUID) -> int:
    current = db.execute(
        select(func.max(Message.seq)).where(Message.session_id == session_id)
    ).scalar()
    return (current or 0) + 1


def add_message(db: DbSession, *, session_id: uuid.UUID, seq: int, role: str, content: str) -> Message:
    message = Message(session_id=session_id, seq=seq, role=role, content=content)
    db.add(message)
    db.flush()
    return message


def add_usage_record(db: DbSession, *, session_id, message_id, model, usage, cost, response_id,
                     latency_ms) -> UsageRecord:
    record = UsageRecord(
        session_id=session_id,
        message_id=message_id,
        model=model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cached_prompt_tokens=usage.cached_prompt_tokens,
        total_tokens=usage.total_tokens,
        prompt_cost_usd=cost.prompt_cost_usd,
        completion_cost_usd=cost.completion_cost_usd,
        total_cost_usd=cost.total_cost_usd,
        pricing_snapshot=cost.pricing_snapshot,
        provider_response_id=response_id,
        latency_ms=latency_ms,
    )
    db.add(record)
    db.flush()
    return record


def bump_session_totals(session: Session, *, usage, cost, added_messages: int) -> None:
    session.message_count += added_messages
    session.total_prompt_tokens += usage.prompt_tokens
    session.total_completion_tokens += usage.completion_tokens
    session.total_cost_usd += cost.total_cost_usd
