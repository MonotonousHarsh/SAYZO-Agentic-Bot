"""Fixtures for tests that need a real PostgreSQL database with pgvector.

Set TEST_DATABASE_URL to a throwaway database. These tests drop and recreate every SAYZO
table, so never point it at your Supabase project. Without it, integration tests are skipped.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from app.config import get_settings
from app.shared.database import create_database_engine

BACKEND = Path(__file__).resolve().parents[2]

TABLES = (
    "action_idempotency, task_events, tasks, message_citations, messages, conversations, "
    "relationship_evidence, relationships, entities, knowledge_chunks, source_access_groups, "
    "source_authors, sources, projects, access_group_members, access_groups, "
    "organisation_members, profiles, organisations"
)


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    configured = get_settings().database_url
    if configured is not None and configured.get_secret_value() == url:
        pytest.fail("TEST_DATABASE_URL must not be the same database as DATABASE_URL")
    return url


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.attributes["database_url"] = url
    return config


@pytest.fixture(scope="session")
def migrated_engine() -> Iterator[Engine]:
    url = _test_database_url()
    config = _alembic_config(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_database_engine(url, pool_size=2)
    yield engine
    engine.dispose()
    # Leaving the database migrated down proves the downgrade works too.
    command.downgrade(config, "base")


@pytest.fixture
def engine(migrated_engine: Engine) -> Engine:
    """A migrated database with every SAYZO table emptied before the test."""
    with migrated_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    return migrated_engine
