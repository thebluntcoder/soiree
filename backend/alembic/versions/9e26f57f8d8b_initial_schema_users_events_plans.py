"""initial schema — users events plans

Revision ID: 9e26f57f8d8b
Revises:
Create Date: 2026-04-26 17:31:43.719264

This is the initial schema. It was originally committed empty (schema was
created by SQLModel.metadata.create_all at app startup), which made Alembic
purely decorative. The real DDL now lives here.

ADOPTING ALEMBIC ON AN EXISTING DATABASE
----------------------------------------
Databases created by the old create_all path already have these tables.
`upgrade()` is therefore idempotent — it inspects the database and only
creates what is missing — so it is safe to run against both a brand-new
database and an already-populated one. An existing deployment that has
never run Alembic can simply run `alembic upgrade head`; it will no-op the
table creation and just record the version.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401  (autogenerate emits sqlmodel.sql.sqltypes.*)


# revision identifiers, used by Alembic.
revision: str = "9e26f57f8d8b"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    """Create users, events and plans — skipping any that already exist."""
    existing = _existing_tables()

    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("phone", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("email", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column(
                "preferred_cuisines", sqlmodel.sql.sqltypes.AutoString(), nullable=True
            ),
            sa.Column(
                "dietary_tags", sqlmodel.sql.sqltypes.AutoString(), nullable=True
            ),
            sa.Column(
                "default_city", sqlmodel.sql.sqltypes.AutoString(), nullable=True
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_users_phone"), "users", ["phone"], unique=True
        )

    if "events" not in existing:
        op.create_table(
            "events",
            sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column(
                "event_type",
                sa.Enum(
                    "date",
                    "friends",
                    "birthday",
                    "corporate",
                    "house_party",
                    "family",
                    name="eventtype",
                ),
                nullable=False,
            ),
            sa.Column(
                "venue_mode",
                sa.Enum("out", "home", "hybrid", name="venuemode"),
                nullable=False,
            ),
            sa.Column(
                "status",
                sa.Enum(
                    "draft",
                    "planned",
                    "ordered",
                    "completed",
                    "cancelled",
                    name="eventstatus",
                ),
                nullable=False,
            ),
            sa.Column("location", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("latitude", sa.Float(), nullable=True),
            sa.Column("longitude", sa.Float(), nullable=True),
            sa.Column("event_date", sa.DateTime(), nullable=True),
            sa.Column("start_hour", sa.Float(), nullable=False),
            sa.Column("budget", sa.Integer(), nullable=False),
            sa.Column("guest_count", sa.Integer(), nullable=False),
            sa.Column("guests", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column(
                "dietary_tags", sqlmodel.sql.sqltypes.AutoString(), nullable=True
            ),
            sa.Column("health_focus", sa.Integer(), nullable=False),
            sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_events_user_id"), "events", ["user_id"], unique=False
        )

    if "plans" not in existing:
        op.create_table(
            "plans",
            sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("event_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column("user_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
            sa.Column(
                "status",
                sa.Enum(
                    "generating",
                    "ready",
                    "approved",
                    "ordering",
                    "confirmed",
                    "failed",
                    name="planstatus",
                ),
                nullable=False,
            ),
            sa.Column("timeline", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column(
                "dineout_options", sqlmodel.sql.sqltypes.AutoString(), nullable=True
            ),
            sa.Column(
                "food_options", sqlmodel.sql.sqltypes.AutoString(), nullable=True
            ),
            sa.Column(
                "instamart_cart", sqlmodel.sql.sqltypes.AutoString(), nullable=True
            ),
            sa.Column(
                "active_offers", sqlmodel.sql.sqltypes.AutoString(), nullable=True
            ),
            sa.Column(
                "health_insight", sqlmodel.sql.sqltypes.AutoString(), nullable=True
            ),
            sa.Column("dineout_cost", sa.Integer(), nullable=True),
            sa.Column("food_cost", sa.Integer(), nullable=True),
            sa.Column("instamart_cost", sa.Integer(), nullable=True),
            sa.Column("total_cost", sa.Integer(), nullable=True),
            sa.Column("total_savings", sa.Integer(), nullable=True),
            sa.Column(
                "dineout_booking_id",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=True,
            ),
            sa.Column(
                "food_order_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True
            ),
            sa.Column(
                "instamart_order_id",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=True,
            ),
            sa.Column("edit_count", sa.Integer(), nullable=False),
            sa.Column("last_edited_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_plans_event_id"), "plans", ["event_id"], unique=False
        )
        op.create_index(
            op.f("ix_plans_user_id"), "plans", ["user_id"], unique=False
        )


def downgrade() -> None:
    """Drop everything this migration may have created."""
    existing = _existing_tables()

    if "plans" in existing:
        op.drop_index(op.f("ix_plans_user_id"), table_name="plans")
        op.drop_index(op.f("ix_plans_event_id"), table_name="plans")
        op.drop_table("plans")
    if "events" in existing:
        op.drop_index(op.f("ix_events_user_id"), table_name="events")
        op.drop_table("events")
    if "users" in existing:
        op.drop_index(op.f("ix_users_phone"), table_name="users")
        op.drop_table("users")

    # Native ENUM types are not dropped automatically when their table goes.
    bind = op.get_bind()
    for enum_name in ("planstatus", "eventstatus", "venuemode", "eventtype"):
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
