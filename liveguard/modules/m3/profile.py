"""M3 — Browser profile configuration and schema.

Defines the Pydantic schema for browser profile definitions used by
the Playwright session manager.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ViewportConfig(BaseModel):
    """Browser viewport dimensions."""

    model_config = ConfigDict(populate_by_name=True)

    w: int = Field(default=1280, ge=320, le=3840, description="Width in pixels")
    h: int = Field(default=720, ge=240, le=2160, description="Height in pixels")


class ProxyConfig(BaseModel):
    """Proxy settings attached to a specific profile.

    Used strictly for geographic availability testing.
    NO residential proxy rotation or evasion.
    """

    model_config = ConfigDict(populate_by_name=True)

    url: str = Field(..., description="Proxy URL, e.g. http://proxy.example.com:8080")
    type: str = Field(default="http", description="Proxy type: http or socks5")
    label: str = Field(default="", description="User-assigned label/tag")


class BrowserProfile(BaseModel):
    """Playwright browser profile definition.

    Attributes:
        id: Unique identifier for the profile.
        userAgent: Browser User-Agent header string.
        viewport: Viewport width and height.
        timezone: IANA timezone identifier, e.g. 'America/New_York'.
        locale: Browser locale, e.g. 'en-US'.
        proxy: Optional proxy configuration for geo testing.
        createdAt: Creation timestamp.
        allow_passive_feed_scroll: Opt-in for passive feed scrolling warm-up.
        allow_interactions: Opt-in for interactive like/comment actions (default False).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., min_length=1, description="Profile unique ID")
    userAgent: str = Field(
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        alias="user_agent",
        description="Browser user agent string",
    )
    viewport: ViewportConfig = Field(default_factory=ViewportConfig)
    timezone: str = Field(default="UTC", description="IANA timezone identifier")
    locale: str = Field(default="en-US", description="Browser locale")
    proxy: ProxyConfig | None = Field(default=None, description="Optional geo proxy")
    createdAt: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        alias="created_at",
        description="Profile creation timestamp",
    )

    # Warm-up routine settings (defaults to strictly False)
    allow_passive_feed_scroll: bool = Field(
        default=False,
        description="Allow 5-10 passive feed scrolls with Gaussian delay",
    )
    allow_interactions: bool = Field(
        default=False,
        description="Allow social interactions (like/comment) - defaults to False",
    )


def load_profile(
    path_or_id: str | Path, profiles_dir: Path | None = None
) -> BrowserProfile:
    """Load a BrowserProfile from a JSON file or directory.

    Args:
        path_or_id: Direct file path or profile ID.
        profiles_dir: Optional directory containing profiles/{id}.json.

    Returns:
        Validated BrowserProfile instance.

    Raises:
        FileNotFoundError: If profile file does not exist.
        ValueError: If JSON is invalid or fails schema validation.
    """
    target = Path(path_or_id)
    if not target.is_file():
        if profiles_dir is not None:
            candidate = profiles_dir / f"{path_or_id}.json"
            if candidate.is_file():
                target = candidate
            else:
                raise FileNotFoundError(f"Profile file not found: {candidate}")
        else:
            raise FileNotFoundError(f"Profile file not found: {target}")

    content = target.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(content)
    return BrowserProfile.model_validate(data)


def save_profile(profile: BrowserProfile, target_path: Path) -> None:
    """Save a BrowserProfile to a JSON file.

    Args:
        profile: The profile instance to serialize.
        target_path: Destination JSON file path.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        profile.model_dump_json(by_alias=True, indent=2),
        encoding="utf-8",
    )
