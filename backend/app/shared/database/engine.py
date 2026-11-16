"""Engine creation.

All SAYZO tables live in their own `sayzo` schema rather than `public`. On Supabase, the
`public` schema is exposed through the auto-generated REST API; keeping our tables out of it
means they are reachable only through this backend, which enforces tenant and access rules.
"""

from sqlalchemy import Engine, create_engine, event

SCHEMA = "sayzo"
# `extensions` is where Supabase installs pgvector; `public` covers a plain Postgres install.
SEARCH_PATH = f"{SCHEMA}, public, extensions"


def sqlalchemy_url(database_url: str) -> str:
    """Accept a URI exactly as Supabase shows it and select the psycopg 3 driver."""
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    return database_url


def create_database_engine(database_url: str, *, pool_size: int = 5) -> Engine:
    engine = create_engine(
        sqlalchemy_url(database_url),
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=pool_size,
        connect_args={"connect_timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def set_search_path(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        # Runs once per new connection. Requires the session pooler (port 5432):
        # transaction mode would not keep this setting between statements.
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {SEARCH_PATH}")
        dbapi_connection.commit()

    return engine
