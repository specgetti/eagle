"""reviews.model_report

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reviews") as batch:
        batch.add_column(sa.Column("model_report", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reviews") as batch:
        batch.drop_column("model_report")
