"""Unit tests for M3 — Browser profile schema."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from liveguard.modules.m3.profile import (
    BrowserProfile,
    ProxyConfig,
    ViewportConfig,
    load_profile,
    save_profile,
)


class TestProfileSchema:
    """Verify BrowserProfile validation and defaults."""

    def test_default_profile_instantiation(self) -> None:
        """BrowserProfile creates with default safe settings."""
        prof = BrowserProfile(id="creator_default")
        assert prof.id == "creator_default"
        assert prof.viewport.w == 1280
        assert prof.viewport.h == 720
        assert prof.timezone == "UTC"
        assert prof.locale == "en-US"
        assert prof.proxy is None
        # Invariants: safety defaults MUST be False
        assert prof.allow_passive_feed_scroll is False
        assert prof.allow_interactions is False

    def test_custom_profile_fields(self) -> None:
        """Profile parses custom userAgent, proxy, and viewport."""
        prof = BrowserProfile(
            id="us_custom",
            userAgent="CustomUA/1.0",
            viewport=ViewportConfig(w=1920, h=1080),
            timezone="America/New_York",
            locale="en-US",
            proxy=ProxyConfig(
                url="http://proxy.example.com:8080",
                type="http",
                label="US-Proxy",
            ),
            allow_passive_feed_scroll=True,
            allow_interactions=False,
        )
        assert prof.userAgent == "CustomUA/1.0"
        assert prof.viewport.w == 1920
        assert prof.proxy is not None
        assert prof.proxy.url == "http://proxy.example.com:8080"
        assert prof.proxy.label == "US-Proxy"
        assert prof.allow_passive_feed_scroll is True
        assert prof.allow_interactions is False

    def test_invalid_profile_raises_validation_error(self) -> None:
        """Empty profile id or invalid dimensions must fail validation."""
        with pytest.raises(ValidationError):
            BrowserProfile(id="")

        with pytest.raises(ValidationError):
            BrowserProfile(id="valid", viewport={"w": 10, "h": 10})  # type: ignore[arg-type]

    def test_save_and_load_profile(self, tmp_path: Path) -> None:
        """Profile can be round-tripped through JSON file."""
        prof = BrowserProfile(
            id="test_persist",
            timezone="Asia/Tokyo",
            locale="ja-JP",
            allow_passive_feed_scroll=False,
        )
        file_path = tmp_path / "test_persist.json"
        save_profile(prof, file_path)

        assert file_path.is_file()
        loaded = load_profile(file_path)
        assert loaded.id == "test_persist"
        assert loaded.timezone == "Asia/Tokyo"
        assert loaded.locale == "ja-JP"
        assert loaded.allow_passive_feed_scroll is False
        assert loaded.allow_interactions is False

    def test_load_profile_missing_file_raises_not_found(self, tmp_path: Path) -> None:
        """Non-existent profile path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_profile(tmp_path / "missing.json")
