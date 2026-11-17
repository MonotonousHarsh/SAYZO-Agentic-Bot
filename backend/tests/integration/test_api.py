from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.api.auth import DevAuthenticator, Identity
from app.api.container import Container
from app.api.main import create_app
from app.modules.answering import AnswerService, EvidencePolicy, EvidenceSelector
from app.modules.answering.prompt import DraftAnswer
from app.modules.conversations import ChatService
from app.modules.identity import AccessService, DirectorySeeder
from app.modules.ingestion import IngestionService
from app.modules.retrieval import RetrievalService
from app.modules.synthetic_data import SyntheticDataset
from app.modules.tasks import TaskProposalService, TaskService
from app.modules.tasks.proposals import ProposedTask, ProposedTasks
from app.shared.database import UnitOfWork
from tests.fakes import HashingEmbeddings, ScriptedLLM

pytestmark = pytest.mark.integration
PROPOSAL = ProposedTasks(
    tasks=[
        ProposedTask(
            title="Complete the v2 API migration",
            description="",
            assignee="Rohan",
            due_date="2026-11-19",
            priority="MEDIUM",
            source=1,
        ),
        ProposedTask(
            title="Run the staging load test",
            description="",
            assignee="Priya Nair",
            due_date="2026-11-20",
            priority="MEDIUM",
            source=1,
        ),
    ],
    note="",
)


class FixedUser:
    """Stands in for Supabase: every request is the given auth user."""

    def __init__(self, identity: Identity) -> None:
        self.identity = identity

    def authenticate(self, authorization, dev_user):  # type: ignore[no-untyped-def]
        return self.identity


def build(engine: Engine, dataset: SyntheticDataset, replies, authenticator=None):  # type: ignore[no-untyped-def]
    unit_of_work = lambda: UnitOfWork(engine)  # noqa: E731
    DirectorySeeder(unit_of_work).seed(dataset.directory)
    embedder = HashingEmbeddings()
    IngestionService(unit_of_work, embedder).ingest(dataset.documents)
    llm = ScriptedLLM(replies)
    retrieval = RetrievalService(unit_of_work, embedder)
    selector = EvidenceSelector(EvidencePolicy(min_similarity=0.0))
    container = Container(
        authenticator=authenticator or DevAuthenticator(),
        access=AccessService(unit_of_work),
        chat=ChatService(
            unit_of_work,
            AnswerService(retrieval, llm, selector),
            TaskProposalService(unit_of_work, retrieval, llm, selector),
        ),
        tasks=TaskService(unit_of_work),
        auth_mode="dev",
        dev_people=("nex_arjun",),
    )
    return TestClient(create_app(lambda: container)), llm


def as_(person: str) -> dict[str, str]:
    return {"X-Dev-User": person}


def test_ask_answer_cite_and_reopen(engine: Engine, seed_dataset: SyntheticDataset) -> None:
    replies = [DraftAnswer(status="ANSWERED", answer="Monday 23 November [1].")]
    client, _ = build(engine, seed_dataset, replies)
    with client:
        assert client.get("/api/me", headers=as_("nex_arjun")).json()["role"] == "MEMBER"
        reply = client.post(
            "/api/chat", json={"message": "When is the Atlas launch?"}, headers=as_("nex_arjun")
        ).json()

        assert reply["reply"]["status"] == "ANSWERED" and reply["reply"]["citations"]
        conversation = reply["conversation_id"]
        listed = client.get("/api/conversations", headers=as_("nex_arjun")).json()
        assert [c["id"] for c in listed] == [conversation]
        messages = client.get(
            f"/api/conversations/{conversation}/messages", headers=as_("nex_arjun")
        ).json()
        assert [m["role"] for m in messages] == ["USER", "ASSISTANT"]
        # Someone else gets "not found", not the conversation.
        other = client.get(f"/api/conversations/{conversation}/messages", headers=as_("nex_rohan"))
        assert other.status_code == 404


