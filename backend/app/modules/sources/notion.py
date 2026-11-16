"""Notion-style page export. Each entry is one saved version (snapshot) of a page."""

from pydantic import AwareDatetime, Field

from app.modules.sources.common import SourceDocument, SourceType, StrictModel

SOURCE_SYSTEM = "notion"


class NotionPage(StrictModel):
    id: str
    organisation_id: str
    title: str
    project_id: str | None = None
    access_group_ids: tuple[str, ...] = Field(min_length=1)
    created_by: str
    created_time: AwareDatetime
    last_edited_by: str
    last_edited_time: AwareDatetime
    version: int = Field(ge=1)
    archived: bool = False
    content: str = Field(min_length=1)


class NotionExport(StrictModel):
    pages: tuple[NotionPage, ...]


def notion_to_documents(export: NotionExport) -> list[SourceDocument]:
    return [_page_to_document(page) for page in export.pages]


def _page_to_document(page: NotionPage) -> SourceDocument:
    authors = tuple(dict.fromkeys((page.created_by, page.last_edited_by)))
    return SourceDocument(
        external_id=page.id,
        source_type=SourceType.DOCUMENT,
        source_system=SOURCE_SYSTEM,
        organisation_id=page.organisation_id,
        title=page.title,
        content="" if page.archived else page.content,
        author_ids=authors,
        access_group_ids=page.access_group_ids,
        project_id=page.project_id,
        occurred_at=page.last_edited_time,
        version=page.version,
        location=f"notion:{page.id}",
        is_deleted=page.archived,
    )
