"""Source connectors: raw formats from each data source and adapters to one common shape.

Every source (Slack-style messages, Notion-style pages, meeting transcripts) has its own raw
format. Each adapter turns its raw format into `SourceDocument` snapshots, so the rest of the
system (validation now, ingestion later) only ever deals with one shape.
"""

from app.modules.sources.common import SourceDocument, SourceFormatError, SourceType
from app.modules.sources.directory import Directory, Organisation, Person, Role
from app.modules.sources.meetings import MeetingExport, meetings_to_documents
from app.modules.sources.notion import NotionExport, notion_to_documents
from app.modules.sources.slack import SlackExport, slack_to_documents

__all__ = [
    "Directory",
    "MeetingExport",
    "NotionExport",
    "Organisation",
    "Person",
    "Role",
    "SlackExport",
    "SourceDocument",
    "SourceFormatError",
    "SourceType",
    "meetings_to_documents",
    "notion_to_documents",
    "slack_to_documents",
]
