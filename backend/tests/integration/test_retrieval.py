from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import Engine, text

from app.modules.identity import AccessContext, AccessDenied, AccessService, DirectorySeeder
from app.modules.identity.ids import member_id, organisation_id
from app.modules.ingestion import IngestionService
from app.modules.ingestion.graph import (
    EntityType,
    ExtractedEntity,
    ExtractedGraph,
    ExtractedRelationship,
    RelationshipType,
)
from app.modules.retrieval import Channel, RetrievalEvaluator, RetrievalService
from app.modules.synthetic_data import SyntheticDataset
from app.shared.database import UnitOfWork
from tests.fakes import HashingEmbeddings, ScriptedGraphExtractor

pytestmark = pytest.mark.integration

NEXORA = organisation_id("org_nexora")


def _relationship(subject: str, kind: RelationshipType, obj: str) -> ExtractedRelationship:
    return ExtractedRelationship(
        subject=subject, relationship_type=kind, object=obj, confidence=0.95
    )


GRAPHS = {
    "I'll complete the API migration by 19 November.": ExtractedGraph(
        entities=[
            ExtractedEntity(name="Rohan Verma", entity_type=EntityType.PERSON),
            ExtractedEntity(name="API migration", entity_type=EntityType.TASK),
            ExtractedEntity(name="Project Atlas", entity_type=EntityType.PROJECT),
        ],
        relationships=[
            _relationship("Rohan Verma", RelationshipType.RESPONSIBLE_FOR, "API migration"),
            _relationship("API migration", RelationshipType.PART_OF, "Project Atlas"),
        ],
    ),
    # Lives only in the restricted hiring plan (HR and leadership).
    "All offers need approval from the CTO": ExtractedGraph(
        entities=[
            ExtractedEntity(name="Meera Iyer", entity_type=EntityType.PERSON),
            ExtractedEntity(name="Offer approval", entity_type=EntityType.TASK),
        ],
        relationships=[
            _relationship("Meera Iyer", RelationshipType.RESPONSIBLE_FOR, "Offer approval")
        ],
    ),
}


@pytest.fixture
def stack(engine: Engine, seed_dataset: SyntheticDataset):  # type: ignore[no-untyped-def]
    unit_of_work = lambda: UnitOfWork(engine)  # noqa: E731
    DirectorySeeder(unit_of_work).seed(seed_dataset.directory)
    embedder = HashingEmbeddings()
    ingestion = IngestionService(
        unit_of_work, embedder, graph_extractor=ScriptedGraphExtractor(GRAPHS)
    )
    ingestion.ingest(seed_dataset.documents)
    return AccessService(unit_of_work), RetrievalService(unit_of_work, embedder), ingestion


def as_person(stack, person: str, org: str = "org_nexora") -> AccessContext:  # type: ignore[no-untyped-def]
    return stack[0].for_person(person, organisation_id(org))


def sources(stack, person: str, question: str, org: str = "org_nexora") -> list[str]:  # type: ignore[no-untyped-def]
    result = stack[1].search(as_person(stack, person, org), question)
    return [hit.external_id for hit in result.hits]


def test_nothing_leaks_for_any_ground_truth_question(stack, seed_dataset) -> None:  # type: ignore[no-untyped-def]
    card = RetrievalEvaluator(stack[0], stack[1]).evaluate(seed_dataset)

    assert len(card.scores) == 12
    assert card.leaks == 0, [(s.question_id, s.leaked) for s in card.scores if s.leaked]


def test_restricted_documents_follow_access_groups(stack) -> None:  # type: ignore[no-untyped-def]
    question = "What is the salary band for backend engineers?"

    assert "nex_doc_hiring_plan" in sources(stack, "nex_meera", question)
    assert "nex_doc_hiring_plan" not in sources(stack, "nex_rohan", question)
    assert "nex_doc_hiring_plan" not in sources(stack, "nex_dev", question)


