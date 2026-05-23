"""Alembic environment — pulls metadata from `gapt_server.db.Base` and
the DSN from `GAPT_POSTGRES_DSN` (or `GAPT_SETTINGS.postgres_dsn`)."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from gapt_server.db import Base  # noqa: F401  — registers metadata as side effect
from gapt_server.db import models  # noqa: F401  — load every model

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_dsn() -> str:
    dsn = os.environ.get("GAPT_POSTGRES_DSN") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "Alembic needs a Postgres DSN. Set GAPT_POSTGRES_DSN or DATABASE_URL "
            "(e.g. postgresql://gapt:gapt@localhost:5432/gapt)."
        )
    # Alembic uses sync driver; coerce async / driver-less DSNs to psycopg sync.
    if dsn.startswith("postgresql+asyncpg"):
        dsn = dsn.replace("postgresql+asyncpg", "postgresql+psycopg", 1)
    elif dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _resolve_dsn()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
