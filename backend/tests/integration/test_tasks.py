from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from app.modules.answering import AnswerService, EvidencePolicy, EvidenceSelector
from app.modules.conversations import ChatService
from app.modules.identity import AccessService, DirectorySeeder
from app.modules.identity.ids import member_id, organisation_id
from app.modules.ingestion import IngestionService
from app.modules.ingestion.service import source_id
from app.modules.retrieval import RetrievalService
from app.modules.synthetic_data import SyntheticDataset
from app.modules.tasks import (
    ActionDenied,
    IdempotencyConflict,
    InvalidTask,
    TaskDraft,
    TaskProposalService,
    TaskService,
    TaskStatus,
)
from app.modules.tasks.proposals import ProposedTask, ProposedTasks
from app.shared.database import UnitOfWork
from tests.fakes import HashingEmbeddings, ScriptedLLM

pytestmark = pytest.mark.integration


@pytest.fixture
def world(engine: Engine, seed_dataset: SyntheticDataset):  # type: ignore[no-untyped-def]
    unit_of_work = lambda: UnitOfWork(engine)  # noqa: E731
    DirectorySeeder(unit_of_work).seed(seed_dataset.directory)
    embedder = HashingEmbeddings()
    IngestionService(unit_of_work, embedder).ingest(seed_dataset.documents)
    access = AccessService(unit_of_work)

    def person(key: str, org: str = "org_nexora"):  # type: ignore[no-untyped-def]
        return access.for_person(key, organisation_id(org))

    return {
        "uow": unit_of_work,
        "engine": engine,
        "tasks": TaskService(unit_of_work),
        "retrieval": RetrievalService(unit_of_work, embedder),
        "person": person,
        "dataset": seed_dataset,
    }


def draft(**overrides) -> TaskDraft:  # type: ignore[no-untyped-def]
    values = dict(
        title="Complete the v2 API migration",
        assignee_member_id=member_id("org_nexora", "nex_rohan"),
        due_date=date(2026, 11, 19),
    )
    values.update(overrides)
    return TaskDraft(**values)


def count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def test_creating_a_task_records_audit_and_idempotency(world) -> None:  # type: ignore[no-untyped-def]
    meera = world["person"]("nex_meera")

    result = world["tasks"].create(meera, draft(), idempotency_key="k1")

    task = result.task
    assert not result.replayed
    assert (task.assigned_to_name, task.due_date, task.status) == (
        "Rohan Verma",
        date(2026, 11, 19),
        TaskStatus.PENDING,
    )
    (event,) = world["tasks"].history(meera, task.id)
    assert event.event_type == "CREATED" and event.new_value["due_date"] == "2026-11-19"
    with world["engine"].connect() as connection:
        status = connection.execute(text("SELECT status FROM action_idempotency")).scalar_one()
    assert status == "SUCCEEDED"


def test_a_double_submit_creates_one_task(world) -> None:  # type: ignore[no-untyped-def]
    meera = world["person"]("nex_meera")
    first = world["tasks"].create(meera, draft(), idempotency_key="k1")

    second = world["tasks"].create(meera, draft(), idempotency_key="k1")

    assert second.replayed and second.task.id == first.task.id
    assert count(world["engine"], "tasks") == 1 and count(world["engine"], "task_events") == 1
    with pytest.raises(IdempotencyConflict):
        world["tasks"].create(meera, draft(title="Something else"), idempotency_key="k1")


def test_viewers_cannot_create_tasks(world) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ActionDenied):
        world["tasks"].create(world["person"]("nex_dev"), draft(), idempotency_key="k1")
    assert (
        count(world["engine"], "tasks") == 0 and count(world["engine"], "action_idempotency") == 0
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"assignee_member_id": member_id("org_vertex", "vtx_sana")}, "assignee"),
        ({"assignee_member_id": uuid4()}, "assignee"),
        ({"title": "ok"}, "title"),
    ],
)
def test_drafts_are_revalidated_on_confirm(world, overrides, message) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(InvalidTask, match=message):
        world["tasks"].create(world["person"]("nex_meera"), draft(**overrides), "k1")
    assert (
        count(world["engine"], "tasks") == 0 and count(world["engine"], "action_idempotency") == 0
    )


def test_a_task_cannot_cite_a_source_its_creator_cannot_see(world) -> None:  # type: ignore[no-untyped-def]
    hiring_plan = next(
        d for d in world["dataset"].documents if d.external_id == "nex_doc_hiring_plan"
    )
    restricted = draft(evidence_source_id=source_id(hiring_plan))

    with pytest.raises(InvalidTask, match="evidence"):
        world["tasks"].create(world["person"]("nex_rohan"), restricted, "k1")
    assert world["tasks"].create(world["person"]("nex_meera"), restricted, "k2").task


def test_status_changes_follow_the_policy_and_are_audited(world) -> None:  # type: ignore[no-untyped-def]
    task = world["tasks"].create(world["person"]("nex_meera"), draft(), "k1").task

    with pytest.raises(ActionDenied):
        world["tasks"].update_status(world["person"]("nex_priya"), task.id, TaskStatus.COMPLETED)
    done = world["tasks"].update_status(world["person"]("nex_rohan"), task.id, TaskStatus.COMPLETED)

    assert done.status is TaskStatus.COMPLETED
    events = world["tasks"].history(world["person"]("nex_meera"), task.id)
    assert [(e.event_type, e.actor_name) for e in events] == [
        ("CREATED", "Meera Iyer"),
        ("STATUS_CHANGED", "Rohan Verma"),
    ]
    assert (
        world["tasks"].list_tasks(world["person"]("nex_rohan"), assigned_to_me=True)[0].id
        == task.id
    )
    assert world["tasks"].list_tasks(world["person"]("vtx_sana", "org_vertex")) == []


def test_chat_proposes_tasks_and_refuses_viewers(world) -> None:  # type: ignore[no-untyped-def]
    llm = ScriptedLLM(
        [
            ProposedTasks(
                tasks=[
                    ProposedTask(
                        title="Complete the v2 API migration",
                        description="",
                        assignee="Rohan",
                        due_date="2026-11-19",
                        priority="MEDIUM",
                        source=1,
                    )
                ],
                note="",
            )
        ]
    )
    selector = EvidenceSelector(EvidencePolicy(min_similarity=0.0))
    chat = ChatService(
        world["uow"],
        AnswerService(world["retrieval"], llm, selector),
        TaskProposalService(
            world["uow"], world["retrieval"], llm, selector, today=lambda: date(2026, 11, 10)
        ),
    )

    turn = chat.ask(world["person"]("nex_meera"), "Create tasks for the Atlas weekly sync items.")
    denied = chat.ask(world["person"]("nex_dev"), "Create a task for Rohan to fix billing.")

    assert turn.reply.answer_status == "TASK_PROPOSED"
    assert turn.proposal is not None and turn.proposal.drafts[0].assignee_name == "Rohan Verma"
    assert "Complete the v2 API migration - Rohan Verma, due 19 Nov 2026" in turn.reply.content
    assert denied.reply.answer_status == "DENIED" and denied.proposal is None
    assert len(llm.prompts) == 1  # the viewer's request never reached the model
    assert count(world["engine"], "tasks") == 0  # proposing creates nothing
