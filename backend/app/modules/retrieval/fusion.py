"""Reciprocal rank fusion: merge several ranked lists without comparing their raw scores.

Each list votes 1 / (k + rank) for every item it contains. Vector distances, text-search
ranks and graph counts are on different scales, so only the positions are used.
"""

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T", bound=Hashable)
K = TypeVar("K", bound=Hashable)


@dataclass(frozen=True)
class Fused(Generic[T, K]):
    item: T
    score: float
    ranks: Mapping[K, int]  # 1-based rank in each list that contained the item


def reciprocal_rank_fusion(
    rankings: Mapping[K, Sequence[T]],
    *,
    k: int = 60,
    weights: Mapping[K, float] | None = None,
) -> list[Fused[T, K]]:
    if k <= 0:
        raise ValueError("k must be positive")
    scores: dict[T, float] = {}
    ranks: dict[T, dict[K, int]] = {}
    for name, items in rankings.items():
        weight = 1.0 if weights is None else weights.get(name, 1.0)
        seen: set[T] = set()
        for position, item in enumerate(items, start=1):
            if item in seen:  # a list may not vote twice for the same item
                continue
            seen.add(item)
            scores[item] = scores.get(item, 0.0) + weight / (k + position)
            ranks.setdefault(item, {})[name] = position
    fused = [Fused(item, scores[item], ranks[item]) for item in scores]
    # Ties: more lists agreeing first, then the best single rank, then a stable order.
    fused.sort(key=lambda f: (-f.score, -len(f.ranks), min(f.ranks.values()), str(f.item)))
    return fused
