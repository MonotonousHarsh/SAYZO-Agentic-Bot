"""Text embeddings behind a provider-independent interface."""

from app.shared.embeddings.interfaces import EmbeddingProvider, to_pgvector_literal

__all__ = ["EmbeddingProvider", "to_pgvector_literal"]
