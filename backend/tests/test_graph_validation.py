from uuid import uuid4

from app.modules.ingestion.graph import (
    EntityType,
    ExtractedEntity,
    ExtractedGraph,
    ExtractedRelationship,
    GraphValidator,
    KnownNames,
    RelationshipType,
)

ROHAN = uuid4()
KNOWN = KnownNames(
    people={"Rohan Verma": ROHAN, "Priya Nair": uuid4(), "Priya Shah": uuid4()},
    projects=["Project Atlas"],
)


def entity(name: str, entity_type: EntityType) -> ExtractedEntity:
    return ExtractedEntity(name=name, entity_type=entity_type)


def relation(subject: str, kind: RelationshipType, obj: str, confidence: float = 0.9):
    return ExtractedRelationship(
        subject=subject, relationship_type=kind, object=obj, confidence=confidence
    )


def validate(entities, relationships):  # type: ignore[no-untyped-def]
    graph = ExtractedGraph(entities=entities, relationships=relationships)
    return GraphValidator(KNOWN).validate(graph)


def test_people_and_projects_resolve_to_canonical_names() -> None:
    result = validate(
        [entity("Rohan", EntityType.PERSON), entity("Atlas", EntityType.PRODUCT)],
        [relation("Rohan", RelationshipType.WORKS_ON, "Atlas")],
    )

    rohan, atlas = result.entities
    assert (rohan.canonical_name, rohan.member_id) == ("Rohan Verma", ROHAN)
    assert (atlas.entity_type, atlas.canonical_name) == (EntityType.PROJECT, "Project Atlas")
    assert len(result.relationships) == 1 and not result.rejected


def test_ambiguous_first_name_is_not_resolved_to_a_member() -> None:
    result = validate([entity("Priya", EntityType.PERSON)], [])

    (priya,) = result.entities
    assert priya.member_id is None and priya.canonical_name == "Priya"


def test_relationship_with_wrong_types_is_rejected() -> None:
    result = validate(
        [entity("PostgreSQL", EntityType.TECHNOLOGY), entity("Rohan Verma", EntityType.PERSON)],
        [relation("PostgreSQL", RelationshipType.OWNS, "Rohan Verma")],
    )

    assert not result.relationships
    assert "not allowed for OWNS" in result.rejected[0]


def test_relationship_to_unlisted_entity_is_rejected() -> None:
    result = validate(
        [entity("Rohan Verma", EntityType.PERSON)],
        [relation("Rohan Verma", RelationshipType.WORKS_ON, "Project Zeus")],
    )

    assert "not a listed entity" in result.rejected[0]


def test_low_confidence_and_self_relationships_are_rejected() -> None:
    result = validate(
        [entity("Project Atlas", EntityType.PROJECT), entity("Rohan Verma", EntityType.PERSON)],
        [
            relation("Rohan Verma", RelationshipType.WORKS_ON, "Project Atlas", confidence=0.3),
            relation("Project Atlas", RelationshipType.DEPENDS_ON, "Atlas"),
        ],
    )

    assert not result.relationships
    assert "too low" in result.rejected[0]
    assert "itself" in result.rejected[1]
