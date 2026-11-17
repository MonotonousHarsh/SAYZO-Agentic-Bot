"""Composition root: builds the real services from settings. Scripts and the API use this;
tests build services directly with fakes."""

from collections.abc import Callable

from sqlalchemy import Engine

from app.config import Settings
from app.modules.answering import AnswerService, EvidencePolicy, EvidenceSelector
from app.modules.retrieval import RetrievalService
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


def answer_service(
    settings: Settings,
    unit_of_work: Callable[[], UnitOfWork],
    embeddings: EmbeddingProvider | None = None,
) -> AnswerService:
    retrieval = RetrievalService(unit_of_work, embeddings or embedder(settings))
    selector = EvidenceSelector(EvidencePolicy(min_similarity=settings.answer_min_similarity))
    return AnswerService(retrieval, llm(settings), selector)
