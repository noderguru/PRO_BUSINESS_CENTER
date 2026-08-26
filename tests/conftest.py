import json
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

TEST_DB_URL = "postgresql+psycopg://pbc:pbc@localhost:5432/pbc_chat_test"


@pytest.fixture(scope="session", autouse=True)
def _env(monkeypatch_session=None):
    import os

    os.environ["DATABASE_URL"] = TEST_DB_URL
    os.environ["OPENAI_API_KEY"] = "sk-test-not-used"
    os.environ["OPENAI_BASE_URL"] = ""
    # тести не залежать від локального .env
    os.environ["OPENAI_DEFAULT_MODEL"] = "gpt-5.6-luna"
    os.environ["CONTEXT_MAX_MESSAGES"] = "20"
    os.environ["CONTEXT_MAX_INPUT_TOKENS"] = "8000"


@pytest.fixture(scope="session")
def engine(_env):
    from app.models import Base

    engine = create_engine(TEST_DB_URL, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    _seed_pricing(engine)
    yield engine
    Base.metadata.drop_all(engine)


def _seed_pricing(engine) -> None:
    from app.models import ModelPricing

    seed = json.loads((Path(__file__).resolve().parents[1] / "pricing_seed.json").read_text())
    with sessionmaker(bind=engine)() as db:
        for m in seed["models"]:
            db.add(
                ModelPricing(
                    id=uuid.uuid4(),
                    model=m["model"],
                    input_usd_per_1m=Decimal(m["input_usd_per_1m"]),
                    cached_input_usd_per_1m=Decimal(m["cached_input_usd_per_1m"]),
                    output_usd_per_1m=Decimal(m["output_usd_per_1m"]),
                    currency=seed["currency"],
                    effective_from=datetime.fromisoformat(seed["effective_from"]),
                )
            )
        db.commit()


@pytest.fixture
def db(engine):
    from app.models import Message, Session, UsageRecord
    from app.services.pricing import PricingService

    PricingService.reset_cache()
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        for model in (UsageRecord, Message, Session):
            session.query(model).delete()
        session.commit()
        yield session


class FakeLLM:
    """Замість SDK. Жоден тест не ходить у мережу."""

    def __init__(self, usage=None, reply="відповідь моделі", error=None):
        from app.services.pricing import Usage

        self.usage = usage or Usage(prompt_tokens=1000, completion_tokens=500, cached_prompt_tokens=200)
        self.reply = reply
        self.error = error
        self.calls: list[list[dict]] = []

    def chat(self, model: str, messages: list[dict]):
        from app.services.llm import LLMResponse

        self.calls.append(messages)
        if self.error:
            raise self.error
        return LLMResponse(
            content=self.reply, usage=self.usage, response_id="resp_fake_1", latency_ms=42
        )


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def client(engine, db, fake_llm):
    from app.api.sessions import get_llm
    from app.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_llm] = lambda: fake_llm
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
