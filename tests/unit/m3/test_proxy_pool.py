"""Unit tests for M3 — Proxy Pool Manager.

Mocks aiohttp requests; no real network required.
Coverage target: ≥ 70% of liveguard/modules/m3/.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from liveguard.modules.m3.proxy_pool import ProxyItem, ProxyPool


@pytest.fixture
def sample_proxies_file(tmp_path: Path) -> Path:
    """Write a sample proxies JSON file."""
    data = [
        {
            "id": "proxy_us",
            "url": "http://user:pass@us.proxy.example:8080",
            "type": "http",
            "label": "US-Datacenter",
            "region": "US",
        },
        {
            "id": "proxy_eu",
            "url": "http://user:pass@eu.proxy.example:8080",
            "type": "http",
            "label": "EU-Frankfurt",
            "region": "DE",
        },
    ]
    p = tmp_path / "proxies.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


class TestProxyPool:
    """ProxyPool loading, health checks, and cooldown tracking."""

    def test_load_proxies_from_file(self, sample_proxies_file: Path) -> None:
        """Proxies load properly from valid JSON."""
        pool = ProxyPool()
        pool.load_file(sample_proxies_file)

        assert len(pool.proxies) == 2
        us_proxy = pool.get_proxy("proxy_us")
        assert us_proxy is not None
        assert us_proxy.label == "US-Datacenter"
        assert us_proxy.region == "US"
        assert us_proxy.is_available is True

    def test_load_proxies_missing_or_invalid_file(self, tmp_path: Path) -> None:
        """Missing file raises FileNotFoundError; non-list raises ValueError."""
        pool = ProxyPool()
        with pytest.raises(FileNotFoundError):
            pool.load_file(tmp_path / "nonexistent.json")

        bad_file = tmp_path / "bad.json"
        bad_file.write_text('{"error": "not a list"}')
        with pytest.raises(ValueError, match="JSON array"):
            pool.load_file(bad_file)

    def test_ten_minute_cooldown_tracking(self) -> None:
        """Failed proxy becomes unavailable for 10 minutes (600s)."""
        proxy = ProxyItem(
            id="p1",
            url="http://proxy1.example:8080",
            cooldown_seconds=600.0,
        )
        assert proxy.is_available is True

        # Simulate failure at t = 1000
        now = 1000.0
        with patch("time.time", return_value=now):
            proxy.record_failure()

        assert proxy.consecutive_failures == 1
        assert proxy.last_failed_at == 1000.0

        # After 5 minutes (300s): still in cooldown
        with patch("time.time", return_value=now + 300.0):
            assert proxy.is_available is False

        # After 9 minutes 59 seconds (599s): still in cooldown
        with patch("time.time", return_value=now + 599.0):
            assert proxy.is_available is False

        # After 10 minutes (600s): cooldown expired, available again
        with patch("time.time", return_value=now + 600.0):
            assert proxy.is_available is True

        # Success resets failure counter
        proxy.record_success()
        assert proxy.consecutive_failures == 0
        assert proxy.last_failed_at is None
        assert proxy.is_available is True

    def test_get_healthy_proxy_filtering_and_fallback(self) -> None:
        """Retrieve available proxy matching region, skipping cooling-down proxies."""
        p_us = ProxyItem(id="us_1", url="http://us.proxy:8080", region="US")
        p_de = ProxyItem(id="de_1", url="http://de.proxy:8080", region="DE")
        pool = ProxyPool(proxies=[p_us, p_de])

        # Query region US
        found = pool.get_healthy_proxy(region="US")
        assert found is not None
        assert found.id == "us_1"

        # Mark us_1 failed
        pool.mark_failed("us_1")
        assert p_us.is_available is False

        # Query region US again -> None available
        assert pool.get_healthy_proxy(region="US") is None

        # Query region DE -> de_1 is available
        found_de = pool.get_healthy_proxy(region="DE")
        assert found_de is not None
        assert found_de.id == "de_1"

    @pytest.mark.asyncio
    async def test_check_proxy_health_success(self) -> None:
        """Health check marks proxy as healthy on HTTP 200."""
        proxy = ProxyItem(id="p_ok", url="http://good.proxy:8080")
        pool = ProxyPool(proxies=[proxy])

        mock_resp = AsyncMock()
        mock_resp.status = 200

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_ctx

        result = await pool.check_proxy_health(proxy, session=mock_session)
        assert result is True
        assert proxy.consecutive_failures == 0
        assert proxy.is_available is True

    @pytest.mark.asyncio
    async def test_check_proxy_health_failure_status_500(self) -> None:
        """Health check marks proxy failed on HTTP 500+."""
        proxy = ProxyItem(id="p_fail", url="http://bad.proxy:8080")
        pool = ProxyPool(proxies=[proxy])

        mock_resp = AsyncMock()
        mock_resp.status = 502

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_ctx

        result = await pool.check_proxy_health(proxy, session=mock_session)
        assert result is False
        assert proxy.consecutive_failures == 1
        assert proxy.is_available is False

    @pytest.mark.asyncio
    async def test_health_check_all_concurrent(self) -> None:
        """health_check_all checks all loaded proxies concurrently."""
        p1 = ProxyItem(id="p1", url="http://p1.example:8080")
        p2 = ProxyItem(id="p2", url="http://p2.example:8080")
        pool = ProxyPool(proxies=[p1, p2])

        async def _mock_check(proxy: ProxyItem, session: Any = None) -> bool:
            return proxy.id == "p1"

        pool.check_proxy_health = _mock_check  # type: ignore[method-assign]

        results = await pool.health_check_all()
        assert results["p1"] is True
        assert results["p2"] is False

    @pytest.mark.asyncio
    async def test_periodic_monitoring_start_stop(self) -> None:
        """Periodic monitoring task starts and terminates cleanly."""
        pool = ProxyPool(health_check_interval_s=0.1)
        pool.health_check_all = AsyncMock(return_value={})  # type: ignore[method-assign]

        await pool.start_periodic_monitoring()
        assert pool._monitor_task is not None
        await asyncio.sleep(0.15)
        await pool.stop_periodic_monitoring()
        assert pool._monitor_task is None
