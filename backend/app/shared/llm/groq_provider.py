"""Groq adapter, using Groq's OpenAI-compatible chat completions endpoint.

Replies are requested in strict JSON-schema mode and then validated again with Pydantic,
because the application must never trust model output that it has not checked itself.
Transient failures (rate limits, server errors, timeouts) are retried a bounded number of
times, honouring Groq's Retry-After header; anything else fails fast. An optional request
rate keeps bulk jobs (like graph extraction) under the free tier's per-minute limit.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from app.shared.llm.interfaces import (
    LLMProviderUnavailable,
    ModelT,
    StructuredOutputError,
)

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_DELAY_SECONDS = 30.0


class GroqProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: float = 60.0,
        max_attempts: int = 4,
        max_validation_attempts: int = 2,
        max_completion_tokens: int = 4096,
        requests_per_minute: float | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            raise ValueError("A Groq API key is required.")
        self._model = model
        self._max_attempts = max_attempts
        self._max_validation_attempts = max_validation_attempts
        self._max_completion_tokens = max_completion_tokens
        self._sleep = sleep
        self._clock = clock
        self._min_interval = 60.0 / requests_per_minute if requests_per_minute else 0.0
        self._last_request_at: float | None = None
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @property
    def model_name(self) -> str:
        return self._model

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[ModelT],
        temperature: float = 0.0,
    ) -> ModelT:
        payload = self._build_payload(system_prompt, user_prompt, response_schema, temperature)
        last_problem = "no attempt made"

        # A reply that fails validation gets one more generation, not an unbounded loop.
        for attempt in range(1, self._max_validation_attempts + 1):
            content = self._post_with_retries(payload)
            try:
                return response_schema.model_validate_json(content)
            except ValidationError as error:
                last_problem = str(error)
                logger.warning(
                    "Groq reply did not match %s (attempt %d/%d)",
                    response_schema.__name__,
                    attempt,
                    self._max_validation_attempts,
                )

        raise StructuredOutputError(
            f"{self._model} did not return a valid {response_schema.__name__}: {last_problem}"
        )

    def _build_payload(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[ModelT],
        temperature: float,
    ) -> dict[str, Any]:
        return {
            "model": self._model,
            "temperature": temperature,
            "max_completion_tokens": self._max_completion_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "strict": True,
                    "schema": response_schema.model_json_schema(),
                },
            },
        }

    def _post_with_retries(self, payload: dict[str, Any]) -> str:
        for attempt in range(1, self._max_attempts + 1):
            self._wait_for_rate_limit()
            try:
                response = self._client.post("/chat/completions", json=payload)
            except httpx.TransportError as error:
                problem = f"network error: {error}"
                retry_after = None
            else:
                if response.status_code == 200:
                    return _extract_content(response)
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    raise LLMProviderUnavailable(
                        f"Groq returned {response.status_code}: {response.text[:500]}"
                    )
                problem = f"Groq returned {response.status_code}"
                retry_after = _retry_after_seconds(response)

            if attempt < self._max_attempts:
                delay = retry_after if retry_after is not None else 2.0 ** (attempt - 1)
                logger.warning("%s; retrying in %.0fs", problem, delay)
                self._sleep(delay)

        raise LLMProviderUnavailable(f"Groq still failing after {self._max_attempts} attempts")

    def _wait_for_rate_limit(self) -> None:
        if self._min_interval and self._last_request_at is not None:
            remaining = self._min_interval - (self._clock() - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._clock()


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return min(float(value), MAX_RETRY_DELAY_SECONDS) if value is not None else None
    except ValueError:
        return None


def _extract_content(response: httpx.Response) -> str:
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise StructuredOutputError(f"unexpected Groq response shape: {error}") from error
    if not isinstance(content, str) or not content:
        raise StructuredOutputError("Groq returned an empty reply")
    return content
