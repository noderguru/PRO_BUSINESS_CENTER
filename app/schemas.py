import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

MAX_CONTENT_LEN = 32_000

# гроші назовні — рядок з фіксованими 8 знаками, без 0E-8 і без float
Money = Annotated[Decimal, PlainSerializer(lambda v: f"{v:.8f}", return_type=str)]


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=128)
    system_prompt: str | None = Field(default=None, max_length=MAX_CONTENT_LEN)


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    title: str | None
    model: str
    system_prompt: str | None
    status: str
    message_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost_usd: Money
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    role: str
    content: str
    created_at: datetime


class SessionDetail(SessionOut):
    messages: list[MessageOut]


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_CONTENT_LEN)

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class UsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int
    total_tokens: int
    prompt_cost_usd: Money
    completion_cost_usd: Money
    total_cost_usd: Money
    currency: str


class SessionTotals(BaseModel):
    message_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost_usd: Money


class SendMessageResponse(BaseModel):
    message: MessageOut
    usage: UsageOut
    session_totals: SessionTotals
    context_truncated: bool
