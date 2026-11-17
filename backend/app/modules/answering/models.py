"""What the answering step returns: a status, the text, and the citations it rests on."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.modules.retrieval import RetrievalResult, SearchHit


class AnswerStatus(StrEnum):
    """Stored on the assistant message; the values match the database constraint."""

    ANSWERED = "ANSWERED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Citation:
    index: int  # the [n] used in the answer text, numbered 1.. in order of first use
    chunk_id: UUID
    source_id: UUID
    source_version: int
    external_id: str
    title: str
    source_type: str
    location: str
    authors: tuple[str, ...]
    occurred_at: datetime
    excerpt: str


@dataclass(frozen=True)
class Answer:
    status: AnswerStatus
    text: str
    citations: tuple[Citation, ...]
    retrieval: RetrievalResult | None = None
    evidence: tuple[SearchHit, ...] = ()  # the chunks the model was shown
