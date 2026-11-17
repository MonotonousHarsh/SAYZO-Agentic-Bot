from uuid import uuid4

from app.modules.retrieval.entity_matching import EntityMatcher, EntityRef

ROHAN = EntityRef(uuid4(), "PERSON", "Rohan Verma")
PRIYA = EntityRef(uuid4(), "PERSON", "Priya Shah")
PRIYA_K = EntityRef(uuid4(), "PERSON", "Priya Kapoor")
ATLAS = EntityRef(uuid4(), "PROJECT", "Project Atlas")
MIGRATION = EntityRef(uuid4(), "TASK", "API migration")
ENTITIES = [ROHAN, PRIYA, PRIYA_K, ATLAS, MIGRATION]


def match(question: str) -> list[str]:
    return [e.name for e in EntityMatcher().match(question, ENTITIES)]


def test_matches_full_names_and_short_project_names() -> None:
    assert match("Who owns the API migration for Atlas?") == ["Project Atlas", "API migration"]


def test_unique_first_names_and_possessives_match() -> None:
    assert match("What is Rohan's deadline?") == ["Rohan Verma"]


def test_ambiguous_first_names_do_not_match() -> None:
    assert match("What did Priya say?") == []
    assert match("What did Priya Shah say?") == ["Priya Shah"]


def test_matching_is_word_bounded() -> None:
    assert match("Tell me about the atlases and rohanx") == []
