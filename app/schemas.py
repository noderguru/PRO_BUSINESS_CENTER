import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_CONTENT_LEN = 32_000


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
    total_cost_usd: Decimal
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
    prompt_cost_usd: Decimal
    completion_cost_usd: Decimal
    total_cost_usd: Decimal
    currency: str


class SessionTotals(BaseModel):
    message_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost_usd: Decimal


class SendMessageResponse(BaseModel):
    message: MessageOut
    usage: UsageOut
    session_totals: SessionTotals
    context_truncated: bool
