"""Local embeddings with a Hugging Face model through sentence-transformers.

The model is downloaded once (about 440 MB for bge-base) and then runs on the CPU, so
embedding costs nothing per call and company text never leaves the machine.
"""

from collections.abc import Sequence

# BGE models are trained to see this instruction in front of search queries (not passages).
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class SentenceTransformerEmbeddings:
    def __init__(
        self,
        model_name: str,
        *,
        expected_dimension: int,
        device: str = "cpu",
        batch_size: int = 16,
        query_instruction: str | None = None,
    ) -> None:
        # Imported here so the rest of the app (and its tests) never pay for loading torch.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name, device=device)
        self._model_name = model_name
        self._batch_size = batch_size
        if query_instruction is None and "bge" in model_name.lower():
            query_instruction = BGE_QUERY_INSTRUCTION
        self._query_instruction = query_instruction or ""

        dimension = self._model.get_sentence_embedding_dimension()
        if dimension != expected_dimension:
            raise ValueError(
                f"{model_name} produces {dimension}-dimensional vectors, but the database "
                f"expects {expected_dimension}. Use a matching model or add a migration."
            )
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            batch_size=self._batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode(
            self._query_instruction + text,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vector.tolist()
