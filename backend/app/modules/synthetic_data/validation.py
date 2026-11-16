"""Dataset validation.

Each rule checks one property of the dataset and reports issues; the validator runs them all.
New checks are added as new rule classes, without changing the validator (open/closed).
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from app.modules.sources import Role, SourceType
from app.modules.synthetic_data.evaluation import EvaluationQuestion, ExpectedBehaviour
from app.modules.synthetic_data.index import DatasetIndex
from app.modules.synthetic_data.loader import SyntheticDataset

MIN_ORGANISATIONS = 2  # tenant isolation cannot be tested with one organisation
MIN_EVALUATION_QUESTIONS = 5  # required by the assessment brief


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    subject: str | None = None

    def __str__(self) -> str:
        where = f" [{self.subject}]" if self.subject else ""
        return f"{self.severity} {self.code}{where}: {self.message}"


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def codes(self) -> set[str]:
        return {issue.code for issue in self.issues}


class ValidationRule(Protocol):
    def check(self, index: DatasetIndex) -> Iterable[ValidationIssue]: ...


def _error(code: str, message: str, subject: str | None = None) -> ValidationIssue:
    return ValidationIssue(Severity.ERROR, code, message, subject)


class UniqueIdsRule:
    """Directory ids and question ids must be unique across the whole dataset."""

    def check(self, index: DatasetIndex) -> Iterable[ValidationIssue]:
        directory = index.dataset.directory
        ids: list[str] = []
        for org in directory.organisations:
            ids.append(org.id)
            ids.extend(p.id for p in org.people)
            ids.extend(g.id for g in org.access_groups)
            ids.extend(p.id for p in org.projects)
        ids.extend(q.id for q in index.dataset.questions)
        for duplicate_id, count in Counter(ids).items():
            if count > 1:
                yield _error("DUPLICATE_ID", f"id is used {count} times", duplicate_id)


class DirectoryIntegrityRule:
    """People may only belong to access groups of their own organisation."""

    def check(self, index: DatasetIndex) -> Iterable[ValidationIssue]:
        for org, person in index.dataset.directory.people():
            for group_id in person.access_group_ids:
                if index.group_owner.get(group_id) != org.id:
                    yield _error(
                        "FOREIGN_GROUP",
                        f"belongs to group {group_id}, which is not in {org.id}",
                        person.id,
                    )


class SourceReferenceRule:
    """Every source must point at its own organisation's people, groups and projects."""

    def check(self, index: DatasetIndex) -> Iterable[ValidationIssue]:
        for doc in index.dataset.documents:
            subject = doc.snapshot_key
            if doc.organisation_id not in index.organisations:
                yield _error("UNKNOWN_ORGANISATION", doc.organisation_id, subject)
                continue
            for author_id in doc.author_ids:
                entry = index.people.get(author_id)
                if entry is None:
                    yield _error("UNKNOWN_PERSON", f"author {author_id} not found", subject)
                elif entry[0].id != doc.organisation_id:
                    yield _error("CROSS_TENANT_AUTHOR", f"author {author_id}", subject)
            for group_id in doc.access_group_ids:
                if index.group_owner.get(group_id) != doc.organisation_id:
                    yield _error("FOREIGN_GROUP", f"access group {group_id}", subject)
            if doc.project_id and index.project_owner.get(doc.project_id) != doc.organisation_id:
                yield _error("FOREIGN_PROJECT", f"project {doc.project_id}", subject)


