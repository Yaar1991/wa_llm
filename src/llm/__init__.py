"""Local LLM access through an Ollama server."""

import logging

import httpx
import pydantic_ai
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.ollama import OllamaProvider

from config import Settings

logger = logging.getLogger(__name__)

# No first-run console banner (it advertises a hosted observability service).
pydantic_ai.BANNER_ENABLED = False


def build_model(settings: Settings) -> OpenAIChatModel:
    """Create a pydantic-ai model that talks to Ollama's OpenAI-compatible API."""
    base_url = settings.ollama_base_url.rstrip("/")
    return OpenAIChatModel(
        settings.ollama_model,
        provider=OllamaProvider(base_url=f"{base_url}/v1"),
    )


def build_model_settings(settings: Settings) -> OpenAIChatModelSettings:
    model_settings = OpenAIChatModelSettings(
        max_tokens=settings.llm_max_output_tokens,
        timeout=settings.llm_timeout_seconds,
    )
    if settings.llm_disable_thinking:
        # Ollama maps reasoning_effort "none" to think=false: faster, and the
        # output-token budget isn't spent on hidden reasoning.
        model_settings["openai_reasoning_effort"] = "none"
    return model_settings


async def unload_model(settings: Settings) -> None:
    """Ask Ollama to evict the model from memory right away (best effort)."""
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
