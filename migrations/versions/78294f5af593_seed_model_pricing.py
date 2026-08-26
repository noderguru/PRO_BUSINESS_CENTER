"""seed model pricing

Revision ID: 78294f5af593
Revises: 7dac99b9d8d5
Create Date: 2026-08-26 11:01:37.536265

"""
from typing import Sequence, Union

import json
import uuid
from decimal import Decimal
from pathlib import Path

from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '78294f5af593'
down_revision: Union[str, None] = '7dac99b9d8d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    seed = json.loads((Path(__file__).resolve().parents[2] / "pricing_seed.json").read_text())
    effective_from = seed["effective_from"]
    rows = [
        {
            "id": str(uuid.uuid4()),
            "model": m["model"],
            "input_usd_per_1m": Decimal(m["input_usd_per_1m"]),
            "cached_input_usd_per_1m": (
                Decimal(m["cached_input_usd_per_1m"]) if m.get("cached_input_usd_per_1m") else None
            ),
            "output_usd_per_1m": Decimal(m["output_usd_per_1m"]),
            "currency": seed["currency"],
            "effective_from": effective_from,
            "effective_to": None,
        }
        for m in seed["models"]
    ]
    table = sa.table(
        "model_pricing",
        sa.column("id", postgresql.UUID(as_uuid=False)),
        sa.column("model", sa.String),
        sa.column("input_usd_per_1m", sa.Numeric(18, 8)),
        sa.column("cached_input_usd_per_1m", sa.Numeric(18, 8)),
        sa.column("output_usd_per_1m", sa.Numeric(18, 8)),
        sa.column("currency", sa.String),
        sa.column("effective_from", sa.DateTime(timezone=True)),
        sa.column("effective_to", sa.DateTime(timezone=True)),
    )
    # ponytail: ON CONFLICT DO NOTHING замість SELECT-then-INSERT — повторний прогон не дублює
    op.execute(insert(table).values(rows).on_conflict_do_nothing(
        index_elements=["model", "effective_from"]
    ))


def downgrade() -> None:
    seed = json.loads((Path(__file__).resolve().parents[2] / "pricing_seed.json").read_text())
    op.execute(
        sa.text("DELETE FROM model_pricing WHERE effective_from = :ts").bindparams(
            ts=seed["effective_from"]
        )
    )
