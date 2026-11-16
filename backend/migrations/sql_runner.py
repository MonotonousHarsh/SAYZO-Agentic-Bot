"""Runs one of the hand-written SQL files in migrations/sql inside the migration transaction."""

from pathlib import Path

from alembic import op

SQL_DIR = Path(__file__).resolve().parent / "sql"


def run_sql_file(name: str) -> None:
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    # Use the driver cursor without parameters so the file is sent exactly as written:
    # '%' in PL/pgSQL and '$$' bodies must not be treated as query placeholders.
    dbapi_connection = op.get_bind().connection.dbapi_connection
    with dbapi_connection.cursor() as cursor:
        cursor.execute(sql)
