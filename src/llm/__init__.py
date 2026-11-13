"""LLM access: a local Ollama server (default) or OpenAI's API."""

import logging

import httpx
import pydantic_ai
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIChatModelSettings,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider

from config import Settings

logger = logging.getLogger(__name__)

# No first-run console banner (it advertises a hosted observability service).
pydantic_ai.BANNER_ENABLED = False


def build_model(settings: Settings) -> OpenAIChatModel | OpenAIResponsesModel:
    """Create the pydantic-ai model for the configured provider."""
    if settings.llm_provider == "openai":
        assert settings.openai_api_key is not None  # enforced by Settings
        # OpenAI's current models are documented for the Responses API.
        return OpenAIResponsesModel(
            settings.openai_model,
            provider=OpenAIProvider(api_key=settings.openai_api_key.get_secret_value()),
        )
    # Ollama exposes an OpenAI-compatible Chat Completions API.
    base_url = settings.ollama_base_url.rstrip("/")
    return OpenAIChatModel(
        settings.ollama_model,
        provider=OllamaProvider(base_url=f"{base_url}/v1"),
    )


def build_model_settings(settings: Settings) -> OpenAIChatModelSettings:
    settings_type = (
        OpenAIResponsesModelSettings
        if settings.llm_provider == "openai"
        else OpenAIChatModelSettings
    )
    model_settings = settings_type(
        max_tokens=settings.llm_max_output_tokens,
        timeout=settings.llm_timeout_seconds,
    )
    if settings.llm_disable_thinking:
        # Answer directly: faster, and the output-token budget isn't spent on
        # hidden reasoning. Ollama maps "none" to think=false.
        model_settings["openai_reasoning_effort"] = "none"
    return model_settings


async def unload_model(settings: Settings) -> None:
    """Ask Ollama to evict the model from memory right away (best effort)."""
    if settings.llm_provider != "ollama":
        return
    base_url = settings.ollama_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/api/generate",
                json={"model": settings.ollama_model, "keep_alive": 0},
            )
            response.raise_for_status()
        logger.info("Unloaded model %s from Ollama", settings.ollama_model)
    except httpx.HTTPError as e:
        logger.warning("Could not unload model %s: %s", settings.ollama_model, e)
