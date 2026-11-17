"""Deterministic checks on an LLM-proposed graph, and resolution to canonical names.

Nothing the model returns is trusted as-is. Names are normalised and resolved against the
organisation's known people and projects, relationship types are checked against the allowed
subject/object types, and low-confidence or malformed proposals are rejected with a reason.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from app.modules.ingestion.graph.schemas import (
    ALLOWED_RELATIONSHIPS,
    EntityType,
    ExtractedGraph,
    ExtractedRelationship,
    RelationshipType,
)

_NON_WORD = re.compile(r"[^\w\s-]")
_SPACES = re.compile(r"\s+")


def normalise_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).casefold()
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


@dataclass(frozen=True)
class KnownNames:
    """An organisation's people (full name -> member id) and project names."""

    people: Mapping[str, UUID]
    projects: Sequence[str]


@dataclass(frozen=True)
class ResolvedEntity:
    entity_type: EntityType
    canonical_name: str
    normalized_name: str
    member_id: UUID | None = None

    @property
    def key(self) -> tuple[EntityType, str]:
        return self.entity_type, self.normalized_name


@dataclass(frozen=True)
class ResolvedRelationship:
    subject: ResolvedEntity
    relationship_type: RelationshipType
    object: ResolvedEntity
    confidence: float


@dataclass
class ValidatedGraph:
    entities: list[ResolvedEntity] = field(default_factory=list)
    relationships: list[ResolvedRelationship] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


class GraphValidator:
    def __init__(self, known: KnownNames, min_confidence: float = 0.6) -> None:
        self._min_confidence = min_confidence
        self._people_by_full = {normalise_name(n): (n, m) for n, m in known.people.items()}
        first_names: dict[str, list[tuple[str, UUID]]] = {}
        for full_name, member in known.people.items():
            first = normalise_name(full_name).split(" ")[0]
            first_names.setdefault(first, []).append((full_name, member))
        # A first name only resolves when exactly one person in the organisation has it.
        self._people_by_first = {k: v[0] for k, v in first_names.items() if len(v) == 1}
        self._projects: dict[str, str] = {}
        for project in known.projects:
            normalised = normalise_name(project)
            self._projects[normalised] = project
            if normalised.startswith("project "):
                self._projects[normalised.removeprefix("project ")] = project

    def validate(self, graph: ExtractedGraph) -> ValidatedGraph:
        result = ValidatedGraph()
        by_name: dict[str, ResolvedEntity] = {}
        for entity in graph.entities:
            resolved = self._resolve(entity.name, entity.entity_type)
            if resolved is None:
                result.rejected.append(f"entity with empty name: {entity.name!r}")
                continue
            by_name[normalise_name(entity.name)] = resolved
            if resolved.key not in {e.key for e in result.entities}:
                result.entities.append(resolved)

        for relationship in graph.relationships:
            reason = self._check_relationship(relationship, by_name)
            if reason:
                result.rejected.append(reason)
                continue
            subject = self._lookup(relationship.subject, by_name)
            obj = self._lookup(relationship.object, by_name)
            assert subject is not None and obj is not None
            result.relationships.append(
                ResolvedRelationship(
                    subject=subject,
                    relationship_type=relationship.relationship_type,
                    object=obj,
                    confidence=min(max(relationship.confidence, 0.0), 1.0),
                )
            )
        return result

    def _resolve(self, name: str, entity_type: EntityType) -> ResolvedEntity | None:
        normalised = normalise_name(name)
        if not normalised:
            return None
        person = self._people_by_full.get(normalised) or self._people_by_first.get(normalised)
        if person is not None:
            full_name, member_id = person
            return ResolvedEntity(
                EntityType.PERSON, full_name, normalise_name(full_name), member_id
            )
        if normalised in self._projects:
            project = self._projects[normalised]
            return ResolvedEntity(EntityType.PROJECT, project, normalise_name(project))
        return ResolvedEntity(entity_type, " ".join(name.split()), normalised)

    def _lookup(self, name: str, by_name: dict[str, ResolvedEntity]) -> ResolvedEntity | None:
        """Find a relationship endpoint among the listed entities, allowing name variants.

        "Atlas" in a relationship matches a listed "Project Atlas"; "Rohan" matches "Rohan Verma".
        """
        direct = by_name.get(normalise_name(name))
        if direct is not None:
            return direct
        resolved = self._resolve(name, EntityType.TASK)
        if resolved is None:
            return None
        return next((e for e in by_name.values() if e.key == resolved.key), None)

    def _check_relationship(
        self, relationship: ExtractedRelationship, by_name: dict[str, ResolvedEntity]
    ) -> str | None:
        label = f"{relationship.subject} {relationship.relationship_type} {relationship.object}"
        subject = self._lookup(relationship.subject, by_name)
        obj = self._lookup(relationship.object, by_name)
        if subject is None or obj is None:
            return f"{label}: subject or object is not a listed entity"
        if subject.key == obj.key:
            return f"{label}: an entity cannot relate to itself"
        if relationship.confidence < self._min_confidence:
            return f"{label}: confidence {relationship.confidence:.2f} is too low"
        allowed_subjects, allowed_objects = ALLOWED_RELATIONSHIPS[relationship.relationship_type]
        if subject.entity_type not in allowed_subjects or obj.entity_type not in allowed_objects:
            return (
                f"{label}: {subject.entity_type} -> {obj.entity_type} is not allowed for "
                f"{relationship.relationship_type}"
            )
        return None
