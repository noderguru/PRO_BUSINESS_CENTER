"""Нормалізація usage. Reasoning-модель — випадок моделі за замовчуванням."""

from decimal import Decimal
from types import SimpleNamespace

from app.services.llm import _normalize_usage
from app.services.pricing import PricingService, Usage


def _completion(usage):
    return SimpleNamespace(usage=usage, id="resp_1")


def test_reasoning_tokens_stay_inside_completion_tokens(db):
    """reasoning_tokens входять у completion_tokens і тарифікуються за output-ставкою."""
    usage = _normalize_usage(
        _completion(
            SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=500,  # з них 400 reasoning
                prompt_tokens_details=SimpleNamespace(cached_tokens=0),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=400),
            )
        )
    )
    assert usage.completion_tokens == 500
    cost = PricingService(db).cost_for(usage, "gpt-5.6-luna")
    assert cost.completion_cost_usd == Decimal("0.00060000")  # 500 * 1.20/1M, reasoning включно


def test_missing_usage_block_gives_explicit_zero():
    assert _normalize_usage(SimpleNamespace(usage=None, id="x")) == Usage(0, 0, 0)


def test_missing_cached_details_default_to_zero():
    usage = _normalize_usage(
        _completion(SimpleNamespace(prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None))
    )
    assert usage.cached_prompt_tokens == 0
    assert usage.total_tokens == 15
