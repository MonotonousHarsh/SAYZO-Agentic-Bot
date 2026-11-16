"""Read-only lookups over a loaded dataset, built once and shared by all validation rules."""

from collections import defaultdict
from functools import cached_property

from app.modules.sources import Organisation, Person, SourceDocument
from app.modules.synthetic_data.loader import SyntheticDataset


class DatasetIndex:
    def __init__(self, dataset: SyntheticDataset) -> None:
        self.dataset = dataset

    @cached_property
    def organisations(self) -> dict[str, Organisation]:
        return {org.id: org for org in self.dataset.directory.organisations}

    @cached_property
    def people(self) -> dict[str, tuple[Organisation, Person]]:
        return {person.id: (org, person) for org, person in self.dataset.directory.people()}

    @cached_property
    def group_owner(self) -> dict[str, str]:
        """Access group id -> id of the organisation that owns it."""
        return {g.id: org.id for org in self.organisations.values() for g in org.access_groups}

    @cached_property
    def project_owner(self) -> dict[str, str]:
        """Project id -> id of the organisation that owns it."""
        return {p.id: org.id for org in self.organisations.values() for p in org.projects}

    @cached_property
    def snapshots(self) -> dict[str, list[SourceDocument]]:
        """External id -> all of its snapshots, in source order."""
        grouped: dict[str, list[SourceDocument]] = defaultdict(list)
        for document in self.dataset.documents:
            grouped[document.external_id].append(document)
        return dict(grouped)

    @cached_property
    def current(self) -> dict[str, SourceDocument]:
        """External id -> its latest snapshot (which may be a deletion tombstone)."""
        return {
            external_id: max(snapshots, key=lambda doc: doc.version)
            for external_id, snapshots in self.snapshots.items()
        }

    def is_live(self, external_id: str) -> bool:
        document = self.current.get(external_id)
        return document is not None and not document.is_deleted

    def can_view(self, person_id: str, external_id: str) -> bool:
        """A person may see a source if it is in their organisation and shares an access group.

        This mirrors the rule retrieval will enforce: organisation first, then access groups.
        """
        entry = self.people.get(person_id)
        document = self.current.get(external_id)
        if entry is None or document is None:
            return False
        organisation, person = entry
        if document.organisation_id != organisation.id:
            return False
        return bool(set(person.access_group_ids) & set(document.access_group_ids))
