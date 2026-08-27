"""context generation for session reset

Revision ID: a1c4e9b7f210
Revises: 78294f5af593
Create Date: 2026-08-27 09:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c4e9b7f210'
down_revision: Union[str, None] = '78294f5af593'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default='0' -> наявні рядки отримують покоління 0, поведінка до CR не змінюється
    op.add_column(
        'sessions',
        sa.Column('generation', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'messages',
        sa.Column('generation', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index(
        'ix_messages_session_gen_seq', 'messages', ['session_id', 'generation', 'seq'], unique=False
    )
    op.drop_index('ix_messages_session_seq', table_name='messages')


def downgrade() -> None:
    op.create_index('ix_messages_session_seq', 'messages', ['session_id', 'seq'], unique=False)
    op.drop_index('ix_messages_session_gen_seq', table_name='messages')
    op.drop_column('messages', 'generation')
    op.drop_column('sessions', 'generation')
