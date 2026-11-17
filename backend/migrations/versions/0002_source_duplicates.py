"""Track exact duplicate sources.

Revision ID: 0002_source_duplicates
Revises: 0001_initial_schema
Create Date: 2026-11-17
"""

from migrations.sql_runner import run_sql_file

revision = "0002_source_duplicates"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0002_source_duplicates.up.sql")


def downgrade() -> None:
    run_sql_file("0002_source_duplicates.down.sql")
