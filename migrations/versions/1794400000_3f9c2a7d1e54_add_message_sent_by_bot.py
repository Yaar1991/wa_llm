"""add sent_by_bot flag to message

Marks messages this app sent (e.g. summaries), so summaries exclude them while
still including messages the account owner typed from the same number.

Revision ID: 3f9c2a7d1e54
Revises: b2c3d4e5f6g7
Create Date: 2026-11-11

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "3f9c2a7d1e54"
down_revision: Union[str, None] = "b2c3d4e5f6g7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "message",
        sa.Column(
            "sent_by_bot", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("message", "sent_by_bot")
