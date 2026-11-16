from datetime import UTC, datetime

import pytest

from app.modules.sources import (
    MeetingExport,
    NotionExport,
    SlackExport,
    SourceFormatError,
    SourceType,
    meetings_to_documents,
    notion_to_documents,
    slack_to_documents,
)
from app.modules.sources.common import normalise_text

NAMES = {"u_anna": "Anna Roy", "u_ben": "Ben Das"}


def slack_export(*events: dict) -> SlackExport:
    return SlackExport.model_validate(
        {
            "workspaces": [
                {
                    "organisation_id": "org_test",
                    "workspace_name": "test",
                    "channels": [
                        {
                            "id": "ch_1",
                            "name": "team",
                            "access_group_ids": ["g_team"],
                            "events": list(events),
                        }
                    ],
                }
            ]
        }
    )


def test_slack_edit_creates_a_new_version_and_delete_a_tombstone() -> None:
    export = slack_export(
        {"client_msg_id": "m1", "user": "u_anna", "ts": "1762671600.000100", "text": "v1"},
        {
            "subtype": "message_changed",
            "client_msg_id": "m1",
            "user": "u_anna",
            "ts": "1762671700.000100",
            "text": "v2",
        },
        {
            "subtype": "message_deleted",
            "client_msg_id": "m1",
            "user": "u_anna",
            "ts": "1762671800.000100",
        },
    )

    documents = slack_to_documents(export, NAMES)

    assert [d.version for d in documents] == [1, 2, 3]
    assert [d.content for d in documents] == ["v1", "v2", ""]
    assert documents[-1].is_deleted
    assert documents[0].title == "#team message from Anna Roy"
    assert documents[0].occurred_at == datetime.fromtimestamp(1762671600.0001, tz=UTC)


def test_slack_re_exported_message_keeps_the_same_version_and_hash() -> None:
    event = {"client_msg_id": "m1", "user": "u_anna", "ts": "1762671600.000100", "text": "hi"}

    first, second = slack_to_documents(slack_export(event, event), NAMES)

    assert first.snapshot_key == second.snapshot_key == "m1@v1"
    assert first.content_hash == second.content_hash


def test_slack_edit_of_unknown_message_is_rejected() -> None:
    export = slack_export(
        {
            "subtype": "message_changed",
            "client_msg_id": "missing",
            "user": "u_anna",
            "ts": "1762671600.000100",
            "text": "x",
        }
    )

    with pytest.raises(SourceFormatError):
        slack_to_documents(export, NAMES)


def test_notion_archived_page_becomes_a_deletion() -> None:
    export = NotionExport.model_validate(
        {
            "pages": [
                {
                    "id": "p1",
                    "organisation_id": "org_test",
                    "title": "Plan",
                    "access_group_ids": ["g_team"],
                    "created_by": "u_anna",
                    "created_time": "2026-11-02T10:00:00+05:30",
                    "last_edited_by": "u_ben",
                    "last_edited_time": "2026-11-03T10:00:00+05:30",
                    "version": 2,
                    "archived": True,
                    "content": "old text",
                }
            ]
        }
    )

    (document,) = notion_to_documents(export)

    assert document.is_deleted and document.content == ""
    assert document.author_ids == ("u_anna", "u_ben")
    assert document.source_type is SourceType.DOCUMENT


def test_meeting_transcript_is_rendered_with_speaker_names() -> None:
    export = MeetingExport.model_validate(
        {
            "meetings": [
                {
                    "id": "mt1",
                    "organisation_id": "org_test",
                    "title": "Sync",
                    "access_group_ids": ["g_team"],
                    "started_at": "2026-11-10T16:00:00+05:30",
                    "participants": ["u_anna", "u_ben"],
                    "utterances": [
                        {"speaker": "u_anna", "offset_seconds": 0, "text": "Hello."},
                        {"speaker": "u_ben", "offset_seconds": 5, "text": "Hi."},
                    ],
                }
            ]
        }
    )

    (document,) = meetings_to_documents(export, NAMES)

    assert document.content == "Anna Roy: Hello.\nBen Das: Hi."
    assert document.source_type is SourceType.MEETING_TRANSCRIPT


def test_normalise_text_ignores_spacing_but_keeps_meaning() -> None:
    assert normalise_text("  Launch:\t Monday  23 Nov\r\n") == "Launch: Monday 23 Nov"
    assert normalise_text("v1.2.0 C++") == "v1.2.0 C++"
