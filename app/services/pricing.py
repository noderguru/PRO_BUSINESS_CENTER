"""Довідник цін і розрахунок вартості.

Єдине місце, де живе формула. Роути бачать лише CostBreakdown.
Гроші — тільки Decimal.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.errors import UnknownModelError
from app.models import ModelPricing

QUANT = Decimal("0.00000001")  # 8 знаків, як NUMERIC(18,8)
PER_1M = Decimal("1000000")
_CACHE_TTL_SECONDS = 60


@dataclass(frozen=True)
class Usage:
    """Нормалізований usage від провайдера.

    prompt_tokens вже включає cached_prompt_tokens — так їх віддає OpenAI.
    """

    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class ModelPrice:
    model: str
    input_usd_per_1m: Decimal
    cached_input_usd_per_1m: Decimal | None
    output_usd_per_1m: Decimal
    currency: str
    effective_from: datetime

    def as_snapshot(self) -> dict:
        return {
            "model": self.model,
            "input_usd_per_1m": str(self.input_usd_per_1m),
            "cached_input_usd_per_1m": (
                str(self.cached_input_usd_per_1m) if self.cached_input_usd_per_1m is not None else None
            ),
            "output_usd_per_1m": str(self.output_usd_per_1m),
            "currency": self.currency,
            "effective_from": self.effective_from.isoformat(),
        }


@dataclass(frozen=True)
class CostBreakdown:
    prompt_cost_usd: Decimal
    completion_cost_usd: Decimal
    total_cost_usd: Decimal
    currency: str
    pricing_snapshot: dict


def _q(value: Decimal) -> Decimal:
    return value.quantize(QUANT, rounding=ROUND_HALF_UP)


class PricingService:
    # ponytail: кеш процесу, а не Redis — довідник змінюється рідше, ніж перезапуск
    _cache: dict[str, tuple[float, ModelPrice]] = {}

    def __init__(self, db: DbSession):
        self.db = db

    def price_for(self, model: str, at: datetime | None = None) -> ModelPrice:
        at = at or datetime.now(timezone.utc)
        cached = self._cache.get(model)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        row = self.db.execute(
            select(ModelPricing)
            .where(
                ModelPricing.model == model,
                ModelPricing.effective_from <= at,
                (ModelPricing.effective_to.is_(None)) | (ModelPricing.effective_to > at),
            )
            .order_by(ModelPricing.effective_from.desc())
            .limit(1)
        ).scalar_one_or_none()

        if row is None:
            # Вартість ніколи не вважається нулем «за замовчуванням»
            raise UnknownModelError(details={"model": model})

        price = ModelPrice(
            model=row.model,
            input_usd_per_1m=row.input_usd_per_1m,
            cached_input_usd_per_1m=row.cached_input_usd_per_1m,
            output_usd_per_1m=row.output_usd_per_1m,
            currency=row.currency,
            effective_from=row.effective_from,
        )
        self._cache[model] = (time.monotonic() + _CACHE_TTL_SECONDS, price)
        return price

    def cost_for(self, usage: Usage, model: str, at: datetime | None = None) -> CostBreakdown:
        price = self.price_for(model, at)

        cached = min(usage.cached_prompt_tokens, usage.prompt_tokens)
        # cached_tokens входять у prompt_tokens, тому за повним тарифом іде лише залишок
        fresh = usage.prompt_tokens - cached
        cached_rate = (
            price.cached_input_usd_per_1m
            if price.cached_input_usd_per_1m is not None
            else price.input_usd_per_1m
        )

        prompt_cost = _q(
            Decimal(fresh) / PER_1M * price.input_usd_per_1m
            + Decimal(cached) / PER_1M * cached_rate
        )
        completion_cost = _q(Decimal(usage.completion_tokens) / PER_1M * price.output_usd_per_1m)

        return CostBreakdown(
            prompt_cost_usd=prompt_cost,
            completion_cost_usd=completion_cost,
            total_cost_usd=_q(prompt_cost + completion_cost),
            currency=price.currency,
            pricing_snapshot=price.as_snapshot(),
        )

    @classmethod
    def reset_cache(cls) -> None:
        cls._cache.clear()
