"""Alembic environment: uses the app's DATABASE_URL and models."""
from logging.config import fileConfig

from alembic import context

from elaw_core import models  # noqa: F401  (registers tables)
from elaw_core.db import Base, get_engine

if context.config.config_file_name and context.config.attributes.get("configure_logger", True):
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_online():
    connectable = context.config.attributes.get("connection") or get_engine()
    if hasattr(connectable, "connect"):
        with connectable.connect() as connection:
            _run(connection)
    else:
        _run(connectable)


def _run(connection):
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=connection.dialect.name == "sqlite",
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
