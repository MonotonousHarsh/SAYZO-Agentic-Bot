"""Decides what an incoming source snapshot means for the stored source. Pure logic, no I/O."""

from dataclasses import dataclass
from enum import StrEnum

from app.modules.sources import SourceDocument


class Action(StrEnum):
    CREATE = "create"  # never seen: store and index it
    UPDATE = "update"  # newer version or restored: re-index it
    DELETE = "delete"  # deleted at the source: remove it from search
    UNCHANGED = "unchanged"  # same version and content: only refresh access metadata
    IGNORE = "ignore"  # older than what we have, or deleted before we ever saw it


@dataclass(frozen=True)
class StoredSource:
    version: int
    content_hash: str
    is_deleted: bool


def decide(stored: StoredSource | None, incoming: SourceDocument) -> Action:
    if stored is None:
        return Action.IGNORE if incoming.is_deleted else Action.CREATE
    if incoming.version < stored.version:
        # Never move backwards: an old snapshot arriving late must not undo a newer edit.
        return Action.IGNORE
    if incoming.is_deleted:
        return Action.UNCHANGED if stored.is_deleted else Action.DELETE
    if stored.is_deleted:
        return Action.UPDATE if incoming.version > stored.version else Action.IGNORE
    if incoming.version == stored.version and incoming.content_hash == stored.content_hash:
        return Action.UNCHANGED
    return Action.UPDATE
