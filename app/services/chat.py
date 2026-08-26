"""Оркестрація обміну. Єдине місце, що знає весь сценарій."""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.models import Message
from app.services import repository as repo
from app.services.context import ContextBuilder
from app.services.llm import LLMClient
from app.services.pricing import CostBreakdown, PricingService, Usage


@dataclass
class ExchangeResult:
    assistant_message: Message
    usage: Usage
    cost: CostBreakdown
    context_truncated: bool


class ChatService:
    def __init__(self, db: DbSession, settings: Settings, llm: LLMClient):
        self.db = db
        self.settings = settings
        self.llm = llm
        self.pricing = PricingService(db)
        self.context = ContextBuilder(
            max_messages=settings.context_max_messages,
            max_input_tokens=settings.context_max_input_tokens,
        )

    def send(self, session_id: uuid.UUID, content: str) -> ExchangeResult:
        # 1. лок сесії: серіалізує паралельні повідомлення і дає монотонний seq
        session = repo.lock_session(self.db, session_id)

        # 2. контекст — уся попередня історія, а не лише останнє повідомлення
        built = self.context.build(
            system_prompt=session.system_prompt,
            history=repo.history_pairs(self.db, session_id),
            new_user_content=content,
            model=session.model,
        )

        # 3. виклик провайдера. Якщо впаде — транзакція відкотиться і нічого не збережеться,
        # ponytail: лок тримається під час мережевого виклику. Ціна — паралельні повідомлення
        # в ОДНУ сесію стають у чергу. Прибрати можна оптимістичним seq з ретраєм на UNIQUE.
        response = self.llm.chat(model=session.model, messages=built.messages)

        # 4. вартість рахує лише pricing-сервіс
        cost = self.pricing.cost_for(response.usage, session.model)

        # 5. одна транзакція: user, assistant, usage_records, агрегати сесії
        seq = repo.next_seq(self.db, session_id)
        repo.add_message(self.db, session_id=session_id, seq=seq, role="user", content=content)
        assistant = repo.add_message(
            self.db, session_id=session_id, seq=seq + 1, role="assistant", content=response.content
        )
        repo.add_usage_record(
            self.db,
            session_id=session_id,
            message_id=assistant.id,
            model=session.model,
            usage=response.usage,
            cost=cost,
            response_id=response.response_id,
            latency_ms=response.latency_ms,
        )
        repo.bump_session_totals(session, usage=response.usage, cost=cost, added_messages=2)
        self.db.commit()
        self.db.refresh(assistant)
        self.db.refresh(session)

        return ExchangeResult(
            assistant_message=assistant,
            usage=response.usage,
            cost=cost,
            context_truncated=built.truncated,
        )
