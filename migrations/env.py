"""Alembic environment: runs migrations using the project's models and settings."""

from logging.config import fileConfig
from typing import Any

from alembic import context

from preciosdb.config import settings
from preciosdb.db import Base
from preciosdb.db.session import build_engine

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    # Allows tests/scripts to override the URL programmatically.
    return config.get_main_option("sqlalchemy.url") or settings.database_url


def _configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite needs "batch" mode to ALTER tables
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=get_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = build_engine(get_url())
    with engine.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
