from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import Engine, text

from app.modules.identity import DirectorySeeder
from app.modules.ingestion import IngestionService
from app.modules.ingestion.graph import (
    EntityType,
    ExtractedEntity,
    ExtractedGraph,
    ExtractedRelationship,
    RelationshipType,
)
from app.modules.ingestion.service import source_id
from app.modules.sources import SourceDocument
from app.modules.synthetic_data import SyntheticDataset
from app.shared.database import UnitOfWork
from app.shared.llm import LLMError
from tests.fakes import HashingEmbeddings, ScriptedGraphExtractor

pytestmark = pytest.mark.integration

MEETING_GRAPH = ExtractedGraph(
    entities=[
        ExtractedEntity(name="Rohan Verma", entity_type=EntityType.PERSON),
        ExtractedEntity(name="API migration", entity_type=EntityType.TASK),
        ExtractedEntity(name="Project Atlas", entity_type=EntityType.PROJECT),
    ],
    relationships=[
        ExtractedRelationship(
            subject="Rohan Verma",
            relationship_type=RelationshipType.RESPONSIBLE_FOR,
            object="API migration",
            confidence=0.95,
        ),
        ExtractedRelationship(
            subject="API migration",
            relationship_type=RelationshipType.PART_OF,
            object="Atlas",
            confidence=0.9,
        ),
    ],
)
MEETING_TRIGGER = "I'll complete the API migration by 19 November."


def make_service(engine: Engine, graph: ScriptedGraphExtractor | None = None) -> IngestionService:
    return IngestionService(lambda: UnitOfWork(engine), HashingEmbeddings(), graph_extractor=graph)


def as_of(dataset: SyntheticDataset, moment: str) -> list[SourceDocument]:
    cutoff = datetime.fromisoformat(moment)
    return [d for d in dataset.documents if d.occurred_at <= cutoff]


def document(dataset: SyntheticDataset, external_id: str) -> SourceDocument:
    return max(
        (d for d in dataset.documents if d.external_id == external_id), key=lambda d: d.version
    )


def source_row(engine: Engine, doc: SourceDocument):  # type: ignore[no-untyped-def]
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT s.*, (SELECT count(*) FROM knowledge_chunks c WHERE c.source_id = s.id) "
                "AS chunk_count FROM sources s WHERE s.id = :id"
            ),
            {"id": source_id(doc)},
        ).one()


def scalar(engine: Engine, sql: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(sql)).scalar_one()


@pytest.fixture
def seeded(engine: Engine, seed_dataset: SyntheticDataset) -> Engine:
    DirectorySeeder(lambda: UnitOfWork(engine)).seed(seed_dataset.directory)
    return engine


def test_full_ingest_of_the_seed_dataset(seeded: Engine, seed_dataset: SyntheticDataset) -> None:
    report = make_service(seeded).ingest(seed_dataset.documents)

    assert not report.failures
    assert report.collapsed_snapshots == 1  # nex_msg_001 was exported twice
    assert report.ignored == 1  # nex_msg_004 was deleted before we ever saw it
    assert report.created == 18 and report.duplicates == 1
    assert scalar(seeded, "SELECT count(*) FROM sources") == 18
    assert scalar(seeded, "SELECT count(DISTINCT source_id) FROM knowledge_chunks") == 17

    duplicate = source_row(seeded, document(seed_dataset, "nex_mtg_002"))
    original = source_row(seeded, document(seed_dataset, "nex_mtg_001"))
    assert duplicate.duplicate_of_source_id == original.id and duplicate.chunk_count == 0
    assert original.chunk_count >= 1


def test_rerun_changes_nothing(seeded: Engine, seed_dataset: SyntheticDataset) -> None:
    service = make_service(seeded)
    service.ingest(seed_dataset.documents)

    report = service.ingest(seed_dataset.documents)

    assert (report.created, report.updated, report.deleted, report.chunks_written) == (0, 0, 0, 0)
    assert report.unchanged == 18 and not report.failures  # 17 indexed + 1 duplicate


