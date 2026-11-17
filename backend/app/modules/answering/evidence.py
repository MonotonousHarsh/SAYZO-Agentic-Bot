"""Decide which retrieved chunks are relevant enough to show the model, and whether any are.

Retrieval always returns its nearest chunks, even for questions nothing answers. Unrelated
text scores about 0.40 cosine similarity with bge; real matches score well above. Chunks
below the threshold are dropped unless the graph linked them to an entity in the question.
If nothing survives, the answer is "not enough evidence" without calling the model at all.
"""

from dataclasses import dataclass

from app.modules.retrieval import Channel, RetrievalResult, SearchHit


@dataclass(frozen=True)
class EvidencePolicy:
    min_similarity: float = 0.5
    max_sources: int = 6

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_similarity <= 1.0:
            raise ValueError("min_similarity must be between 0 and 1")
        if self.max_sources < 1:
            raise ValueError("max_sources must be at least 1")


class EvidenceSelector:
    def __init__(self, policy: EvidencePolicy | None = None) -> None:
        self._policy = policy or EvidencePolicy()

    def select(self, result: RetrievalResult) -> tuple[SearchHit, ...]:
        relevant = [hit for hit in result.hits if self._is_relevant(hit)]
        return tuple(relevant[: self._policy.max_sources])

    def _is_relevant(self, hit: SearchHit) -> bool:
        if Channel.GRAPH in hit.ranks:
            return True
        return hit.similarity is not None and hit.similarity >= self._policy.min_similarity
