"""Composition root: builds the real services from settings. Scripts and the API use this;
tests build services directly with fakes."""

from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Engine

from app.config import Settings
from app.modules.answering import AnswerService, EvidencePolicy, EvidenceSelector
from app.modules.conversations import ChatService
from app.modules.retrieval import RetrievalService
from app.modules.tasks import TaskProposalService, TaskService
from app.shared.database import UnitOfWork, create_database_engine
from app.shared.embeddings import EmbeddingProvider
from app.shared.llm import LLMProvider


class ConfigurationError(Exception):
    """A required setting is missing."""


def database_engine(settings: Settings) -> Engine:
    if settings.database_url is None:
        raise ConfigurationError("DATABASE_URL is not set (add it to the .env file)")
    return create_database_engine(settings.database_url.get_secret_value())


def unit_of_work_factory(engine: Engine) -> Callable[[], UnitOfWork]:
    return lambda: UnitOfWork(engine)


def embedder(settings: Settings) -> EmbeddingProvider:
    from app.shared.embeddings.sentence_transformer_provider import SentenceTransformerEmbeddings

    return SentenceTransformerEmbeddings(
        settings.embedding_model,
        expected_dimension=settings.embedding_dimension,
        device=settings.embedding_device,
    )


def llm(settings: Settings) -> LLMProvider:
    from app.shared.llm.groq_provider import GroqProvider

    if settings.groq_api_key is None:
        raise ConfigurationError("GROQ_API_KEY is not set (add it to the .env file)")
    return GroqProvider(
        api_key=settings.groq_api_key.get_secret_value(),
        model=settings.groq_model,
        base_url=settings.groq_base_url,
        timeout_seconds=settings.groq_timeout_seconds,
        requests_per_minute=settings.groq_requests_per_minute or None,
    )


def retrieval_service(
    settings: Settings,
    unit_of_work: Callable[[], UnitOfWork],
    embeddings: EmbeddingProvider | None = None,
) -> RetrievalService:
    return RetrievalService(unit_of_work, embeddings or embedder(settings))


def evidence_selector(settings: Settings) -> EvidenceSelector:
    return EvidenceSelector(EvidencePolicy(min_similarity=settings.answer_min_similarity))


def answer_service(
    settings: Settings,
    unit_of_work: Callable[[], UnitOfWork],
    embeddings: EmbeddingProvider | None = None,
) -> AnswerService:
    retrieval = retrieval_service(settings, unit_of_work, embeddings)
    return AnswerService(
        retrieval, llm(settings), evidence_selector(settings), today=lambda: local_today(settings)
    )


def task_service(settings: Settings, unit_of_work: Callable[[], UnitOfWork]) -> TaskService:
    return TaskService(unit_of_work, timezone=settings.timezone)


def task_proposal_service(
    settings: Settings,
    unit_of_work: Callable[[], UnitOfWork],
    retrieval: RetrievalService | None = None,
    model: LLMProvider | None = None,
) -> TaskProposalService:
    return TaskProposalService(
        unit_of_work,
        retrieval or retrieval_service(settings, unit_of_work),
        model or llm(settings),
        evidence_selector(settings),
        today=lambda: local_today(settings),
    )


def chat_service(settings: Settings, unit_of_work: Callable[[], UnitOfWork]) -> ChatService:
    """Answers and task proposals sharing one embedding model and one LLM client."""
    embeddings = embedder(settings)
    model = llm(settings)
    retrieval = retrieval_service(settings, unit_of_work, embeddings)
    selector = evidence_selector(settings)
    return ChatService(
        unit_of_work,
        AnswerService(retrieval, model, selector, today=lambda: local_today(settings)),
        TaskProposalService(
            unit_of_work, retrieval, model, selector, today=lambda: local_today(settings)
        ),
    )


def local_today(settings: Settings) -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()
