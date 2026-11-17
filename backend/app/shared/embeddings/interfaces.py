"""The contract every embedding provider implements."""

from collections.abc import Sequence
from typing import Protocol


class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages for storage. Returns one unit-length vector per text, in order."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query (some models encode queries differently from passages)."""
        ...


def to_pgvector_literal(vector: Sequence[float]) -> str:
    """Format a vector the way pgvector parses it, e.g. '[0.1,0.2]'."""
    return "[" + ",".join(f"{value:.7f}" for value in vector) + "]"
