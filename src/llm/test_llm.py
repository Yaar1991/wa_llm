import json
from typing import Any

import httpx
import httpx2
from openai import AsyncOpenAI
import pytest
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pytest_httpx import HTTPXMock

from config import Settings
from llm import build_model, build_model_settings, unload_model


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        db_uri="postgresql+asyncpg://u:p@localhost/db",
        whatsapp_host="http://localhost:3000",
        ollama_base_url="http://ollama.test:11434/",
        ollama_model="qwen3:8b",
    )


def test_build_model_points_at_ollama_openai_endpoint():
    model = build_model(_settings())

    assert model.model_name == "qwen3:8b"
    assert model.system == "ollama"
    assert str(model.client.base_url) == "http://ollama.test:11434/v1/"


def test_model_settings_disable_thinking_by_default():
    settings = build_model_settings(_settings())
    assert settings.get("openai_reasoning_effort") == "none"
    assert settings.get("max_tokens") == 3072


def test_model_settings_can_keep_thinking():
    settings = build_model_settings(
        _settings().model_copy(update={"llm_disable_thinking": False})
    )
    assert "openai_reasoning_effort" not in settings


async def test_unload_model_sets_keep_alive_zero(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST", url="http://ollama.test:11434/api/generate", json={}
    )

    await unload_model(_settings())

    request = httpx_mock.get_request()
    assert request is not None
    assert json.loads(request.content) == {"model": "qwen3:8b", "keep_alive": 0}


async def test_unload_model_ignores_errors(httpx_mock: HTTPXMock):
    httpx_mock.add_exception(httpx.ConnectError("ollama not running"))

    await unload_model(_settings())  # must not raise


def _openai_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "db_uri": "postgresql+asyncpg://u:p@localhost/db",
        "whatsapp_host": "http://localhost:3000",
        "llm_provider": "openai",
        "openai_api_key": "sk-test",
    }
    return Settings(_env_file=None, **(values | overrides))  # type: ignore[call-arg]


def test_openai_provider_requires_api_key():
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        _openai_settings(openai_api_key="  ")


def test_openai_is_opt_in():
    assert _settings().llm_provider == "ollama"


def test_build_model_uses_openai_responses_api():
    model = build_model(_openai_settings())

    assert isinstance(model, OpenAIResponsesModel)
    assert model.model_name == "gpt-6-luna"
    assert model.system == "openai"
    assert str(model.client.base_url) == "https://api.openai.com/v1/"


async def test_openai_request_turns_reasoning_off():
    # The OpenAI SDK uses httpx2, which pytest_httpx doesn't intercept, so the
    # client gets a mock transport and the test can never reach the network.
    requests: list[httpx2.Request] = []
    response_body = {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": "gpt-6-luna",
        "status": "completed",
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "סיכום", "annotations": []}
                ],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=response_body)

    settings = _openai_settings()
    client = AsyncOpenAI(
        api_key="sk-test",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    model = OpenAIResponsesModel(
        settings.openai_model, provider=OpenAIProvider(openai_client=client)
    )
    agent = Agent(model, model_settings=build_model_settings(settings))

    result = await agent.run("hi")

    assert result.output == "סיכום"
    [request] = requests
    assert str(request.url) == "https://api.openai.com/v1/responses"
    body = json.loads(request.content)
    assert body["model"] == "gpt-6-luna"
    assert body["reasoning"]["effort"] == "none"
    assert body["max_output_tokens"] == 3072


async def test_unload_model_is_a_no_op_for_openai(httpx_mock: HTTPXMock):
    await unload_model(_openai_settings())

    assert httpx_mock.get_requests() == []
