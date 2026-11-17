"""Conversations as stored: messages and citation snapshots that stay readable after the
cited source is edited or deleted."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class ConversationNotFound(Exception):
    """No such conversation for this member (deliberately the same error when it exists but
    belongs to someone else, so ids cannot be probed)."""


@dataclass(frozen=True)
class StoredCitation:
    index: int
    source_id: UUID | None  # NULL once the source is gone
    chunk_id: UUID | None  # NULL once the chunk is re-indexed or removed
    source_version: int
    source_title: str
    excerpt: str


@dataclass(frozen=True)
class StoredMessage:
    id: UUID
    role: str
    content: str
    answer_status: str | None
    created_at: datetime
    citations: tuple[StoredCitation, ...] = ()


@dataclass(frozen=True)
class ChatTurn:
    conversation_id: UUID
    reply: StoredMessage
    replayed: bool = False  # True when a retried request returned the original reply
