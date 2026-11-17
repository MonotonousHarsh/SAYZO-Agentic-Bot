"""Test doubles shared across tests."""

import hashlib
import math
import re
from collections.abc import Sequence

from pydantic import BaseModel

from app.modules.ingestion.graph import ExtractedGraph
from app.shared.llm.interfaces import LLMError, ModelT


class ScriptedLLM:
    """Returns pre-written replies in order; an exception in the script is raised instead."""

    model_name = "scripted-test-model"

    def __init__(self, replies: Sequence[BaseModel | Exception]) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[ModelT],
        temperature: float = 0.0,
    ) -> ModelT:
        self.prompts.append(user_prompt)
        if not self._replies:
            raise LLMError("script exhausted")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, response_schema)
        return reply


class HashingEmbeddings:
    """Deterministic stand-in for a real embedding model: a normalised bag of hashed words.

    Texts that share words get similar vectors, which is enough to test storage and ranking
    plumbing without downloading a model.
    """

    model_name = "hashing-test"

    def __init__(self, dimension: int = 768) -> None:
        self.dimension = dimension
        self.calls = 0

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for word in re.findall(r"\w+", text.lower()):
            vector[int(hashlib.sha1(word.encode()).hexdigest(), 16) % self.dimension] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class ScriptedGraphExtractor:
    """Returns a fixed graph for any text containing a trigger phrase, an empty graph otherwise."""

    prompt_version = "scripted"

    def __init__(self, graphs: dict[str, ExtractedGraph] | None = None) -> None:
        self._graphs = graphs or {}
        self.calls: list[str] = []

    def extract(
        self, text: str, *, title: str, people: Sequence[str], projects: Sequence[str]
    ) -> ExtractedGraph:
        self.calls.append(title)
        for trigger, graph in self._graphs.items():
            if trigger in text:
                return graph
        return ExtractedGraph(entities=[], relationships=[])
