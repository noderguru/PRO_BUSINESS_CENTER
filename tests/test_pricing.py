from decimal import Decimal

import pytest

from app.errors import UnknownModelError
from app.services.pricing import PricingService, Usage


def test_cost_splits_cached_and_fresh_prompt_tokens(db):
    # 800 свіжих * 0.15/1M + 200 кешованих * 0.075/1M = 0.000135
    cost = PricingService(db).cost_for(
        Usage(prompt_tokens=1000, completion_tokens=500, cached_prompt_tokens=200), "gpt-4o-mini"
    )
    assert cost.prompt_cost_usd == Decimal("0.00013500")
    assert cost.completion_cost_usd == Decimal("0.00030000")
    assert cost.total_cost_usd == Decimal("0.00043500")
    assert cost.currency == "USD"


def test_cached_tokens_are_not_billed_twice(db):
    """Регресія: cached_tokens входять у prompt_tokens, а не додаються зверху."""
    pricing = PricingService(db)
    without = pricing.cost_for(Usage(1000, 0, 0), "gpt-4o-mini").total_cost_usd
    with_cache = pricing.cost_for(Usage(1000, 0, 1000), "gpt-4o-mini").total_cost_usd
    assert with_cache < without
    assert with_cache == Decimal("0.00007500")  # 1000 * 0.075/1M


def test_snapshot_keeps_applied_prices(db):
    cost = PricingService(db).cost_for(Usage(10, 10), "gpt-5.6-terra")
    assert cost.pricing_snapshot["model"] == "gpt-5.6-terra"
    assert cost.pricing_snapshot["input_usd_per_1m"] == "2.00000000"


def test_unknown_model_never_costs_zero(db):
    with pytest.raises(UnknownModelError):
        PricingService(db).cost_for(Usage(100, 100), "gpt-does-not-exist")


def test_zero_usage_is_zero_cost(db):
    assert PricingService(db).cost_for(Usage(0, 0), "gpt-4o-mini").total_cost_usd == Decimal("0")
