"""Tests for k_extract.config.settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import k_extract.config.settings as settings_module
from k_extract.config.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Reset the module-level settings cache before each test."""
    settings_module._cached_settings = None


class TestSettingsDefaults:
    """Test that Settings has the correct default values."""

    def test_default_model_id(self) -> None:
        s = Settings()
        assert s.model_id == "claude-sonnet-4-6@default"

    def test_default_log_format(self) -> None:
        s = Settings()
        assert s.log_format == "color"


class TestSettingsEnvVarOverrides:
    """Test environment variable overrides."""

    def test_model_id_from_k_extract_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("K_EXTRACT_MODEL", "claude-opus-4-6@vertex")
        s = Settings()
        assert s.model_id == "claude-opus-4-6@vertex"

    def test_log_format_from_k_extract_log_format(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("K_EXTRACT_LOG_FORMAT", "json")
        s = Settings()
        assert s.log_format == "json"

    def test_log_format_invalid_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("K_EXTRACT_LOG_FORMAT", "xml")
        with pytest.raises(ValidationError):
            Settings()


class TestGetSettings:
    """Test the cached get_settings function."""

    def test_returns_settings_instance(self) -> None:
        s = get_settings()
        assert isinstance(s, Settings)

    def test_caches_result(self) -> None:
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_uses_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("K_EXTRACT_MODEL", "test-model-42")
        s = get_settings()
        assert s.model_id == "test-model-42"
