"""Ingestion: turns source snapshots into stored, searchable, graph-linked knowledge.

For every source the service decides what changed (see planning.py), then:
- new or edited content is chunked, embedded and graph-extracted *before* a transaction opens,
  and written in one short transaction, so search sees either the old version or the new one,
  never a half-written mix;
- deleted sources are soft-deleted and their chunks (and the graph evidence from them) removed;
- exact duplicates are stored but not indexed, pointing at the original;
- access groups, authors and project are re-synced on every run, so permission changes apply
  without re-embedding anything.
One failing source is reported and skipped; it does not stop the rest of the run.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC
from uuid import UUID

from app.modules.identity import ids
from app.modules.ingestion.chunking import Chunker
from app.modules.ingestion.graph import GraphExtractor, GraphValidator, KnownNames, ValidatedGraph
from app.modules.ingestion.planning import Action, decide
from app.modules.ingestion.repository import ChunkRecord, KnowledgeRepository, SourceRecord
from app.modules.sources import SourceDocument
from app.shared.database import UnitOfWork, stable_id
from app.shared.embeddings import EmbeddingProvider
from app.shared.llm import LLMError

logger = logging.getLogger(__name__)

# Chunks shorter than this rarely state a relationship; skipping them saves LLM calls.
MIN_WORDS_FOR_GRAPH = 6


@dataclass
class IngestionReport:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    ignored: int = 0
    duplicates: int = 0
    collapsed_snapshots: int = 0
    indexed_later: int = 0
    chunks_written: int = 0
    relationships_written: int = 0
    graph_rejections: list[str] = field(default_factory=list)
    graph_failures: int = 0
    failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _IndexPayload:
    chunks: list[ChunkRecord]
    graphs: list[ValidatedGraph | None]


def source_id(document: SourceDocument) -> UUID:
    return stable_id(
        "source", document.organisation_id, document.source_system, document.external_id
    )


def latest_snapshots(documents: Sequence[SourceDocument]) -> tuple[list[SourceDocument], int]:
    """The newest snapshot of each source, oldest first, and how many repeats were collapsed."""
    latest: dict[UUID, SourceDocument] = {}
    seen: set[str] = set()
    repeats = 0
    for document in documents:
        key = f"{source_id(document)}@{document.version}"
        if key in seen:
            repeats += 1  # the same snapshot exported twice
        seen.add(key)
        current = latest.get(source_id(document))
        if current is None or document.version >= current.version:
            latest[source_id(document)] = document
    return sorted(latest.values(), key=lambda d: (d.occurred_at, d.external_id)), repeats


class IngestionService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        embedder: EmbeddingProvider,
        chunker: Chunker | None = None,
        graph_extractor: GraphExtractor | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._embedder = embedder
        self._chunker = chunker or Chunker()
        self._graph_extractor = graph_extractor
        self._known_names: dict[UUID, KnownNames] = {}

    def ingest(self, documents: Sequence[SourceDocument]) -> IngestionReport:
        report = IngestionReport()
        snapshots, report.collapsed_snapshots = latest_snapshots(documents)
        for document in snapshots:
            try:
                self._ingest_one(document, report)
            except Exception as error:  # one bad source must not stop the run
                logger.exception("Failed to ingest %s", document.external_id)
                report.failures.append(f"{document.external_id}: {error}")
        self._index_pending(report)
        return report

    # -- per-source handling -------------------------------------------------------------------

    def _ingest_one(self, document: SourceDocument, report: IngestionReport) -> None:
        record = self._to_record(document)
        with self._unit_of_work() as uow:
            stored = KnowledgeRepository(uow.connection).stored_source(record.id)
        action = decide(stored, document)

        if action is Action.IGNORE:
            report.ignored += 1
        elif action is Action.UNCHANGED:
            with self._unit_of_work() as uow:
                self._sync_access(KnowledgeRepository(uow.connection), document, record)
            report.unchanged += 1
        elif action is Action.DELETE:
            self._delete(document, record)
            report.deleted += 1
        else:
            self._store(document, record, report)
            if action is Action.CREATE:
                report.created += 1
            else:
                report.updated += 1

    def _delete(self, document: SourceDocument, record: SourceRecord) -> None:
        with self._unit_of_work() as uow:
            repository = KnowledgeRepository(uow.connection)
            repository.mark_deleted(record.id, document.version, document.occurred_at)
            repository.release_duplicates_of(record.id)
            self._sync_access(repository, document, record)
            repository.delete_unsupported_relationships(record.organisation_id)

    def _store(
        self, document: SourceDocument, record: SourceRecord, report: IngestionReport
    ) -> None:
        with self._unit_of_work() as uow:
            original = KnowledgeRepository(uow.connection).find_original(
                record.organisation_id, record.content_hash, record.id
            )

        payload = None
        if original is None:
            # Slow work (embedding model, LLM) happens before the transaction opens.
            payload = self._build_index(
                record.organisation_id, record.title, record.content, report
            )

        with self._unit_of_work() as uow:
            repository = KnowledgeRepository(uow.connection)
            repository.save_source(record, duplicate_of=original)
            repository.release_duplicates_of(record.id)
            self._sync_access(repository, document, record)
            if payload is None:
                report.duplicates += 1
            else:
                self._write_index(
                    repository, record.id, record.organisation_id, record.version, payload, report
                )
            repository.delete_unsupported_relationships(record.organisation_id)

    def _index_pending(self, report: IngestionReport) -> None:
        """Index sources left unindexed, e.g. duplicates whose original was deleted or edited."""
        with self._unit_of_work() as uow:
            pending = KnowledgeRepository(uow.connection).pending_sources()
        for source in pending:
            try:
                with self._unit_of_work() as uow:
                    original = KnowledgeRepository(uow.connection).find_original(
                        source.organisation_id, source.content_hash, source.id
                    )
                if original is not None:
                    with self._unit_of_work() as uow:
                        KnowledgeRepository(uow.connection).mark_duplicate(source.id, original)
                    report.duplicates += 1
                    continue
                payload = self._build_index(
                    source.organisation_id, source.title, source.content, report
                )
                with self._unit_of_work() as uow:
                    self._write_index(
                        KnowledgeRepository(uow.connection),
                        source.id,
                        source.organisation_id,
                        source.version,
                        payload,
                        report,
                    )
                report.indexed_later += 1
            except Exception as error:
                logger.exception("Failed to index %s", source.id)
                report.failures.append(f"{source.id}: {error}")

    # -- building and writing the index --------------------------------------------------------

    def _build_index(
        self, organisation_id: UUID, title: str, content: str, report: IngestionReport
    ) -> _IndexPayload:
        chunks = self._chunker.split(content)
        vectors = self._embedder.embed_documents([f"{title}\n{chunk.content}" for chunk in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("embedding provider returned the wrong number of vectors")
        records = [
            ChunkRecord(chunk.index, chunk.content, chunk.token_count, vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        graphs = [self._extract_graph(organisation_id, title, c.content, report) for c in chunks]
        return _IndexPayload(records, graphs)

    def _extract_graph(
        self, organisation_id: UUID, title: str, text: str, report: IngestionReport
    ) -> ValidatedGraph | None:
        if self._graph_extractor is None or len(text.split()) < MIN_WORDS_FOR_GRAPH:
            return None
        known = self._known(organisation_id)
        try:
            extracted = self._graph_extractor.extract(
                text, title=title, people=sorted(known.people), projects=known.projects
            )
        except LLMError as error:
            logger.warning("Graph extraction failed for %r: %s", title, error)
            report.graph_failures += 1
            return None
        graph = GraphValidator(known).validate(extracted)
        report.graph_rejections.extend(f"{title}: {reason}" for reason in graph.rejected)
        return graph

    def _write_index(
        self,
        repository: KnowledgeRepository,
        source: UUID,
        organisation_id: UUID,
        version: int,
        payload: _IndexPayload,
        report: IngestionReport,
    ) -> None:
        chunk_ids = repository.write_chunks(
            source, organisation_id, version, payload.chunks, self._embedder.model_name
        )
        report.chunks_written += len(chunk_ids)
        for chunk, graph in zip(chunk_ids, payload.graphs, strict=True):
            if graph is not None:
                repository.write_graph(organisation_id, chunk, graph.entities, graph.relationships)
                report.relationships_written += len(graph.relationships)

    # -- helpers -------------------------------------------------------------------------------

    def _known(self, organisation_id: UUID) -> KnownNames:
        if organisation_id not in self._known_names:
            with self._unit_of_work() as uow:
                self._known_names[organisation_id] = KnowledgeRepository(
                    uow.connection
                ).known_names(organisation_id)
        return self._known_names[organisation_id]

    @staticmethod
    def _to_record(document: SourceDocument) -> SourceRecord:
        return SourceRecord(
            id=source_id(document),
            organisation_id=ids.organisation_id(document.organisation_id),
            source_system=document.source_system,
            external_id=document.external_id,
            source_type=document.source_type.value,
            title=document.title,
            location=document.location,
            project_id=ids.project_id(document.project_id) if document.project_id else None,
            content=document.content,
            content_hash=document.content_hash,
            version=document.version,
            occurred_at=document.occurred_at.astimezone(UTC),
        )

    @staticmethod
    def _sync_access(
        repository: KnowledgeRepository, document: SourceDocument, record: SourceRecord
    ) -> None:
        repository.sync_access(
            record.id,
            record.organisation_id,
            record.project_id,
            [ids.member_id(document.organisation_id, a) for a in document.author_ids],
            [ids.access_group_id(g) for g in document.access_group_ids],
        )
