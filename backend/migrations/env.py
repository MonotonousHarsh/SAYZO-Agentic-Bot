"""Alembic environment: runs the SQL migrations against DATABASE_URL.

Migrations are hand-written SQL files (see migrations/sql), so every constraint, index and
security setting is explicit and reviewable rather than generated.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.config import get_settings
from app.shared.database import SCHEMA, sqlalchemy_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def database_url() -> str:
    # Tests pass their own URL; normal runs read DATABASE_URL from the environment / .env.
    override = config.attributes.get("database_url")
    if override:
        return override
    configured = get_settings().database_url
    if configured is None:
        raise RuntimeError("DATABASE_URL is not set (add it to the .env file).")
    return configured.get_secret_value()


def run_migrations_online() -> None:
    engine = create_engine(sqlalchemy_url(database_url()), poolclass=pool.NullPool)
    with engine.connect() as connection:
        # The version table lives in our schema, so the schema must exist first.
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=None,
            version_table_schema=SCHEMA,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline mode is not supported; run against a database.")
run_migrations_online()
