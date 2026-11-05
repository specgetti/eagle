"""reviews.model_source and reviews.reported_model

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reviews") as batch:
        batch.add_column(sa.Column("model_source", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("reported_model", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reviews") as batch:
        batch.drop_column("reported_model")
        batch.drop_column("model_source")
