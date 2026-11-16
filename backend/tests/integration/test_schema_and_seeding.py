import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.modules.identity import DirectorySeeder, ids
from app.modules.sources import Directory
from app.modules.synthetic_data import SyntheticDataset
from app.shared.database import SCHEMA, UnitOfWork

pytestmark = pytest.mark.integration


def seed(engine: Engine, directory: Directory) -> None:
    DirectorySeeder(lambda: UnitOfWork(engine)).seed(directory)


def count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def test_every_table_has_row_level_security(engine: Engine) -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT tablename, rowsecurity FROM pg_tables "
                "WHERE schemaname = :schema AND tablename <> 'alembic_version'"
            ),
            {"schema": SCHEMA},
        ).all()

    assert len(rows) == 19
    assert all(row_security for _, row_security in rows)


def test_seeding_writes_the_directory(engine: Engine, seed_dataset: SyntheticDataset) -> None:
    seed(engine, seed_dataset.directory)

    assert count(engine, "organisations") == 2
    assert count(engine, "profiles") == 8
    assert count(engine, "organisation_members") == 8
    assert count(engine, "access_groups") == 7
    assert count(engine, "projects") == 3
    assert count(engine, "access_group_members") == 16


def test_seeding_twice_changes_nothing(engine: Engine, seed_dataset: SyntheticDataset) -> None:
    seed(engine, seed_dataset.directory)
    with engine.connect() as connection:
        before = connection.execute(text("SELECT id FROM organisation_members ORDER BY id")).all()

    seed(engine, seed_dataset.directory)

    with engine.connect() as connection:
        after = connection.execute(text("SELECT id FROM organisation_members ORDER BY id")).all()
    assert before == after
    assert count(engine, "access_group_members") == 16


def test_reseeding_updates_group_membership(engine: Engine, seed_dataset: SyntheticDataset) -> None:
    seed(engine, seed_dataset.directory)
    directory = seed_dataset.directory
    nexora = directory.organisations[0]
    rohan = next(p for p in nexora.people if p.id == "nex_rohan")
    moved = rohan.model_copy(update={"access_group_ids": ("nex_g_general",)})
    people = tuple(moved if p.id == "nex_rohan" else p for p in nexora.people)
    changed = directory.model_copy(
        update={"organisations": (nexora.model_copy(update={"people": people}),)}
    )

    seed(engine, changed)

    with engine.connect() as connection:
        groups = (
            connection.execute(
                text("SELECT access_group_id FROM access_group_members WHERE member_id = :m"),
                {"m": ids.member_id("org_nexora", "nex_rohan")},
            )
            .scalars()
            .all()
        )
    assert groups == [ids.access_group_id("nex_g_general")]


def test_database_rejects_cross_tenant_group_membership(
    engine: Engine, seed_dataset: SyntheticDataset
) -> None:
    seed(engine, seed_dataset.directory)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO access_group_members (access_group_id, member_id, organisation_id) "
                "VALUES (:group, :member, :org)"
            ),
            {
                "group": ids.access_group_id("nex_g_hr"),
                "member": ids.member_id("org_vertex", "vtx_sana"),
                "org": ids.organisation_id("org_nexora"),
            },
        )


def test_database_rejects_task_assigned_to_another_organisation(
    engine: Engine, seed_dataset: SyntheticDataset
) -> None:
    seed(engine, seed_dataset.directory)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tasks (organisation_id, title, created_by, assigned_to) "
                "VALUES (:org, 'Fix billing endpoints', :creator, :assignee)"
            ),
            {
                "org": ids.organisation_id("org_nexora"),
                "creator": ids.member_id("org_nexora", "nex_meera"),
                "assignee": ids.member_id("org_vertex", "vtx_vikram"),
            },
        )


def test_task_events_are_append_only(engine: Engine, seed_dataset: SyntheticDataset) -> None:
    seed(engine, seed_dataset.directory)
    org = ids.organisation_id("org_nexora")
    meera = ids.member_id("org_nexora", "nex_meera")
    with engine.begin() as connection:
        task_id = connection.execute(
            text(
                "INSERT INTO tasks (organisation_id, title, created_by) "
                "VALUES (:org, 'Review docs', :meera) RETURNING id"
            ),
            {"org": org, "meera": meera},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO task_events (organisation_id, task_id, actor_member_id, event_type) "
                "VALUES (:org, :task, :meera, 'CREATED')"
            ),
            {"org": org, "task": task_id, "meera": meera},
        )

    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
        connection.execute(text("UPDATE task_events SET event_type = 'UPDATED'"))


def test_invalid_role_is_rejected(engine: Engine, seed_dataset: SyntheticDataset) -> None:
    nexora = seed_dataset.directory.organisations[0]
    broken_person = nexora.people[0].model_copy(update={"role": "OWNER"})
    broken = seed_dataset.directory.model_copy(
        update={"organisations": (nexora.model_copy(update={"people": (broken_person,)}),)}
    )

    with pytest.raises(IntegrityError):
        seed(engine, broken)
    assert count(engine, "organisations") == 0  # the whole seed rolled back


def test_embedding_dimension_is_enforced(engine: Engine, seed_dataset: SyntheticDataset) -> None:
    seed(engine, seed_dataset.directory)
    org = ids.organisation_id("org_nexora")
    with engine.begin() as connection:
        source_id = connection.execute(
            text(
                "INSERT INTO sources (organisation_id, source_system, external_id, source_type, "
                "title, location, content, content_hash, version, occurred_at) VALUES "
                "(:org, 'notion', 'x', 'document', 'T', 'notion:x', 'c', 'h', 1, now()) "
                "RETURNING id"
            ),
            {"org": org},
        ).scalar_one()

    with pytest.raises(DBAPIError, match="dimensions"), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO knowledge_chunks (organisation_id, source_id, source_version, "
                "chunk_index, content, token_count, embedding, embedding_model) VALUES "
                "(:org, :source, 1, 0, 'c', 1, '[1,2,3]', 'test')"
            ),
            {"org": org, "source": source_id},
        )
