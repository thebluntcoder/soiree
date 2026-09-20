"""plans.share_token

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-20 10:00:00.000000

Shareable plan card (TODO.md §5). share_token is an unguessable capability
token that makes a plan viewable read-only at GET /shared/{token} without
logging in. NULL means not shared; revoking sets it back to NULL.

Unique index, so a token resolves to at most one plan (multiple NULLs are
fine under a unique index in Postgres and SQLite).

Idempotent — inspects the table first, so it is safe whether the DB was
built from migrations or by the old create_all.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_plans_share_token"


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    if not _has_column("plans", "share_token"):
        op.add_column("plans", sa.Column("share_token", sa.String(), nullable=True))
    if not _has_index("plans", _INDEX):
        op.create_index(_INDEX, "plans", ["share_token"], unique=True)


def downgrade() -> None:
    if _has_index("plans", _INDEX):
        op.drop_index(_INDEX, table_name="plans")
    if _has_column("plans", "share_token"):
        op.drop_column("plans", "share_token")
