"""
alembic/env.py — Alembic migration environment configuration.

CONCEPT: What Alembic does
----------------------------
Alembic is a database migration tool for SQLAlchemy. It tracks schema
changes over time in versioned migration files (in alembic/versions/).

Problem with create_all():
  - Can CREATE tables if they don't exist
  - Cannot ADD columns to existing tables
  - Cannot RENAME or change column types
  - Loses all data if you drop and recreate

Alembic solves this by generating ALTER TABLE statements that modify
the existing schema without touching data.

CONCEPT: autogenerate
-----------------------
When you run `alembic revision --autogenerate -m "description"`,
Alembic compares your SQLModel models against the current DB schema
and generates the migration script automatically.

Then run `alembic upgrade head` to apply it.

CONCEPT: Why we import all models here
----------------------------------------
Alembic's autogenerate needs to know about all SQLModel models
to compare them against the DB. We import them here so they register
with SQLModel.metadata before the comparison runs.
If you add a new model, import it here too.

CONCEPT: Sync vs async
------------------------
Alembic is synchronous — it does not support async engines directly.
We use psycopg2 (sync driver) in alembic.ini for migrations.
The app still uses asyncpg at runtime — these are separate concerns.
"""

from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool, create_engine
from alembic import context
from sqlmodel import SQLModel

# Import ALL models so Alembic knows about them
from app.models.user import User  # noqa: F401
from app.models.event import Event  # noqa: F401
from app.models.plan import Plan  # noqa: F401

# Alembic Config object — gives access to alembic.ini values
config = context.config

# Set up Python logging from alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# SQLModel.metadata contains all table definitions from imported models.
# Alembic compares this against the live DB to detect schema changes.
target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in offline mode — generates SQL without connecting.
    Useful for reviewing what will change before applying.
    Run with: alembic upgrade head --sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in online mode — connects to DB and applies changes.
    Normal mode for: alembic upgrade head / alembic downgrade -1
    """
    import os

    # Use DATABASE_URL from environment if available (Railway/production)
    # Fall back to alembic.ini value for local development
    # Convert asyncpg URL back to psycopg2 for Alembic (Alembic is sync)
    db_url = os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    db_url = db_url.replace("postgres://", "postgresql://", 1)
    connectable = create_engine(db_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
