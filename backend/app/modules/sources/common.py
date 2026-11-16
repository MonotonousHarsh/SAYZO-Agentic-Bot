"""The common shape every source is converted into, plus shared helpers."""

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import cached_property

from pydantic import BaseModel, ConfigDict

# Maps a person id to their display name. Unknown ids fall back to the id itself;
# the dataset validator reports them as reference errors.
PeopleNames = Mapping[str, str]

_WHITESPACE = re.compile(r"[ \t]+")


class StrictModel(BaseModel):
    """Base for raw source schemas: unknown fields are rejected, parsed objects are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceFormatError(ValueError):
    """Raised when a raw export is structurally impossible (e.g. an edit of an unknown message)."""


class SourceType(StrEnum):
    MESSAGE = "message"
    DOCUMENT = "document"
    MEETING_TRANSCRIPT = "meeting_transcript"


@dataclass(frozen=True)
class SourceDocument:
    """One snapshot of one piece of company knowledge.

    A source that is edited produces several snapshots with the same `external_id` and an
    increasing `version`. A deletion is a snapshot with `is_deleted=True` and empty content.
    """

    external_id: str
    source_type: SourceType
    source_system: str
    organisation_id: str
    title: str
    content: str
    author_ids: tuple[str, ...]
    access_group_ids: tuple[str, ...]
    project_id: str | None
    occurred_at: datetime
    version: int
    location: str
    is_deleted: bool = False

    @property
    def snapshot_key(self) -> str:
        return f"{self.external_id}@v{self.version}"

    @cached_property
    def content_hash(self) -> str:
        """SHA-256 of the normalised title and content: equal hashes mean exact duplicates."""
        normalised = normalise_text(f"{self.title}\n{self.content}")
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def normalise_text(text: str) -> str:
    """Make text comparable without changing its meaning.

    Unicode is NFKC-normalised, line endings unified, runs of spaces/tabs collapsed and
    blank edges trimmed. Punctuation, numbers and casing are kept: they carry meaning.
    """
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = (_WHITESPACE.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(lines).strip()


def display_name(people: PeopleNames, person_id: str) -> str:
    return people.get(person_id, person_id)
