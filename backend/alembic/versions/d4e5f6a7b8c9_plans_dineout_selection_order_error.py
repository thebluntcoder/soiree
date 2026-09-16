"""plans.dineout_selection + plans.order_error

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-16 10:00:00.000000

Phase 2, slice 1 (Dineout book_table only — see TODO.md §4).

dineout_selection is a JSON snapshot of the resolved Dineout booking
target, captured at plan-generation time: restaurant_id, name, date,
guest_count, start_hour, available_slots. The [DINEOUT] prose has no
structural link back to one specific slot, so order placement re-derives
a slotId from this via closest_slot() against a freshly re-fetched slot
list — never a stale one.

order_error is a short machine-readable failure code set when a plan's
status becomes 'failed' during ordering, surfaced by GET /orders/{plan_id}.

Idempotent — inspects the table first, so it is safe whether the DB was
built from migrations or by the old create_all.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("plans", "dineout_selection"):
        op.add_column("plans", sa.Column("dineout_selection", sa.Text(), nullable=True))
    if not _has_column("plans", "order_error"):
        op.add_column("plans", sa.Column("order_error", sa.String(), nullable=True))


def downgrade() -> None:
    if _has_column("plans", "order_error"):
        op.drop_column("plans", "order_error")
    if _has_column("plans", "dineout_selection"):
        op.drop_column("plans", "dineout_selection")
