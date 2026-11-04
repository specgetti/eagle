"""users.auth_source for SSO accounts

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("auth_source", sa.String(length=16), nullable=False, server_default="local")
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("auth_source")
