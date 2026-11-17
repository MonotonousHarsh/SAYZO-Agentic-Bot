"""Find which known entities a question mentions, so the graph can be searched from them.

Matching is deterministic and word-bounded: "Atlas" matches the project "Project Atlas", and
"Rohan" matches "Rohan Verma" only if no other person shares that first name.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.shared.text import normalise_name

MIN_VARIANT_LENGTH = 3


@dataclass(frozen=True)
class EntityRef:
    id: UUID
    entity_type: str
    name: str


class EntityMatcher:
    def match(self, question: str, entities: Sequence[EntityRef]) -> list[EntityRef]:
        haystack = f" {normalise_name(question)} "
        first_names = Counter(
            normalise_name(e.name).split(" ")[0] for e in entities if e.entity_type == "PERSON"
        )
        matched = []
        for entity in entities:
            variants = self._variants(entity, first_names)
            if any(f" {variant} " in haystack for variant in variants):
                matched.append(entity)
        return sorted(matched, key=lambda e: (e.entity_type, e.name))

    @staticmethod
    def _variants(entity: EntityRef, first_names: Counter[str]) -> set[str]:
        name = normalise_name(entity.name)
        variants = {name}
        if entity.entity_type == "PROJECT" and name.startswith("project "):
            variants.add(name.removeprefix("project "))
        if entity.entity_type == "PERSON":
            first = name.split(" ")[0]
            if first_names[first] == 1:
                variants.add(first)
        return {v for v in variants if len(v) >= MIN_VARIANT_LENGTH}
