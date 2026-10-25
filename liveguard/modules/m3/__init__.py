"""M3 — Geo-Test & Session Manager package."""

from __future__ import annotations

from liveguard.modules.m3.profile import (
    BrowserProfile,
    ProxyConfig,
    ViewportConfig,
    load_profile,
    save_profile,
)
from liveguard.modules.m3.proxy_pool import ProxyItem, ProxyPool
from liveguard.modules.m3.session import M3GeoSessionManager, calculate_warmup_delay

__all__ = [
    "BrowserProfile",
    "M3GeoSessionManager",
    "ProxyConfig",
    "ProxyItem",
    "ProxyPool",
    "ViewportConfig",
    "calculate_warmup_delay",
    "load_profile",
    "save_profile",
]
