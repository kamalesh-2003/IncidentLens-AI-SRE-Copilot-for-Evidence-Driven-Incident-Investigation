"""Application configuration and shared singletons.

A single source of truth for environment-driven settings (via ``pydantic-settings``),
logging setup, and the Anthropic client factory. Import :func:`get_settings` rather
than reading ``os.environ`` directly so configuration stays validated and testable.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Reasoning stages (commit analysis, impact estimation, postmortem drafting) default
# to the most capable Opus-tier model; lightweight steps use the fast model. Both are
# overridable via the environment.
DEFAULT_REASONING_MODEL = "claude-opus-4-8"
DEFAULT_FAST_MODEL = "claude-haiku-4-5"


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables and ``.env``.

    Field names map to upper-cased environment variables (e.g. ``anthropic_api_key``
    ← ``ANTHROPIC_API_KEY``). Unknown environment variables are ignored.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Integrations / secrets (all optional so the service boots without them).
    anthropic_api_key: str | None = Field(default=None, description="Claude API key.")
    slack_webhook_url: str | None = Field(default=None, description="Slack incoming webhook URL.")
    voyage_api_key: str | None = Field(default=None, description="Voyage AI embeddings key.")
    prometheus_url: str | None = Field(default=None, description="Prometheus HTTP API base URL.")

    # Behavior.
    demo_git_repo_path: str = Field(
        default="./demo/app",
        description="Repository the commit analyzer inspects.",
    )
    claude_model_reasoning: str = Field(default=DEFAULT_REASONING_MODEL)
    claude_model_fast: str = Field(default=DEFAULT_FAST_MODEL)
    log_level: str = Field(default="INFO", description="Root logging level.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` singleton."""
    return Settings()


def configure_logging(level: str | None = None) -> None:
    """Configure root logging with a consistent format.

    Uses the provided ``level`` or falls back to :attr:`Settings.log_level`.
    """
    logging.basicConfig(
        level=(level or get_settings().log_level).upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


@lru_cache(maxsize=1)
def get_anthropic_client():
    """Return a cached Anthropic client.

    Imported lazily so modules that never call the API (e.g. the git tools) don't
    require the ``anthropic`` package. Raises :class:`RuntimeError` if no key is set.
    """
    import anthropic

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not configured; set it in the environment or .env"
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)
