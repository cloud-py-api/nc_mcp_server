"""Tests for configuration loading."""

import pytest

from nextcloud_mcp.config import Config
from nextcloud_mcp.permissions import PermissionLevel


class TestConfigFromEnv:
    def test_loads_required_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://cloud.example.com")
        monkeypatch.setenv("NEXTCLOUD_USER", "alice")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "secret")

        config = Config.from_env()
        assert config.nextcloud_url == "http://cloud.example.com"
        assert config.user == "alice"
        assert config.password == "secret"

    def test_strips_trailing_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://cloud.example.com/")
        monkeypatch.setenv("NEXTCLOUD_USER", "alice")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "secret")

        config = Config.from_env()
        assert config.nextcloud_url == "http://cloud.example.com"

    def test_default_permission_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://localhost")
        monkeypatch.setenv("NEXTCLOUD_USER", "admin")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "admin")

        config = Config.from_env()
        assert config.permission_level == PermissionLevel.READ

    def test_permission_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://localhost")
        monkeypatch.setenv("NEXTCLOUD_USER", "admin")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "admin")
        monkeypatch.setenv("NEXTCLOUD_MCP_PERMISSIONS", "write")

        config = Config.from_env()
        assert config.permission_level == PermissionLevel.WRITE

    def test_permission_destructive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://localhost")
        monkeypatch.setenv("NEXTCLOUD_USER", "admin")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "admin")
        monkeypatch.setenv("NEXTCLOUD_MCP_PERMISSIONS", "destructive")

        config = Config.from_env()
        assert config.permission_level == PermissionLevel.DESTRUCTIVE

    def test_invalid_permission_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://localhost")
        monkeypatch.setenv("NEXTCLOUD_USER", "admin")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "admin")
        monkeypatch.setenv("NEXTCLOUD_MCP_PERMISSIONS", "admin")

        with pytest.raises(ValueError, match="Invalid NEXTCLOUD_MCP_PERMISSIONS"):
            Config.from_env()

    def test_default_retry_max(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://localhost")
        monkeypatch.setenv("NEXTCLOUD_USER", "admin")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "admin")

        config = Config.from_env()
        assert config.retry_max == 3

    def test_retry_max_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://localhost")
        monkeypatch.setenv("NEXTCLOUD_USER", "admin")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "admin")
        monkeypatch.setenv("NEXTCLOUD_MCP_RETRY_MAX", "5")

        config = Config.from_env()
        assert config.retry_max == 5

    def test_retry_max_zero_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://localhost")
        monkeypatch.setenv("NEXTCLOUD_USER", "admin")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "admin")
        monkeypatch.setenv("NEXTCLOUD_MCP_RETRY_MAX", "0")

        config = Config.from_env()
        assert config.retry_max == 0

    def test_retry_max_negative_clamped_to_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://localhost")
        monkeypatch.setenv("NEXTCLOUD_USER", "admin")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "admin")
        monkeypatch.setenv("NEXTCLOUD_MCP_RETRY_MAX", "-1")

        config = Config.from_env()
        assert config.retry_max == 0

    def test_retry_max_invalid_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://localhost")
        monkeypatch.setenv("NEXTCLOUD_USER", "admin")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "admin")
        monkeypatch.setenv("NEXTCLOUD_MCP_RETRY_MAX", "abc")

        with pytest.raises(ValueError, match="Invalid NEXTCLOUD_MCP_RETRY_MAX"):
            Config.from_env()

    def test_case_insensitive_permission(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://localhost")
        monkeypatch.setenv("NEXTCLOUD_USER", "admin")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "admin")
        monkeypatch.setenv("NEXTCLOUD_MCP_PERMISSIONS", "WRITE")

        config = Config.from_env()
        assert config.permission_level == PermissionLevel.WRITE


class TestConfigValidation:
    def test_missing_url_raises(self) -> None:
        config = Config(nextcloud_url="", user="admin", password="admin")
        with pytest.raises(ValueError, match="NEXTCLOUD_URL"):
            config.validate()

    def test_missing_user_raises(self) -> None:
        config = Config(nextcloud_url="http://localhost", user="", password="admin")
        with pytest.raises(ValueError, match="NEXTCLOUD_USER"):
            config.validate()

    def test_missing_password_raises(self) -> None:
        config = Config(nextcloud_url="http://localhost", user="admin", password="")
        with pytest.raises(ValueError, match="NEXTCLOUD_PASSWORD"):
            config.validate()

    def test_missing_multiple_shows_all(self) -> None:
        config = Config(nextcloud_url="", user="", password="")
        with pytest.raises(ValueError, match=r"NEXTCLOUD_URL.*NEXTCLOUD_USER.*NEXTCLOUD_PASSWORD"):
            config.validate()

    def test_valid_config_passes(self) -> None:
        config = Config(nextcloud_url="http://localhost", user="admin", password="admin")
        config.validate()  # Should not raise
