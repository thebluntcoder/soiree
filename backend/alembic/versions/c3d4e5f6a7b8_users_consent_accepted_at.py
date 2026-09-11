"""users.consent_accepted_at

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-11 10:00:00.000000

Consent is captured client-side (a checkbox in the sign-in modal, required
before /auth/start will proceed) and recorded here at /auth/callback time —
this is the server-side record of "the user agreed to the privacy policy
as of this login". See api/v1/endpoints/auth.py.

Idempotent — inspects the table first, so it is safe whether the DB was
built from migrations or by the old create_all.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("users", "consent_accepted_at"):
        op.add_column(
            "users", sa.Column("consent_accepted_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    if _has_column("users", "consent_accepted_at"):
        op.drop_column("users", "consent_accepted_at")
