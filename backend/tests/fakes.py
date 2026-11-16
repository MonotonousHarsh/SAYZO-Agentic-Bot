"""Test doubles shared across tests."""

from collections.abc import Sequence

from pydantic import BaseModel

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