class TimelineRule:
    """Timestamps sit inside the manifest window and each new version is later than the last."""

    def check(self, index: DatasetIndex) -> Iterable[ValidationIssue]:
        window = index.dataset.manifest.time_window
        for doc in index.dataset.documents:
            if not window.contains(doc.occurred_at):
                yield _error("OUTSIDE_TIME_WINDOW", doc.occurred_at.isoformat(), doc.snapshot_key)
        for external_id, snapshots in index.snapshots.items():
            versions = sorted({doc.version for doc in snapshots})
            if versions != list(range(1, len(versions) + 1)):
                yield _error("VERSION_GAP", f"versions are {versions}", external_id)
            latest_time_by_version = {doc.version: doc.occurred_at for doc in snapshots}
            ordered = [latest_time_by_version[v] for v in versions]
            if any(later <= earlier for earlier, later in zip(ordered, ordered[1:], strict=False)):
                yield _error("VERSION_TIME_ORDER", "a newer version is not later", external_id)
            hashes_per_version: dict[int, set[str]] = defaultdict(set)
            for doc in snapshots:
                hashes_per_version[doc.version].add(doc.content_hash)
            for version, hashes in hashes_per_version.items():
                if len(hashes) > 1:
                    yield _error(
                        "CONFLICTING_SNAPSHOTS",
                        f"version {version} appears with different content",
                        external_id,
                    )
            if any(doc.is_deleted for doc in snapshots[:-1]):
                yield _error("CHANGE_AFTER_DELETE", "source changes after deletion", external_id)


class DuplicateDeclarationRule:
    """Exact duplicates must be declared in the manifest, and declared ones must be real."""

    def check(self, index: DatasetIndex) -> Iterable[ValidationIssue]:
        by_hash: dict[str, list[str]] = defaultdict(list)
        for doc in index.dataset.documents:
            if not doc.is_deleted:
                by_hash[doc.content_hash].append(doc.external_id)

        found = {frozenset(ids) for ids in by_hash.values() if len(ids) > 1}
        declared = {frozenset(group) for group in index.dataset.manifest.edge_cases.duplicates}

        for group in found - declared:
            yield _error("UNDECLARED_DUPLICATE", "identical content", ",".join(sorted(group)))
        for group in declared - found:
            yield _error(
                "DECLARED_DUPLICATE_MISSING", "content is not identical", ",".join(sorted(group))
            )


class EdgeCaseRule:
    """Each declared edge case must really be present in the data."""

    def check(self, index: DatasetIndex) -> Iterable[ValidationIssue]:
        cases = index.dataset.manifest.edge_cases
        for external_id in cases.edits:
            if len(index.snapshots.get(external_id, [])) < 2 or not index.is_live(external_id):
                yield _error("EDIT_NOT_PRESENT", "needs 2+ versions and to be live", external_id)
        for external_id in cases.deletions:
            current = index.current.get(external_id)
            if current is None or not current.is_deleted:
                yield _error("DELETION_NOT_PRESENT", "latest snapshot is not deleted", external_id)
        for group in cases.conflicts:
            if len(group) < 2 or not all(index.is_live(i) for i in group):
                yield _error("CONFLICT_NOT_PRESENT", "needs 2+ live sources", ",".join(group))
        for external_id in cases.restricted:
            yield from self._check_restricted(index, external_id)
        for external_id in cases.prompt_injections:
            if not index.is_live(external_id):
                yield _error("INJECTION_NOT_PRESENT", "source is not live", external_id)

    @staticmethod
    def _check_restricted(index: DatasetIndex, external_id: str) -> Iterable[ValidationIssue]:
        current = index.current.get(external_id)
        if current is None:
            yield _error("RESTRICTED_NOT_PRESENT", "source not found", external_id)
            return
        org = index.organisations[current.organisation_id]
        if all(index.can_view(person.id, external_id) for person in org.people):
            yield _error(
                "NOT_RESTRICTED", "every member of its organisation can see it", external_id
            )


class CoverageRule:
    """The dataset must exercise every source type, tenant isolation and enough questions."""

    def check(self, index: DatasetIndex) -> Iterable[ValidationIssue]:
        present = {doc.source_type for doc in index.dataset.documents}
        for source_type in SourceType:
            if source_type not in present:
                yield _error("MISSING_SOURCE_TYPE", f"no {source_type} sources")
        if len(index.organisations) < MIN_ORGANISATIONS:
            yield _error("TOO_FEW_ORGANISATIONS", f"need at least {MIN_ORGANISATIONS}")
        if len(index.dataset.questions) < MIN_EVALUATION_QUESTIONS:
            yield _error("TOO_FEW_QUESTIONS", f"need at least {MIN_EVALUATION_QUESTIONS}")
        behaviours = {q.expected_behaviour for q in index.dataset.questions}
        for behaviour in ExpectedBehaviour:
            if behaviour not in behaviours:
                yield ValidationIssue(
                    Severity.WARNING, "BEHAVIOUR_NOT_TESTED", f"no {behaviour} question"
                )


