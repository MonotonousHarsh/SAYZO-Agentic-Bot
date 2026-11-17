"""Controlled vocabulary for the graph, and the JSON shape the LLM must return.

The vocabulary is closed on purpose: a free-text relationship name would let the model invent
synonyms ("OWNER_OF", "IS_OWNER", ...) that retrieval could never match reliably.
"""

from enum import StrEnum

from app.modules.sources.common import StrictModel


class EntityType(StrEnum):
    PERSON = "PERSON"
    PROJECT = "PROJECT"
    TEAM = "TEAM"
    TASK = "TASK"
    TECHNOLOGY = "TECHNOLOGY"
    CUSTOMER = "CUSTOMER"
    PRODUCT = "PRODUCT"


class RelationshipType(StrEnum):
    OWNS = "OWNS"
    RESPONSIBLE_FOR = "RESPONSIBLE_FOR"
    ASSIGNED_TO = "ASSIGNED_TO"
    WORKS_ON = "WORKS_ON"
    DEPENDS_ON = "DEPENDS_ON"
    BLOCKED_BY = "BLOCKED_BY"
    USES = "USES"
    PART_OF = "PART_OF"


_E = EntityType
# Which entity types may appear on each side of each relationship (subject -> object).
ALLOWED_RELATIONSHIPS: dict[
    RelationshipType, tuple[frozenset[EntityType], frozenset[EntityType]]
] = {
    RelationshipType.OWNS: (frozenset({_E.PERSON, _E.TEAM}), frozenset({_E.PROJECT, _E.PRODUCT})),
    RelationshipType.RESPONSIBLE_FOR: (
        frozenset({_E.PERSON, _E.TEAM}),
        frozenset({_E.TASK, _E.PROJECT}),
    ),
    RelationshipType.ASSIGNED_TO: (frozenset({_E.PERSON}), frozenset({_E.PROJECT, _E.TASK})),
    RelationshipType.WORKS_ON: (
        frozenset({_E.PERSON, _E.TEAM}),
        frozenset({_E.PROJECT, _E.PRODUCT, _E.TASK}),
    ),
    RelationshipType.DEPENDS_ON: (
        frozenset({_E.PROJECT, _E.TASK, _E.PRODUCT}),
        frozenset({_E.PROJECT, _E.TASK, _E.PRODUCT, _E.TECHNOLOGY}),
    ),
    RelationshipType.BLOCKED_BY: (
        frozenset({_E.PROJECT, _E.TASK}),
        frozenset({_E.PROJECT, _E.TASK}),
    ),
    RelationshipType.USES: (
        frozenset({_E.PROJECT, _E.PRODUCT, _E.TEAM}),
        frozenset({_E.TECHNOLOGY}),
    ),
    RelationshipType.PART_OF: (
        frozenset({_E.TASK, _E.PROJECT, _E.TEAM, _E.PRODUCT}),
        frozenset({_E.PROJECT, _E.PRODUCT, _E.TEAM}),
    ),
}


class ExtractedEntity(StrictModel):
    name: str
    entity_type: EntityType


class ExtractedRelationship(StrictModel):
    subject: str
    relationship_type: RelationshipType
    object: str
    confidence: float


class ExtractedGraph(StrictModel):
    entities: list[ExtractedEntity]
    relationships: list[ExtractedRelationship]
