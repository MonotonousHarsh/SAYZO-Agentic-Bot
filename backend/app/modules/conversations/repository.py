"""SQL for conversations. Every query is scoped by organisation; methods never commit."""

from collections import defaultdict
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Connection, text

from app.modules.answering import Citation
from app.modules.conversations.models import StoredCitation, StoredMessage


class ConversationRepository:
    def __init__(self, connection: Connection) -> None:
        self._db = connection

    def owner_of(self, organisation_id: UUID, conversation_id: UUID) -> UUID | None:
        return self._db.execute(
            text(
                "SELECT created_by FROM conversations "
                "WHERE id = :id AND organisation_id = :org AND deleted_at IS NULL"
            ),
            {"id": conversation_id, "org": organisation_id},
        ).scalar_one_or_none()

    def list_for(self, organisation_id: UUID, member_id: UUID, limit: int = 50) -> list:  # type: ignore[type-arg]
        return list(
            self._db.execute(
                text(
                    "SELECT id, title, updated_at FROM conversations WHERE organisation_id = :org "
                    "AND created_by = :member AND deleted_at IS NULL "
                    "ORDER BY updated_at DESC, id LIMIT :limit"
                ),
                {"org": organisation_id, "member": member_id, "limit": limit},
            ).all()
        )

    def create(self, organisation_id: UUID, member_id: UUID, title: str) -> UUID:
        return self._db.execute(
            text(
                "INSERT INTO conversations (organisation_id, created_by, title) "
                "VALUES (:org, :member, :title) RETURNING id"
            ),
            {"org": organisation_id, "member": member_id, "title": title},
        ).scalar_one()

    def add_message(
        self,
        organisation_id: UUID,
        conversation_id: UUID,
        role: str,
        content: str,
        answer_status: str | None = None,
        client_request_id: str | None = None,
    ) -> UUID:
        message_id = self._db.execute(
            text(
                """
                INSERT INTO messages (organisation_id, conversation_id, role, content,
                                      answer_status, client_request_id)
                VALUES (:org, :conversation, :role, :content, :status, :request)
                RETURNING id
                """
            ),
            {
                "org": organisation_id,
                "conversation": conversation_id,
                "role": role,
                "content": content,
                "status": answer_status,
                "request": client_request_id,
            },
        ).scalar_one()
        self._db.execute(
            text("UPDATE conversations SET updated_at = now() WHERE id = :id"),
            {"id": conversation_id},
        )
        return message_id

    def add_citations(
        self, organisation_id: UUID, message_id: UUID, citations: Sequence[Citation]
    ) -> None:
        for citation in citations:
            self._db.execute(
                text(
                    """
                    INSERT INTO message_citations (organisation_id, message_id, citation_index,
                        source_id, chunk_id, source_version, source_title, excerpt)
                    VALUES (:org, :message, :index, :source, :chunk, :version, :title, :excerpt)
                    """
                ),
                {
                    "org": organisation_id,
                    "message": message_id,
                    "index": citation.index,
                    "source": citation.source_id,
                    "chunk": citation.chunk_id,
                    "version": citation.source_version,
                    "title": citation.title,
                    "excerpt": citation.excerpt,
                },
            )

    def reply_for_request(
        self, organisation_id: UUID, conversation_id: UUID, client_request_id: str
    ) -> StoredMessage | None:
        message_id = self._db.execute(
            text(
                "SELECT id FROM messages WHERE organisation_id = :org "
                "AND conversation_id = :conversation AND client_request_id = :request"
            ),
            {"org": organisation_id, "conversation": conversation_id, "request": client_request_id},
        ).scalar_one_or_none()
        if message_id is None:
            return None
        return self.messages(organisation_id, conversation_id, only=message_id)[0]

    def messages(
        self, organisation_id: UUID, conversation_id: UUID, only: UUID | None = None
    ) -> list[StoredMessage]:
        rows = self._db.execute(
            text(
                """
                SELECT id, role, content, answer_status, created_at FROM messages
                WHERE organisation_id = :org AND conversation_id = :conversation
                  AND (CAST(:only AS uuid) IS NULL OR id = CAST(:only AS uuid))
                ORDER BY created_at, role DESC, id
                """
            ),
            {"org": organisation_id, "conversation": conversation_id, "only": only},
        ).all()
        citations: dict[UUID, list[StoredCitation]] = defaultdict(list)
        for row in self._db.execute(
            text(
                """
                SELECT c.message_id, c.citation_index, c.source_id, c.chunk_id,
                       c.source_version, c.source_title, c.excerpt
                FROM message_citations c
                WHERE c.organisation_id = :org AND c.message_id = ANY(CAST(:ids AS uuid[]))
                ORDER BY c.message_id, c.citation_index
                """
            ),
            {"org": organisation_id, "ids": [str(r.id) for r in rows]},
        ).all():
            citations[row.message_id].append(
                StoredCitation(
                    row.citation_index,
                    row.source_id,
                    row.chunk_id,
                    row.source_version,
                    row.source_title,
                    row.excerpt,
                )
            )
        return [
            StoredMessage(
                r.id, r.role, r.content, r.answer_status, r.created_at, tuple(citations[r.id])
            )
            for r in rows
        ]
