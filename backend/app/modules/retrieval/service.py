"""Hybrid retrieval: vector + keyword + graph, fused by rank, filtered by access first.

The question is embedded outside the transaction (it is the slow part). All three searches
then run in one short read transaction, each already restricted to what the reader may see.
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from app.modules.identity import AccessContext
from app.modules.retrieval.entity_matching import EntityMatcher
from app.modules.retrieval.fusion import reciprocal_rank_fusion
from app.modules.retrieval.models import Channel, GraphFact, RetrievalResult, SearchHit
from app.modules.retrieval.repository import ChunkRow, GraphEdge, RetrievalRepository
from app.shared.database import UnitOfWork
from app.shared.embeddings import EmbeddingProvider

MAX_QUESTION_CHARS = 2000


@dataclass(frozen=True)
class RetrievalSettings:
    candidates_per_channel: int = 20
    max_results: int = 8
    max_chunks_per_source: int = 2  # leave room for other sources, so answers can combine them
    max_graph_facts: int = 12
    rrf_k: int = 60

    def __post_init__(self) -> None:
        if min(self.candidates_per_channel, self.max_results, self.max_chunks_per_source) < 1:
            raise ValueError("retrieval limits must be at least 1")


class RetrievalService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        embedder: EmbeddingProvider,
        settings: RetrievalSettings | None = None,
        matcher: EntityMatcher | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._embedder = embedder
        self._settings = settings or RetrievalSettings()
        self._matcher = matcher or EntityMatcher()

    def search(self, access: AccessContext, question: str) -> RetrievalResult:
        question = " ".join(question.split())
        if not question:
            raise ValueError("question must not be empty")
        if len(question) > MAX_QUESTION_CHARS:
            raise ValueError(f"question is longer than {MAX_QUESTION_CHARS} characters")
        if not access.access_group_ids:
            return RetrievalResult(question=question, hits=())

        embedding = self._embedder.embed_query(question)
        limit = self._settings.candidates_per_channel
        with self._unit_of_work() as uow:
            repository = RetrievalRepository(uow.connection)
            similar = repository.vector_search(access, embedding, limit)
            keyword = repository.keyword_search(access, question, limit)
            matched = self._matcher.match(question, repository.entities(access))
            edges = repository.graph_edges(access, [e.id for e in matched], limit * 3)
            graph = list(dict.fromkeys(edge.chunk_id for edge in edges))[:limit]

            fused = reciprocal_rank_fusion(
                {
                    Channel.VECTOR: [chunk for chunk, _ in similar],
                    Channel.KEYWORD: keyword,
                    Channel.GRAPH: graph,
                },
                k=self._settings.rrf_k,
            )
            rows = repository.chunks(access, [f.item for f in fused])

        similarity = dict(similar)
        hits: list[SearchHit] = []
        per_source: dict[UUID, int] = {}
        for candidate in fused:
            row = rows.get(candidate.item)
            if (
                row is None
                or per_source.get(row.source_id, 0) >= self._settings.max_chunks_per_source
            ):
                continue
            per_source[row.source_id] = per_source.get(row.source_id, 0) + 1
            hits.append(_hit(row, candidate.score, candidate.ranks, similarity.get(row.chunk_id)))
            if len(hits) == self._settings.max_results:
                break

        return RetrievalResult(
            question=question,
            hits=tuple(hits),
            graph_facts=self._facts(edges, {h.chunk_id for h in hits}),
            matched_entities=tuple(e.name for e in matched),
        )

    def _facts(self, edges: list[GraphEdge], returned: set[UUID]) -> tuple[GraphFact, ...]:
        """One fact per relationship, citing a returned chunk that supports it."""
        facts: dict[UUID, GraphFact] = {}
        for edge in edges:
            if edge.chunk_id in returned and edge.relationship_id not in facts:
                facts[edge.relationship_id] = GraphFact(
                    edge.subject, edge.relationship_type, edge.object, edge.chunk_id
                )
        return tuple(facts.values())[: self._settings.max_graph_facts]


def _hit(row: ChunkRow, score: float, ranks, similarity: float | None) -> SearchHit:  # type: ignore[no-untyped-def]
    return SearchHit(
        chunk_id=row.chunk_id,
        chunk_index=row.chunk_index,
        content=row.content,
        source_id=row.source_id,
        source_version=row.source_version,
        external_id=row.external_id,
        source_system=row.source_system,
        source_type=row.source_type,
        title=row.title,
        location=row.location,
        project=row.project,
        authors=row.authors,
        occurred_at=row.occurred_at,
        score=score,
        ranks=dict(ranks),
        similarity=similarity,
    )
