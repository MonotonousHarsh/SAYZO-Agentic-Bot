"""Print what the database looks like: migration version, pgvector, tables and row counts.

Usage (from the backend folder): python scripts/db_status.py
"""

import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.shared.database import SCHEMA, create_database_engine


def main() -> int:
    database_url = get_settings().database_url
    if database_url is None:
        print("DATABASE_URL is not set (add it to the .env file).", file=sys.stderr)
        return 1
    engine = create_database_engine(database_url.get_secret_value(), pool_size=1)
    try:
        with engine.connect() as connection:
            pgvector = connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one_or_none()
            version = connection.execute(
                text(f"SELECT version_num FROM {SCHEMA}.alembic_version")
            ).scalar_one_or_none()
            tables = connection.execute(
                text(
                    "SELECT tablename, rowsecurity FROM pg_tables "
                    "WHERE schemaname = :schema AND tablename <> 'alembic_version' "
                    "ORDER BY tablename"
                ),
                {"schema": SCHEMA},
            ).all()
            print(f"pgvector: {pgvector or 'NOT INSTALLED'}")
            print(f"migration: {version or 'none applied'}")
            for name, row_security in tables:
                count = connection.execute(text(f'SELECT count(*) FROM {SCHEMA}."{name}"'))
                rls = "RLS on" if row_security else "RLS OFF"
                print(f"  {name:<24} {count.scalar_one():>6} rows   {rls}")
    except SQLAlchemyError as error:
        print(f"Database check failed: {error.__class__.__name__}: {error.orig}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
