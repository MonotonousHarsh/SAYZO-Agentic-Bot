"""SQL for sources, chunks and the graph. Methods never commit; the unit of work does."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, text

from app.modules.ingestion.graph import KnownNames, ResolvedEntity, ResolvedRelationship
from app.modules.ingestion.planning import StoredSource
from app.shared.database import stable_id
from app.shared.embeddings import to_pgvector_literal


@dataclass(frozen=True)
class SourceRecord:
    id: UUID
    organisation_id: UUID
    source_system: str
    external_id: str
    source_type: str
    title: str
    location: str
    project_id: UUID | None
    content: str
    content_hash: str
    version: int
    occurred_at: datetime


@dataclass(frozen=True)
class ChunkRecord:
    chunk_index: int
    content: str
    token_count: int
    embedding: Sequence[float]


@dataclass(frozen=True)
class PendingSource:
    id: UUID
    organisation_id: UUID
    title: str
    content: str
    content_hash: str
    version: int


def chunk_id(source_id: UUID, version: int, chunk_index: int) -> UUID:
    return stable_id("chunk", str(source_id), str(version), str(chunk_index))


class KnowledgeRepository:
    def __init__(self, connection: Connection) -> None:
        self._db = connection

    # -- reading -------------------------------------------------------------------------------

    def stored_source(self, source_id: UUID) -> StoredSource | None:
        row = self._db.execute(
            text("SELECT version, content_hash, status FROM sources WHERE id = :id"),
            {"id": source_id},
        ).one_or_none()
        if row is None:
            return None
        return StoredSource(
            version=row.version, content_hash=row.content_hash, is_deleted=row.status == "DELETED"
        )

    def find_original(self, organisation_id: UUID, content_hash: str, exclude: UUID) -> UUID | None:
        """The live, non-duplicate source with identical content, if one exists."""
        return self._db.execute(
            text(
                """
                SELECT id FROM sources
                WHERE organisation_id = :org AND content_hash = :hash AND id <> :exclude
                  AND status = 'ACTIVE' AND duplicate_of_source_id IS NULL
                ORDER BY created_at, id
                LIMIT 1
                """
            ),
            {"org": organisation_id, "hash": content_hash, "exclude": exclude},
        ).scalar_one_or_none()

    def pending_sources(self) -> list[PendingSource]:
        """Live sources that are neither indexed nor marked as a duplicate."""
        rows = self._db.execute(
            text(
                """
                SELECT id, organisation_id, title, content, content_hash, version FROM sources
                WHERE status = 'ACTIVE' AND indexed_at IS NULL AND duplicate_of_source_id IS NULL
                ORDER BY created_at, id
                """
            )
        ).all()
        return [PendingSource(**row._mapping) for row in rows]

    def known_names(self, organisation_id: UUID) -> KnownNames:
        people = self._db.execute(
            text(
                """
                SELECT p.full_name, m.id FROM organisation_members m
                JOIN profiles p ON p.id = m.profile_id
                WHERE m.organisation_id = :org AND m.status = 'ACTIVE'
                """
            ),
            {"org": organisation_id},
        ).all()
        projects = (
            self._db.execute(
                text("SELECT name FROM projects WHERE organisation_id = :org"),
                {"org": organisation_id},
            )
            .scalars()
            .all()
        )
        return KnownNames(people={name: member for name, member in people}, projects=projects)

    # -- writing sources -----------------------------------------------------------------------

    def save_source(self, source: SourceRecord, *, duplicate_of: UUID | None) -> None:
        """Insert or replace the stored content. Marks it unindexed until chunks are written."""
        self._db.execute(
            text(
                """
                INSERT INTO sources (id, organisation_id, source_system, external_id, source_type,
                    title, location, project_id, content, content_hash, version, status,
                    occurred_at, deleted_at, indexed_at, duplicate_of_source_id)
                VALUES (:id, :organisation_id, :source_system, :external_id, :source_type,
                    :title, :location, :project_id, :content, :content_hash, :version, 'ACTIVE',
                    :occurred_at, NULL, NULL, :duplicate_of)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title, location = EXCLUDED.location,
                    project_id = EXCLUDED.project_id, content = EXCLUDED.content,
                    content_hash = EXCLUDED.content_hash, version = EXCLUDED.version,
                    status = 'ACTIVE', occurred_at = EXCLUDED.occurred_at, deleted_at = NULL,
                    indexed_at = NULL, duplicate_of_source_id = EXCLUDED.duplicate_of_source_id,
                    updated_at = now()
                """
            ),
            {**source.__dict__, "duplicate_of": duplicate_of},
        )
        self.delete_chunks(source.id)

    def mark_deleted(self, source_id: UUID, version: int, deleted_at: datetime) -> None:
        self._db.execute(
            text(
                """
                UPDATE sources SET status = 'DELETED', deleted_at = :deleted_at, content = '',
                    version = :version, indexed_at = NULL, duplicate_of_source_id = NULL,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": source_id, "version": version, "deleted_at": deleted_at},
        )
        self.delete_chunks(source_id)

    def release_duplicates_of(self, source_id: UUID) -> int:
        """Sources that were duplicates of this one become standalone (indexed on the next pass)."""
        result = self._db.execute(
            text(
                "UPDATE sources SET duplicate_of_source_id = NULL, updated_at = now() "
                "WHERE duplicate_of_source_id = :id"
            ),
            {"id": source_id},
        )
        return result.rowcount

    def mark_duplicate(self, source_id: UUID, original_id: UUID) -> None:
        self._db.execute(
            text(
                "UPDATE sources SET duplicate_of_source_id = :original, indexed_at = NULL, "
                "updated_at = now() WHERE id = :id"
            ),
            {"id": source_id, "original": original_id},
        )
        self.delete_chunks(source_id)

    def sync_access(
        self,
        source_id: UUID,
        organisation_id: UUID,
        project_id: UUID | None,
        author_ids: Sequence[UUID],
        group_ids: Sequence[UUID],
    ) -> None:
        """Make project, authors and access groups match the source system exactly."""
        params = {"source": source_id, "org": organisation_id}
        self._db.execute(
            text("UPDATE sources SET project_id = :project WHERE id = :source"),
            {**params, "project": project_id},
        )
        self._replace_links("source_authors", "member_id", author_ids, params)
        self._replace_links("source_access_groups", "access_group_id", group_ids, params)

    def _replace_links(self, table: str, column: str, ids: Sequence[UUID], params: dict) -> None:
        self._db.execute(
            text(f"DELETE FROM {table} WHERE source_id = :source AND NOT ({column} = ANY(:ids))"),
            {**params, "ids": list(ids)},
        )
        if ids:
            self._db.execute(
                text(
                    f"INSERT INTO {table} (source_id, {column}, organisation_id) "
                    f"VALUES (:source, :link, :org) ON CONFLICT DO NOTHING"
                ),
                [{**params, "link": link} for link in ids],
            )

    # -- writing the index ---------------------------------------------------------------------

    def delete_chunks(self, source_id: UUID) -> None:
        # Evidence rows go with their chunks (ON DELETE CASCADE).
        self._db.execute(
            text("DELETE FROM knowledge_chunks WHERE source_id = :id"), {"id": source_id}
        )

    def write_chunks(
        self,
        source_id: UUID,
        organisation_id: UUID,
        version: int,
        chunks: Sequence[ChunkRecord],
        embedding_model: str,
    ) -> list[UUID]:
        self.delete_chunks(source_id)
        ids = [chunk_id(source_id, version, chunk.chunk_index) for chunk in chunks]
        if chunks:
            self._db.execute(
                text(
                    """
                    INSERT INTO knowledge_chunks (id, organisation_id, source_id, source_version,
                        chunk_index, content, token_count, embedding, embedding_model)
                    VALUES (:id, :org, :source, :version, :chunk_index, :content, :token_count,
                        CAST(:embedding AS vector), :model)
                    """
                ),
                [
                    {
                        "id": id_,
                        "org": organisation_id,
                        "source": source_id,
                        "version": version,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.content,
                        "token_count": chunk.token_count,
                        "embedding": to_pgvector_literal(chunk.embedding),
                        "model": embedding_model,
                    }
                    for id_, chunk in zip(ids, chunks, strict=True)
                ],
            )
        self._db.execute(
            text("UPDATE sources SET indexed_at = now() WHERE id = :id"), {"id": source_id}
        )
        return ids

    def write_graph(
        self,
        organisation_id: UUID,
        evidence_chunk_id: UUID,
        entities: Sequence[ResolvedEntity],
        relationships: Sequence[ResolvedRelationship],
    ) -> None:
        for entity in entities:
            self._upsert_entity(organisation_id, entity)
        for relationship in relationships:
            subject_id = self._upsert_entity(organisation_id, relationship.subject)
            object_id = self._upsert_entity(organisation_id, relationship.object)
            relationship_id = stable_id(
                "relationship",
                str(organisation_id),
                str(subject_id),
                relationship.relationship_type,
                str(object_id),
            )
            self._db.execute(
                text(
                    """
                    INSERT INTO relationships (id, organisation_id, subject_entity_id,
                        relationship_type, object_entity_id)
                    VALUES (:id, :org, :subject, :type, :object)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": relationship_id,
                    "org": organisation_id,
                    "subject": subject_id,
                    "type": relationship.relationship_type,
                    "object": object_id,
                },
            )
            self._db.execute(
                text(
                    """
                    INSERT INTO relationship_evidence (relationship_id, chunk_id, organisation_id,
                        extraction_confidence)
                    VALUES (:relationship, :chunk, :org, :confidence)
                    ON CONFLICT (relationship_id, chunk_id) DO UPDATE
                        SET extraction_confidence = EXCLUDED.extraction_confidence
                    """
                ),
                {
                    "relationship": relationship_id,
                    "chunk": evidence_chunk_id,
                    "org": organisation_id,
                    "confidence": relationship.confidence,
                },
            )

    def delete_unsupported_relationships(self, organisation_id: UUID) -> int:
        """A relationship whose evidence chunks are all gone is no longer a known fact."""
        result = self._db.execute(
            text(
                """
                DELETE FROM relationships r
                WHERE r.organisation_id = :org AND NOT EXISTS (
                    SELECT 1 FROM relationship_evidence e WHERE e.relationship_id = r.id)
                """
            ),
            {"org": organisation_id},
        )
        return result.rowcount

    def _upsert_entity(self, organisation_id: UUID, entity: ResolvedEntity) -> UUID:
        entity_id = stable_id(
            "entity", str(organisation_id), entity.entity_type, entity.normalized_name
        )
        self._db.execute(
            text(
                """
                INSERT INTO entities (id, organisation_id, entity_type, canonical_name,
                    normalized_name, member_id)
                VALUES (:id, :org, :type, :canonical, :normalized, :member)
                ON CONFLICT (id) DO UPDATE
                    SET member_id = COALESCE(entities.member_id, EXCLUDED.member_id)
                """
            ),
            {
                "id": entity_id,
                "org": organisation_id,
                "type": entity.entity_type,
                "canonical": entity.canonical_name,
                "normalized": entity.normalized_name,
                "member": entity.member_id,
            },
        )
        return entity_id
