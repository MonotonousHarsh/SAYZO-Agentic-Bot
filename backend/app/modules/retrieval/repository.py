"""SQL for retrieval. Every query applies the same visibility rule *inside* the query, before
ranking and limiting, so an invisible chunk can never take a slot or leak through a join.

Visible means: same organisation; source ACTIVE, indexed and not a duplicate; chunk belongs
to the source's current version; and the source shares an access group with the reader.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, text

from app.modules.identity import AccessContext
from app.modules.retrieval.entity_matching import EntityRef
from app.shared.embeddings import to_pgvector_literal

_VISIBLE = """
        c.organisation_id = :org
    AND s.organisation_id = c.organisation_id
    AND s.status = 'ACTIVE'
    AND s.indexed_at IS NOT NULL
    AND s.duplicate_of_source_id IS NULL
    AND c.source_version = s.version
    AND EXISTS (
        SELECT 1 FROM source_access_groups sag
        WHERE sag.source_id = s.id AND sag.organisation_id = s.organisation_id
          AND sag.access_group_id = ANY(CAST(:groups AS uuid[]))
    )
"""

# Questions are natural language, so any query word may match (OR), not all of them (AND).
_OR_QUERY = "to_tsquery('english', replace(plainto_tsquery('english', :query)::text, ' & ', ' | '))"


@dataclass(frozen=True)
class ChunkRow:
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


@dataclass(frozen=True)
class GraphEdge:
    chunk_id: UUID
    relationship_id: UUID
    subject: str
    relationship_type: str
    object: str
    endpoints_matched: int


class RetrievalRepository:
    def __init__(self, connection: Connection) -> None:
        self._db = connection

    @staticmethod
    def _scope(access: AccessContext) -> dict[str, object]:
        return {
            "org": access.organisation_id,
            "groups": [str(g) for g in sorted(access.access_group_ids)],
        }

    def vector_search(
        self, access: AccessContext, embedding: Sequence[float], limit: int
    ) -> list[tuple[UUID, float]]:
        """(chunk id, cosine similarity), most similar first."""
        rows = self._db.execute(
            text(
                f"""
                SELECT c.id, 1 - (c.embedding <=> CAST(:embedding AS vector)) AS similarity
                FROM knowledge_chunks c JOIN sources s ON s.id = c.source_id
                WHERE {_VISIBLE}
                ORDER BY c.embedding <=> CAST(:embedding AS vector), c.id
                LIMIT :limit
                """
            ),
            {**self._scope(access), "embedding": to_pgvector_literal(embedding), "limit": limit},
        ).all()
        return [(row.id, float(row.similarity)) for row in rows]

    def keyword_search(self, access: AccessContext, query: str, limit: int) -> list[UUID]:
        """Chunks whose text or source title share words with the query, best match first."""
        rows = self._db.execute(
            text(
                f"""
                WITH q AS (SELECT {_OR_QUERY} AS tsq)
                SELECT c.id,
                       ts_rank_cd(c.content_tsv || to_tsvector('english', s.title), q.tsq) AS rank
                FROM q, knowledge_chunks c JOIN sources s ON s.id = c.source_id
                WHERE (c.content_tsv || to_tsvector('english', s.title)) @@ q.tsq
                  AND {_VISIBLE}
                ORDER BY rank DESC, c.id
                LIMIT :limit
                """
            ),
            {**self._scope(access), "query": query, "limit": limit},
        ).all()
        return [row.id for row in rows]

    def entities(self, access: AccessContext) -> list[EntityRef]:
        rows = self._db.execute(
            text(
                "SELECT id, entity_type, canonical_name FROM entities "
                "WHERE organisation_id = :org ORDER BY canonical_name, id"
            ),
            {"org": access.organisation_id},
        ).all()
        return [EntityRef(row.id, row.entity_type, row.canonical_name) for row in rows]

    def graph_edges(
        self, access: AccessContext, entity_ids: Sequence[UUID], limit: int
    ) -> list[GraphEdge]:
        """Relationships touching the given entities, through evidence chunks the reader can
        see. A relationship known only from a restricted chunk is never returned."""
        if not entity_ids:
            return []
        rows = self._db.execute(
            text(
                f"""
                SELECT e.chunk_id, r.id AS relationship_id, subj.canonical_name AS subject,
                       r.relationship_type, obj.canonical_name AS object,
                       (r.subject_entity_id = ANY(CAST(:entities AS uuid[])))::int
                     + (r.object_entity_id = ANY(CAST(:entities AS uuid[])))::int
                       AS endpoints_matched
                FROM relationships r
                JOIN relationship_evidence e
                     ON e.relationship_id = r.id AND e.organisation_id = r.organisation_id
                JOIN knowledge_chunks c ON c.id = e.chunk_id
                JOIN sources s ON s.id = c.source_id
                JOIN entities subj ON subj.id = r.subject_entity_id
                JOIN entities obj ON obj.id = r.object_entity_id
                WHERE r.organisation_id = :org
                  AND (r.subject_entity_id = ANY(CAST(:entities AS uuid[]))
                       OR r.object_entity_id = ANY(CAST(:entities AS uuid[])))
                  AND {_VISIBLE}
                ORDER BY endpoints_matched DESC, s.occurred_at DESC, e.chunk_id, r.id
                LIMIT :limit
                """
            ),
            {**self._scope(access), "entities": [str(e) for e in entity_ids], "limit": limit},
        ).all()
        return [
            GraphEdge(
                row.chunk_id,
                row.relationship_id,
                row.subject,
                row.relationship_type,
                row.object,
                row.endpoints_matched,
            )
            for row in rows
        ]

    def chunks(self, access: AccessContext, chunk_ids: Sequence[UUID]) -> dict[UUID, ChunkRow]:
        """Full details for chunks; visibility is checked again here as a second line."""
        if not chunk_ids:
            return {}
        rows = self._db.execute(
            text(
                f"""
                SELECT c.id AS chunk_id, c.chunk_index, c.content, s.id AS source_id,
                       s.version AS source_version, s.external_id, s.source_system,
                       s.source_type, s.title, s.location, p.name AS project, s.occurred_at,
                       COALESCE((
                           SELECT array_agg(pr.full_name ORDER BY pr.full_name)
                           FROM source_authors a
                           JOIN organisation_members m ON m.id = a.member_id
                           JOIN profiles pr ON pr.id = m.profile_id
                           WHERE a.source_id = s.id
                       ), '{{}}') AS authors
                FROM knowledge_chunks c
                JOIN sources s ON s.id = c.source_id
                LEFT JOIN projects p ON p.id = s.project_id
                WHERE c.id = ANY(CAST(:ids AS uuid[])) AND {_VISIBLE}
                """
            ),
            {**self._scope(access), "ids": [str(i) for i in chunk_ids]},
        ).all()
        return {
            row.chunk_id: ChunkRow(
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
                authors=tuple(row.authors),
                occurred_at=row.occurred_at,
            )
            for row in rows
        }
