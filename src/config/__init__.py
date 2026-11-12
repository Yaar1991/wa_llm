from functools import lru_cache
from typing import Optional, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # API settings
    port: int = 5001
    host: str = "0.0.0.0"

    # Database settings
    db_uri: str

    # WhatsApp settings
    whatsapp_host: str
    whatsapp_basic_auth_password: Optional[str] = None
    whatsapp_basic_auth_user: Optional[str] = None

    # Local LLM (Ollama) settings
    # Root URL of the Ollama server (without /v1).
    ollama_base_url: str = "http://localhost:11434"
    # Created from ollama/Modelfile (gemma4:e4b with a fixed context window).
    ollama_model: str = "wa-summarizer"
    llm_timeout_seconds: float = 600
    llm_max_output_tokens: int = 3072
    # Ask "thinking" models (e.g. qwen3) to answer directly. Set to false if
    # your model or Ollama version rejects reasoning_effort="none".
    llm_disable_thinking: bool = True
    # Unload the model from RAM as soon as a summary run finishes.
    unload_model_after_run: bool = True

    # Scheduled summary settings
    # Standard 5-field crontab expression. Empty string disables the scheduler.
    summary_cron: str = "0 21 * * *"
    summary_timezone: str = "Asia/Jerusalem"
    # Groups with fewer new messages than this are skipped until the next run.
    summary_min_messages: int = 15
    # Max transcript size sent to the model in one request; longer chats are
    # summarized in parts and then merged. Keep it well below the Ollama
    # context length (OLLAMA_CONTEXT_LENGTH).
    summary_chunk_max_chars: int = 12000

    # Optional settings
    debug: bool = False
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        arbitrary_types_allowed=True,
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        try:
            tz = ZoneInfo(self.summary_timezone)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(
                f"Invalid SUMMARY_TIMEZONE '{self.summary_timezone}'"
            ) from e

        if self.summary_cron.strip():
            try:
                CronTrigger.from_crontab(self.summary_cron, timezone=tz)
            except ValueError as e:
                raise ValueError(
                    f"Invalid SUMMARY_CRON '{self.summary_cron}': {e}"
                ) from e

        if self.summary_chunk_max_chars < 1000:
            raise ValueError("SUMMARY_CHUNK_MAX_CHARS must be at least 1000")

        return self


@lru_cache
def get_settings() -> Settings:
    # Use model_validate({}) to trigger Pydantic's validation and environment variable loading
    # without passing arguments directly, which satisfies type checkers that would otherwise
    # complain about missing required fields in __init__.
    return Settings.model_validate({})
