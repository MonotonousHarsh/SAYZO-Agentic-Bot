"""The contract every LLM provider implements.

Application code depends on `LLMProvider`, never on a vendor SDK, so the provider can be
swapped (Groq today, a local model later) without touching the code that uses it.
"""

from typing import Protocol, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMError(Exception):
    """Base class for LLM provider failures."""


class LLMProviderUnavailable(LLMError):
    """The provider could not be reached or kept failing after bounded retries."""


class StructuredOutputError(LLMError):
    """The model's reply did not match the requested schema."""


class LLMProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[ModelT],
        temperature: float = 0.0,
    ) -> ModelT:
        """Return the model's reply parsed and validated as `response_schema`."""
        ...
