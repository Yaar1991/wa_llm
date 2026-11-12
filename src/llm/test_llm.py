import json

import httpx
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