def test_edits_and_deletions_arrive_over_time(
    seeded: Engine, seed_dataset: SyntheticDataset
) -> None:
    service = make_service(seeded)
    plan = document(seed_dataset, "nex_doc_launch_plan")
    wrong_date = document(seed_dataset, "nex_msg_004")

    service.ingest(as_of(seed_dataset, "2026-11-10T12:00:00+05:30"))
    assert source_row(seeded, plan).version == 1
    assert "Friday 20 November 2026." in source_row(seeded, plan).content

    report = service.ingest(as_of(seed_dataset, "2026-11-12T09:35:00+05:30"))
    assert report.updated == 2  # the launch plan and Priya's edited message
    assert source_row(seeded, wrong_date).status == "ACTIVE"

    report = service.ingest(seed_dataset.documents)
    assert report.deleted == 1
    deleted = source_row(seeded, wrong_date)
    assert (deleted.status, deleted.content, deleted.chunk_count) == ("DELETED", "", 0)
    with seeded.connect() as connection:
        versions = (
            connection.execute(
                text("SELECT DISTINCT source_version FROM knowledge_chunks WHERE source_id = :id"),
                {"id": source_id(plan)},
            )
            .scalars()
            .all()
        )
    assert versions == [2]  # no chunk of the old version survives


def test_deleting_an_original_promotes_its_duplicate_and_moves_graph_evidence(
    seeded: Engine, seed_dataset: SyntheticDataset
) -> None:
    graph = ScriptedGraphExtractor({MEETING_TRIGGER: MEETING_GRAPH})
    service = make_service(seeded, graph)
    service.ingest(seed_dataset.documents)
    assert scalar(seeded, "SELECT count(*) FROM relationships") == 2

    original = document(seed_dataset, "nex_mtg_001")
    deletion = replace(original, version=2, content="", is_deleted=True)
    report = service.ingest([deletion])

    assert report.deleted == 1 and report.indexed_later == 1
    copy = source_row(seeded, document(seed_dataset, "nex_mtg_002"))
    assert copy.duplicate_of_source_id is None and copy.chunk_count >= 1
    with seeded.connect() as connection:
        evidence_sources = (
            connection.execute(
                text(
                    "SELECT DISTINCT c.source_id FROM relationship_evidence e "
                    "JOIN knowledge_chunks c ON c.id = e.chunk_id"
                )
            )
            .scalars()
            .all()
        )
    assert evidence_sources == [copy.id]
    assert scalar(seeded, "SELECT count(*) FROM relationships") == 2


def test_graph_is_resolved_to_members_and_projects(
    seeded: Engine, seed_dataset: SyntheticDataset
) -> None:
    graph = ScriptedGraphExtractor({MEETING_TRIGGER: MEETING_GRAPH})
    make_service(seeded, graph).ingest(seed_dataset.documents)

    with seeded.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT s.canonical_name AS subject, r.relationship_type, o.canonical_name AS object,
                       s.member_id IS NOT NULL AS subject_is_member
                FROM relationships r
                JOIN entities s ON s.id = r.subject_entity_id
                JOIN entities o ON o.id = r.object_entity_id
                ORDER BY r.relationship_type
                """
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("API migration", "PART_OF", "Project Atlas", False),
        ("Rohan Verma", "RESPONSIBLE_FOR", "API migration", True),
    ]


def test_access_changes_apply_without_reindexing(
    seeded: Engine, seed_dataset: SyntheticDataset
) -> None:
    embedder = HashingEmbeddings()
    service = IngestionService(lambda: UnitOfWork(seeded), embedder)
    service.ingest(seed_dataset.documents)
    calls_before = embedder.calls
    plan = document(seed_dataset, "nex_doc_launch_plan")

    report = service.ingest([replace(plan, access_group_ids=("nex_g_engineering",))])

    assert report.unchanged == 1 and embedder.calls == calls_before
    with seeded.connect() as connection:
        groups = (
            connection.execute(
                text(
                    "SELECT g.external_key FROM source_access_groups sg "
                    "JOIN access_groups g ON g.id = sg.access_group_id WHERE sg.source_id = :id"
                ),
                {"id": source_id(plan)},
            )
            .scalars()
            .all()
        )
    assert groups == ["nex_g_engineering"]


def test_failing_graph_extraction_still_indexes_the_source(
    seeded: Engine, seed_dataset: SyntheticDataset
) -> None:
    class FailingExtractor(ScriptedGraphExtractor):
        def extract(self, text, *, title, people, projects):  # type: ignore[no-untyped-def]
            raise LLMError("rate limited")

    report = make_service(seeded, FailingExtractor()).ingest(seed_dataset.documents)

    assert not report.failures and report.graph_failures > 0
    assert scalar(seeded, "SELECT count(DISTINCT source_id) FROM knowledge_chunks") == 17