def test_organisations_are_isolated(stack) -> None:  # type: ignore[no-untyped-def]
    vertex = sources(stack, "vtx_sana", "When does Project Atlas go live?", org="org_vertex")
    nexora = sources(stack, "nex_rohan", "When does Project Atlas go live?")

    assert vertex and all(s.startswith("vtx_") for s in vertex)
    assert nexora and all(s.startswith("nex_") for s in nexora)


def test_deleted_and_duplicate_sources_are_never_returned(stack, seed_dataset) -> None:  # type: ignore[no-untyped-def]
    _, retrieval, ingestion = stack
    ingestion_before_delete = [
        d
        for d in seed_dataset.documents
        if d.occurred_at <= datetime.fromisoformat("2026-11-12T09:35:00+05:30")
    ]
    question = "Atlas launch is on the 15th, telling customers now"
    arjun = as_person(stack, "nex_arjun")

    # Rewind to before the deletion: the wrong message is visible...
    with ingestion._unit_of_work() as uow:  # noqa: SLF001 - test setup only
        uow.connection.execute(text("TRUNCATE sources CASCADE"))
    ingestion.ingest(ingestion_before_delete)
    assert "nex_msg_004" in [h.external_id for h in retrieval.search(arjun, question).hits]

    # ...and disappears once the deletion arrives.
    ingestion.ingest(seed_dataset.documents)
    found = [h.external_id for h in retrieval.search(arjun, question).hits]
    assert "nex_msg_004" not in found
    assert "nex_mtg_002" not in sources(stack, "nex_meera", "Atlas weekly sync action items")


def test_graph_connects_entities_named_in_the_question(stack) -> None:  # type: ignore[no-untyped-def]
    result = stack[1].search(as_person(stack, "nex_meera"), "Who owns the API migration for Atlas?")

    assert set(result.matched_entities) >= {"API migration", "Project Atlas"}
    meeting = next(h for h in result.hits if h.external_id == "nex_mtg_001")
    assert Channel.GRAPH in meeting.ranks
    facts = {(f.subject, f.relationship_type, f.object) for f in result.graph_facts}
    assert ("Rohan Verma", "RESPONSIBLE_FOR", "API migration") in facts
    assert all(f.chunk_id in {h.chunk_id for h in result.hits} for f in result.graph_facts)


def test_graph_facts_from_restricted_sources_do_not_leak(stack) -> None:  # type: ignore[no-untyped-def]
    question = "Who is responsible for offer approval, Meera?"

    allowed = stack[1].search(as_person(stack, "nex_meera"), question)
    denied = stack[1].search(as_person(stack, "nex_rohan"), question)

    assert any(f.object == "Offer approval" for f in allowed.graph_facts)
    assert not any(f.object == "Offer approval" for f in denied.graph_facts)


def test_access_is_reloaded_on_every_request(stack, engine: Engine) -> None:  # type: ignore[no-untyped-def]
    access_service, retrieval, _ = stack
    meera = member_id("org_nexora", "nex_meera")
    question = "What is the salary band for backend engineers?"
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM access_group_members WHERE member_id = :m AND access_group_id IN "
                "(SELECT id FROM access_groups WHERE external_key = 'nex_g_leadership')"
            ),
            {"m": meera},
        )
    context = access_service.for_member(NEXORA, meera)
    assert "nex_doc_hiring_plan" not in [
        h.external_id for h in retrieval.search(context, question).hits
    ]

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE organisation_members SET status = 'SUSPENDED' WHERE id = :m"), {"m": meera}
        )
    with pytest.raises(AccessDenied):
        access_service.for_member(NEXORA, meera)


def test_a_reader_with_no_groups_sees_nothing(stack) -> None:  # type: ignore[no-untyped-def]
    nobody = replace(as_person(stack, "nex_rohan"), access_group_ids=frozenset())
    assert stack[1].search(nobody, "Project Atlas launch").is_empty


def test_results_spread_across_sources(stack) -> None:  # type: ignore[no-untyped-def]
    found = sources(stack, "nex_meera", "Project Atlas")
    assert max(found.count(s) for s in set(found)) <= 2
    with pytest.raises(ValueError):
        stack[1].search(as_person(stack, "nex_meera"), "   ")
