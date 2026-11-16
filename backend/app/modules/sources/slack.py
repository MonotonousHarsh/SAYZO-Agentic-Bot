"""Slack-style message export (simplified).

Each channel holds an ordered event stream. A message is identified by `client_msg_id`;
`message_changed` creates a new version of it and `message_deleted` tombstones it. The same
event can appear twice when a channel is re-exported, which the adapter passes through
unchanged: detecting that duplicate is the job of whoever consumes the documents.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, model_validator

from app.modules.sources.common import (
    PeopleNames,
    SourceDocument,
    SourceFormatError,
    SourceType,
    StrictModel,
    display_name,
)

SOURCE_SYSTEM = "slack"


class SlackSubtype(StrEnum):
    MESSAGE_CHANGED = "message_changed"
    MESSAGE_DELETED = "message_deleted"


class SlackEvent(StrictModel):
    type: str = "message"
    subtype: SlackSubtype | None = None
    client_msg_id: str
    user: str
    ts: str = Field(pattern=r"^\d{10}\.\d{6}$")
    text: str | None = None

    @model_validator(mode="after")
    def text_required_unless_deleted(self) -> "SlackEvent":
        if self.subtype is not SlackSubtype.MESSAGE_DELETED and not self.text:
            raise ValueError(f"event for {self.client_msg_id} needs text")
        return self

    @property
    def occurred_at(self) -> datetime:
        return datetime.fromtimestamp(float(self.ts), tz=UTC)


class SlackChannel(StrictModel):
    id: str
    name: str
    access_group_ids: tuple[str, ...] = Field(min_length=1)
    project_id: str | None = None
    events: tuple[SlackEvent, ...]


class SlackWorkspace(StrictModel):
    organisation_id: str
    workspace_name: str
    channels: tuple[SlackChannel, ...]


class SlackExport(StrictModel):
    workspaces: tuple[SlackWorkspace, ...]


def slack_to_documents(export: SlackExport, people: PeopleNames) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    for workspace in export.workspaces:
        for channel in workspace.channels:
            documents.extend(_channel_to_documents(workspace, channel, people))
    return documents


def _channel_to_documents(
    workspace: SlackWorkspace, channel: SlackChannel, people: PeopleNames
) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    current_version: dict[str, int] = {}

    for event in channel.events:
        known = event.client_msg_id in current_version
        if event.subtype is None:
            # A repeated plain message is a re-export: same version, same content.
            version = current_version.get(event.client_msg_id, 1)
        elif not known:
            raise SourceFormatError(
                f"#{channel.name}: {event.subtype} for unknown message {event.client_msg_id}"
            )
        else:
            version = current_version[event.client_msg_id] + 1
        current_version[event.client_msg_id] = version

        is_deleted = event.subtype is SlackSubtype.MESSAGE_DELETED
        documents.append(
            SourceDocument(
                external_id=event.client_msg_id,
                source_type=SourceType.MESSAGE,
                source_system=SOURCE_SYSTEM,
                organisation_id=workspace.organisation_id,
                title=f"#{channel.name} message from {display_name(people, event.user)}",
                content="" if is_deleted else (event.text or ""),
                author_ids=(event.user,),
                access_group_ids=channel.access_group_ids,
                project_id=channel.project_id,
                occurred_at=event.occurred_at,
                version=version,
                location=f"#{channel.name}",
                is_deleted=is_deleted,
            )
        )
    return documents
