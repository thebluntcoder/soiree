"""add users.last_login_at

Revision ID: a1b2c3d4e5f6
Revises: 9e26f57f8d8b
Create Date: 2026-09-10 12:00:00.000000

Phone-OTP login for Soirée itself (see api/v1/endpoints/users.py) stamps
`users.last_login_at` on every successful verify. This adds the nullable
column.

Idempotent — inspects the table and only adds the column if it is missing,
so it is safe on a database whose `users` table was created by the old
`create_all` path (which already includes the column) as well as one built
purely from migrations.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "9e26f57f8d8b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("users", "last_login_at"):
        op.add_column(
            "users", sa.Column("last_login_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    if _has_column("users", "last_login_at"):
        op.drop_column("users", "last_login_at")
