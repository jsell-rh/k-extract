"""Pydantic Settings for runtime configuration.

Provides typed configuration with environment variable support,
validation, and defaults. This handles runtime/environment concerns
(model ID, log format) — distinct from the YAML-based ExtractionConfig
which captures per-project extraction decisions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables.

    Attributes:
        model_id: Model ID for extraction agents.
            Env var: K_EXTRACT_MODEL.
        log_format: Log output format.
            Env var: K_EXTRACT_LOG_FORMAT.
    """

    model_config = SettingsConfigDict(env_prefix="K_EXTRACT_")

    model_id: str = Field(
        default="claude-sonnet-4-6@default",
        validation_alias=AliasChoices("K_EXTRACT_MODEL", "K_EXTRACT_MODEL_ID"),
    )
    log_format: Literal["color", "json"] = "color"


# Module-level cache
_cached_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the runtime settings, cached for reuse.

    Returns:
        The Settings instance, constructed once from environment variables.
    """
    global _cached_settings
    if _cached_settings is None:
        _cached_settings = Settings()
    return _cached_settings
