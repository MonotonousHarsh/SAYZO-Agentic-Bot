"""Initial schema: identity, knowledge, graph, conversations and tasks.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-16
"""

from migrations.sql_runner import run_sql_file

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0001_initial_schema.up.sql")


def downgrade() -> None:
    run_sql_file("0001_initial_schema.down.sql")
