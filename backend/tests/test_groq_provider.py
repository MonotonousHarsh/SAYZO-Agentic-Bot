import json

import httpx
import pytest

from app.modules.sources.common import StrictModel
from app.shared.llm import LLMProviderUnavailable, StructuredOutputError
from app.shared.llm.groq_provider import GroqProvider


class Answer(StrictModel):
    answer: str
    confident: bool


def reply(content: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})


def provider_with(responses: list[httpx.Response], seen: list[httpx.Request]) -> GroqProvider:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    client = httpx.Client(base_url="https://groq.test", transport=httpx.MockTransport(handler))
    return GroqProvider(api_key="test", model="test-model", client=client, sleep=lambda _: None)


def ask(provider: GroqProvider) -> Answer:
    return provider.generate_structured(
        system_prompt="system", user_prompt="user", response_schema=Answer
    )


def test_returns_validated_model_and_requests_strict_schema() -> None:
    seen: list[httpx.Request] = []
    provider = provider_with([reply('{"answer": "42", "confident": true}')], seen)

    result = ask(provider)

    assert result == Answer(answer="42", confident=True)
    body = json.loads(seen[0].content)
    assert body["model"] == "test-model"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False


def test_retries_rate_limits_then_succeeds() -> None:
    seen: list[httpx.Request] = []
    provider = provider_with(
        [httpx.Response(429), httpx.Response(503), reply('{"answer": "ok", "confident": false}')],
        seen,
    )

    assert ask(provider).answer == "ok"
    assert len(seen) == 3


def test_gives_up_after_bounded_retries() -> None:
    seen: list[httpx.Request] = []
    provider = provider_with([httpx.Response(503)] * 3, seen)

    with pytest.raises(LLMProviderUnavailable):
        ask(provider)
    assert len(seen) == 3


def test_does_not_retry_client_errors() -> None:
    seen: list[httpx.Request] = []
    provider = provider_with([httpx.Response(401, text="bad key")], seen)

    with pytest.raises(LLMProviderUnavailable, match="401"):
        ask(provider)
    assert len(seen) == 1


def test_regenerates_once_when_reply_does_not_match_schema() -> None:
    seen: list[httpx.Request] = []
    provider = provider_with([reply('{"answer": 1}'), reply('{"wrong": true}')], seen)

    with pytest.raises(StructuredOutputError):
        ask(provider)
    assert len(seen) == 2
