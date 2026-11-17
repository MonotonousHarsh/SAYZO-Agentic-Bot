import pytest
from sqlalchemy import Engine, text

from app.modules.answering import AnswerService, EvidencePolicy, EvidenceSelector
from app.modules.answering.prompt import DraftAnswer
from app.modules.conversations import ChatService, ConversationNotFound
from app.modules.identity import AccessService, DirectorySeeder
from app.modules.identity.ids import organisation_id
from app.modules.ingestion import IngestionService
from app.modules.retrieval import RetrievalService
from app.modules.synthetic_data import SyntheticDataset
from app.shared.database import UnitOfWork
from tests.fakes import HashingEmbeddings, ScriptedLLM

pytestmark = pytest.mark.integration
QUESTION = "When is the Project Atlas launch?"


@pytest.fixture
def setup(engine: Engine, seed_dataset: SyntheticDataset):  # type: ignore[no-untyped-def]
    unit_of_work = lambda: UnitOfWork(engine)  # noqa: E731
    DirectorySeeder(unit_of_work).seed(seed_dataset.directory)
    embedder = HashingEmbeddings()
    IngestionService(unit_of_work, embedder).ingest(seed_dataset.documents)
    access = AccessService(unit_of_work)
    arjun = access.for_person("nex_arjun", organisation_id("org_nexora"))
    rohan = access.for_person("nex_rohan", organisation_id("org_nexora"))
    llm = ScriptedLLM([DraftAnswer(status="ANSWERED", answer="It is on 23 November [1].")] * 3)
    answers = AnswerService(
        RetrievalService(unit_of_work, embedder),
        llm,
        EvidenceSelector(EvidencePolicy(min_similarity=0.0)),  # stand-in embeddings score low
    )
    return ChatService(unit_of_work, answers), llm, arjun, rohan


def test_question_answer_and_citations_are_stored(setup, engine: Engine) -> None:  # type: ignore[no-untyped-def]
    chat, _, arjun, _ = setup

    turn = chat.ask(arjun, QUESTION)

    assert turn.reply.answer_status == "ANSWERED" and len(turn.reply.citations) == 1
    history = chat.history(arjun, turn.conversation_id)
    assert [(m.role, m.content) for m in history] == [
        ("USER", QUESTION),
        ("ASSISTANT", "It is on 23 November [1]."),
    ]
    with engine.connect() as connection:
        title = connection.execute(text("SELECT title FROM conversations")).scalar_one()
    assert title == QUESTION


def test_a_retried_request_returns_the_original_reply(setup) -> None:  # type: ignore[no-untyped-def]
    chat, llm, arjun, _ = setup
    first = chat.ask(arjun, QUESTION)

    second = chat.ask(arjun, "Follow-up", first.conversation_id, client_request_id="req-1")
    retry = chat.ask(arjun, "Follow-up", first.conversation_id, client_request_id="req-1")

    assert retry.replayed and retry.reply.id == second.reply.id
    assert len(llm.prompts) == 2  # the retry did not call the model again
    assert len(chat.history(arjun, first.conversation_id)) == 4


def test_conversations_are_private_to_their_owner(setup) -> None:  # type: ignore[no-untyped-def]
    chat, _, arjun, rohan = setup
    turn = chat.ask(arjun, QUESTION)

    with pytest.raises(ConversationNotFound):
        chat.history(rohan, turn.conversation_id)
    with pytest.raises(ConversationNotFound):
        chat.ask(rohan, "Anything?", turn.conversation_id)


def test_citations_stay_readable_after_the_source_is_reindexed(setup, engine: Engine) -> None:  # type: ignore[no-untyped-def]
    chat, _, arjun, _ = setup
    turn = chat.ask(arjun, QUESTION)
    cited = turn.reply.citations[0]

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM knowledge_chunks WHERE id = :id"), {"id": cited.chunk_id}
        )

    (stored,) = chat.history(arjun, turn.conversation_id)[1].citations
    assert stored.chunk_id is None
    assert (stored.source_title, stored.excerpt) == (cited.source_title, cited.excerpt)
