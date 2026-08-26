import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session as DbSession

from app.config import Settings, get_settings
from app.db import get_db
from app.schemas import (
    MessageCreate, SendMessageResponse, SessionCreate, SessionDetail, SessionOut, SessionTotals,
    UsageOut,
)
from app.services import repository as repo
from app.services.chat import ChatService
from app.services.llm import LLMClient
from app.services.pricing import PricingService

router = APIRouter(prefix="/sessions", tags=["sessions"])


def get_llm(settings: Settings = Depends(get_settings)) -> LLMClient:
    return LLMClient(settings)


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: SessionCreate,
    db: DbSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionOut:
    model = payload.model or settings.openai_default_model
    # невідома модель відхиляється тут, а не при першому повідомленні
    PricingService(db).price_for(model)

    session = repo.create_session(
        db, title=payload.title, model=model, system_prompt=payload.system_prompt
    )
    db.commit()
    db.refresh(session)
    return SessionOut.model_validate(session)


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(session_id: uuid.UUID, db: DbSession = Depends(get_db)) -> SessionDetail:
    """Сесія, повна історія та накопичена вартість — одним відповіддю."""
    session = repo.get_session(db, session_id)
    return SessionDetail.model_validate(session)


@router.post("/{session_id}/messages", response_model=SendMessageResponse)
def send_message(
    session_id: uuid.UUID,
    payload: MessageCreate,
    db: DbSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    llm: LLMClient = Depends(get_llm),
) -> SendMessageResponse:
    result = ChatService(db, settings, llm).send(session_id, payload.content)
    session = result.assistant_message.session

    return SendMessageResponse(
        message=result.assistant_message,
        usage=UsageOut(
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            cached_prompt_tokens=result.usage.cached_prompt_tokens,
            total_tokens=result.usage.total_tokens,
            prompt_cost_usd=result.cost.prompt_cost_usd,
            completion_cost_usd=result.cost.completion_cost_usd,
            total_cost_usd=result.cost.total_cost_usd,
            currency=result.cost.currency,
        ),
        session_totals=SessionTotals.model_validate(session, from_attributes=True),
        context_truncated=result.context_truncated,
    )
