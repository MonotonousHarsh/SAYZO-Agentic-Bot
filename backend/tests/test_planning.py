from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.modules.ingestion.planning import Action, StoredSource, decide
from app.modules.sources import SourceDocument, SourceType

DOC = SourceDocument(
    external_id="x",
    source_type=SourceType.DOCUMENT,
    source_system="notion",
    organisation_id="org",
    title="Plan",
    content="Launch on Monday.",
    author_ids=("a",),
    access_group_ids=("g",),
    project_id=None,
    occurred_at=datetime(2026, 11, 2, tzinfo=UTC),
    version=2,
    location="notion:x",
)
DELETED = replace(DOC, version=3, content="", is_deleted=True)


def stored(version: int = 2, *, same_content: bool = True, deleted: bool = False) -> StoredSource:
    content_hash = DOC.content_hash if same_content else "other"
    return StoredSource(version=version, content_hash=content_hash, is_deleted=deleted)


@pytest.mark.parametrize(
    ("existing", "incoming", "expected"),
    [
        (None, DOC, Action.CREATE),
        (None, DELETED, Action.IGNORE),
        (stored(), DOC, Action.UNCHANGED),
        (stored(1, same_content=False), DOC, Action.UPDATE),
        (stored(2, same_content=False), DOC, Action.UPDATE),
        (stored(3), DOC, Action.IGNORE),
        (stored(), DELETED, Action.DELETE),
        (stored(3, deleted=True), DELETED, Action.UNCHANGED),
        (stored(1, deleted=True), DOC, Action.UPDATE),
        (stored(2, deleted=True), DOC, Action.IGNORE),
    ],
    ids=[
        "new",
        "deleted-before-seen",
        "same",
        "newer-version",
        "same-version-new-content",
        "stale-snapshot",
        "deleted-at-source",
        "already-deleted",
        "restored",
        "deleted-not-restored",
    ],
)
def test_decide(existing: StoredSource | None, incoming: SourceDocument, expected: Action) -> None:
    assert decide(existing, incoming) is expected
