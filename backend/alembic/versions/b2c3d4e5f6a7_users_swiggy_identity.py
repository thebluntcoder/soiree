"""users: Swiggy OAuth identity, phone optional

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-10 18:00:00.000000

Login is now Swiggy OAuth (see api/v1/endpoints/auth.py). The Swiggy MCP
access token is a JWT; its `sub` claim is the stable per-user key.

  + users.swiggy_sub      (nullable, unique index) — the login key
  + users.swiggy_user_id  (nullable)               — Swiggy's numeric id
  ~ users.phone           NOT NULL → nullable

The old unique index on `phone` is left as-is: phone is nullable and
unused now, and Postgres allows many NULLs under a unique index, so it is
harmless — not worth a fragile drop/recreate across dialects.

Idempotent — inspects the table first, so it is safe whether the DB was
built from migrations or by the old create_all.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _index_names(table: str) -> set[str]:
    return {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    cols = _cols("users")
    if "swiggy_sub" not in cols:
        op.add_column("users", sa.Column("swiggy_sub", sa.String(), nullable=True))
    if "swiggy_user_id" not in cols:
        op.add_column("users", sa.Column("swiggy_user_id", sa.String(), nullable=True))

    if "ix_users_swiggy_sub" not in _index_names("users"):
        op.create_index("ix_users_swiggy_sub", "users", ["swiggy_sub"], unique=True)

    # batch_alter_table so this also works on SQLite (local dev), which has
    # no native ALTER COLUMN.
    if _phone_not_null():
        with op.batch_alter_table("users") as batch:
            batch.alter_column("phone", existing_type=sa.String(), nullable=True)


def _phone_not_null() -> bool:
    for col in sa.inspect(op.get_bind()).get_columns("users"):
        if col["name"] == "phone":
            return not col["nullable"]
    return False


def downgrade() -> None:
    if not _phone_not_null():
        with op.batch_alter_table("users") as batch:
            batch.alter_column("phone", existing_type=sa.String(), nullable=False)
    if "ix_users_swiggy_sub" in _index_names("users"):
        op.drop_index("ix_users_swiggy_sub", table_name="users")
    cols = _cols("users")
    if "swiggy_user_id" in cols:
        op.drop_column("users", "swiggy_user_id")
    if "swiggy_sub" in cols:
        op.drop_column("users", "swiggy_sub")