def test_propose_then_confirm_tasks_once(engine: Engine, seed_dataset: SyntheticDataset) -> None:
    client, _ = build(engine, seed_dataset, [PROPOSAL])
    with client:
        turn = client.post(
            "/api/chat",
            json={"message": "Create tasks for the Atlas weekly sync action items"},
            headers=as_("nex_meera"),
        ).json()
        assert turn["reply"]["status"] == "TASK_PROPOSED"
        drafts = turn["proposed_tasks"]
        assert [d["assignee_name"] for d in drafts] == ["Rohan Verma", "Priya Nair"]
        assert client.get("/api/tasks", headers=as_("nex_meera")).json() == []

        body = {
            "draft": {
                k: drafts[0][k]
                for k in (
                    "title",
                    "description",
                    "assignee_member_id",
                    "due_date",
                    "priority",
                    "evidence_source_id",
                )
            },
            "idempotency_key": f"{turn['reply']['id']}:0",
            "conversation_id": turn["conversation_id"],
            "message_id": turn["reply"]["id"],
        }
        first = client.post("/api/tasks", json=body, headers=as_("nex_meera"))
        again = client.post("/api/tasks", json=body, headers=as_("nex_meera"))

        assert first.status_code == 201 and not first.json()["replayed"]
        assert again.json()["replayed"] and again.json()["task"]["id"] == first.json()["task"]["id"]
        task_id = first.json()["task"]["id"]
        assert first.json()["task"]["due_date"] == "2026-11-19"

        changed = client.patch(
            f"/api/tasks/{task_id}", json={"status": "COMPLETED"}, headers=as_("nex_rohan")
        )
        assert changed.json()["status"] == "COMPLETED"
        denied = client.patch(
            f"/api/tasks/{task_id}", json={"status": "PENDING"}, headers=as_("nex_priya")
        )
        assert denied.status_code == 403
        events = client.get(f"/api/tasks/{task_id}/events", headers=as_("nex_meera")).json()
        assert [e["event_type"] for e in events] == ["CREATED", "STATUS_CHANGED"]

        conflict = client.post(
            "/api/tasks",
            json={**body, "draft": {**body["draft"], "title": "Changed"}},
            headers=as_("nex_meera"),
        )
        assert conflict.status_code == 409


def test_viewers_and_outsiders_are_refused(engine: Engine, seed_dataset: SyntheticDataset) -> None:
    client, llm = build(engine, seed_dataset, [])
    with client:
        draft = {"draft": {"title": "Fix billing endpoints"}, "idempotency_key": "k"}
        assert client.post("/api/tasks", json=draft, headers=as_("nex_dev")).status_code == 403
        denied = client.post(
            "/api/chat",
            json={"message": "Create a task for Rohan to fix billing"},
            headers=as_("nex_dev"),
        ).json()
        assert denied["reply"]["status"] == "DENIED" and not llm.prompts
        assert client.get("/api/me").status_code == 401
        assert client.get("/api/me", headers=as_("nobody")).status_code == 403
        bad = client.post("/api/chat", json={"message": "x", "extra": 1}, headers=as_("nex_dev"))
        assert bad.status_code == 422
        # A Vertex member cannot see Nexora's tasks.
        assert client.get("/api/tasks", headers=as_("vtx_sana")).json() == []


def test_supabase_user_is_linked_to_the_directory_by_email(
    engine: Engine, seed_dataset: SyntheticDataset
) -> None:
    rohan = uuid4()
    email = next(p.email for _, p in seed_dataset.directory.people() if p.id == "nex_rohan")

    def client_for(user_id, mail):  # type: ignore[no-untyped-def]
        return build(engine, seed_dataset, [], FixedUser(Identity(user_id, mail)))[0]

    with client_for(rohan, email) as signed_in:
        assert signed_in.get("/api/me").json()["full_name"] == "Rohan Verma"
    with engine.connect() as connection:
        linked = connection.execute(
            text("SELECT auth_user_id FROM profiles WHERE external_key = 'nex_rohan'")
        ).scalar_one()
    assert linked == rohan
    # Another auth user with the same email cannot take over the linked profile.
    with client_for(uuid4(), email) as impostor:
        assert impostor.get("/api/me").status_code == 403
    with client_for(uuid4(), "stranger@example.com") as stranger:
        assert stranger.get("/api/me").status_code == 403


def test_ui_and_public_config_are_served(engine: Engine, seed_dataset: SyntheticDataset) -> None:
    client, _ = build(engine, seed_dataset, [])
    with client:
        assert "SAYZO" in client.get("/").text
        assert client.get("/static/app.js").status_code == 200
        config = client.get("/api/config").json()
        assert config["auth_mode"] == "dev" and "secret" not in str(config).lower()