class EvaluationGroundTruthRule:
    """Every question's expectations must agree with the data and the access model."""

    def check(self, index: DatasetIndex) -> Iterable[ValidationIssue]:
        for question in index.dataset.questions:
            yield from self._check_question(index, question)

    def _check_question(
        self, index: DatasetIndex, question: EvaluationQuestion
    ) -> Iterable[ValidationIssue]:
        subject = question.id
        entry = index.people.get(question.asked_by)
        if entry is None or entry[0].id != question.organisation_id:
            yield _error("UNKNOWN_ASKER", f"{question.asked_by} not in organisation", subject)
            return
        _, asker = entry

        for source_id in question.expected_source_ids:
            if not index.is_live(source_id):
                yield _error("EXPECTED_SOURCE_NOT_LIVE", source_id, subject)
            elif not index.can_view(asker.id, source_id):
                yield _error("EXPECTED_SOURCE_NOT_VISIBLE", source_id, subject)

        for source_id in question.forbidden_source_ids:
            if source_id not in index.current:
                yield _error("UNKNOWN_FORBIDDEN_SOURCE", source_id, subject)
            elif index.is_live(source_id) and index.can_view(asker.id, source_id):
                yield _error(
                    "FORBIDDEN_SOURCE_IS_ALLOWED",
                    f"{source_id} is live and visible to the asker",
                    subject,
                )

        yield from self._check_behaviour(index, question, asker.role)

    @staticmethod
    def _check_behaviour(
        index: DatasetIndex, question: EvaluationQuestion, asker_role: Role
    ) -> Iterable[ValidationIssue]:
        subject = question.id
        behaviour = question.expected_behaviour
        sources = question.expected_source_ids

        if behaviour in (ExpectedBehaviour.ANSWER, ExpectedBehaviour.TASK_PROPOSAL) and not sources:
            yield _error("MISSING_EXPECTED_SOURCES", f"{behaviour} needs sources", subject)
        if behaviour is ExpectedBehaviour.CONFLICT and len(sources) < 2:
            yield _error("CONFLICT_NEEDS_TWO_SOURCES", "list both sides", subject)
        if behaviour in (ExpectedBehaviour.ABSTAIN, ExpectedBehaviour.DENIED) and sources:
            yield _error("UNEXPECTED_SOURCES", f"{behaviour} must not cite sources", subject)
        if behaviour is ExpectedBehaviour.DENIED and asker_role is not Role.VIEWER:
            yield _error("DENIAL_NOT_JUSTIFIED", f"{asker_role} may create tasks", subject)
        if behaviour is ExpectedBehaviour.TASK_PROPOSAL:
            if asker_role is Role.VIEWER:
                yield _error("VIEWER_CANNOT_CREATE_TASKS", "asker is a VIEWER", subject)
            if not question.expected_tasks:
                yield _error("MISSING_EXPECTED_TASKS", "list the expected tasks", subject)
            for task in question.expected_tasks:
                assignee = index.people.get(task.assignee_id)
                if assignee is None or assignee[0].id != question.organisation_id:
                    yield _error("INVALID_ASSIGNEE", task.assignee_id, subject)
        elif question.expected_tasks:
            yield _error("UNEXPECTED_TASKS", "only TASK_PROPOSAL lists tasks", subject)


def default_rules() -> Sequence[ValidationRule]:
    return (
        UniqueIdsRule(),
        DirectoryIntegrityRule(),
        SourceReferenceRule(),
        TimelineRule(),
        DuplicateDeclarationRule(),
        EdgeCaseRule(),
        CoverageRule(),
        EvaluationGroundTruthRule(),
    )


class DatasetValidator:
    def __init__(self, rules: Sequence[ValidationRule] | None = None) -> None:
        self._rules = tuple(rules) if rules is not None else tuple(default_rules())

    def validate(self, dataset: SyntheticDataset) -> ValidationReport:
        index = DatasetIndex(dataset)
        issues = tuple(issue for rule in self._rules for issue in rule.check(index))
        return ValidationReport(issues)
