"""Alembic environment.

Reads the DB connection string from the `DATABASE_URL` env var (a
standard SQLAlchemy/psycopg2 DSN, e.g.
`postgresql+psycopg2://agent_intel:agent_intel@localhost:5432/agent_intel`
-- matching docker-compose.yml's local Postgres by default) rather than
a hardcoded `sqlalchemy.url` in alembic.ini, so the same migrations run
unmodified against local dev, CI, and (via Secrets Manager-populated
env vars in a later phase) real RDS.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make `shared_schema` importable when alembic is invoked from
# packages/shared-schema/ (prepend_sys_path=. in alembic.ini already
# covers this in most cases; this is a defensive fallback).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared_schema.tables import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

DEFAULT_LOCAL_DATABASE_URL = (
    "postgresql+psycopg2://agent_intel:agent_intel@localhost:5432/agent_intel"
)
database_url = os.environ.get("DATABASE_URL", DEFAULT_LOCAL_DATABASE_URL)
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (emits SQL to stdout)."""
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
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
