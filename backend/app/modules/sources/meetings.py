"""Meeting transcript export: who spoke, when (offset from start) and what they said."""

from pydantic import AwareDatetime, Field

from app.modules.sources.common import (
    PeopleNames,
    SourceDocument,
    SourceType,
    StrictModel,
    display_name,
)

SOURCE_SYSTEM = "meetings"


class Utterance(StrictModel):
    speaker: str
    offset_seconds: int = Field(ge=0)
    text: str = Field(min_length=1)


class MeetingTranscript(StrictModel):
    id: str
    organisation_id: str
    title: str
    project_id: str | None = None
    access_group_ids: tuple[str, ...] = Field(min_length=1)
    started_at: AwareDatetime
    participants: tuple[str, ...] = Field(min_length=1)
    utterances: tuple[Utterance, ...] = Field(min_length=1)


class MeetingExport(StrictModel):
    meetings: tuple[MeetingTranscript, ...]


def meetings_to_documents(export: MeetingExport, people: PeopleNames) -> list[SourceDocument]:
    return [_meeting_to_document(meeting, people) for meeting in export.meetings]


def _meeting_to_document(meeting: MeetingTranscript, people: PeopleNames) -> SourceDocument:
    lines = [
        f"{display_name(people, utterance.speaker)}: {utterance.text}"
        for utterance in meeting.utterances
    ]
    return SourceDocument(
        external_id=meeting.id,
        source_type=SourceType.MEETING_TRANSCRIPT,
        source_system=SOURCE_SYSTEM,
        organisation_id=meeting.organisation_id,
        title=meeting.title,
        content="\n".join(lines),
        author_ids=meeting.participants,
        access_group_ids=meeting.access_group_ids,
        project_id=meeting.project_id,
        occurred_at=meeting.started_at,
        version=1,
        location=f"meeting:{meeting.id}",
    )
