"""What retrieval returns: ranked chunks with their source metadata, and supporting graph facts."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class Channel(StrEnum):
    VECTOR = "vector"  # meaning: embedding similarity
    KEYWORD = "keyword"  # exact words: Postgres full-text search
    GRAPH = "graph"  # relationships between entities named in the question


@dataclass(frozen=True)
class SearchHit:
    chunk_id: UUID
    chunk_index: int
    content: str
    source_id: UUID
    source_version: int
    external_id: str
    source_system: str
    source_type: str
    title: str
    location: str
    project: str | None
    authors: tuple[str, ...]
    occurred_at: datetime
    score: float
    ranks: Mapping[Channel, int] = field(default_factory=dict)
    similarity: float | None = None


@dataclass(frozen=True)
class GraphFact:
    """A relationship whose evidence is one of the returned chunks, so it can be cited."""

    subject: str
    relationship_type: str
    object: str
    chunk_id: UUID


@dataclass(frozen=True)
class RetrievalResult:
    question: str
    hits: tuple[SearchHit, ...]
    graph_facts: tuple[GraphFact, ...] = ()
    matched_entities: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.hits
