"""M3 — Geo-Test Proxy Pool Manager.

Provides health-checking, availability tracking, and cooldown management
for proxies used strictly for geographic availability testing.

CRITICAL INVARIANT:
- NO residential proxy rotation for ban evasion.
- Proxies are labeled explicitly by users for region testing.
- Failed proxies are put into a 10-minute cooldown (unavailable for 10 min).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import structlog

logger = structlog.get_logger("liveguard.m3.proxy_pool")


@dataclass
class ProxyItem:
    """Individual proxy entry in the geo-test pool.

    Attributes:
        id: Unique proxy identifier.
        url: Full proxy URL, e.g. http://proxy.example.com:8080.
        type: Proxy protocol (http, socks5).
        label: Explicit user-defined label (e.g. 'EU-Frankfurt-Test').
        region: ISO-3166-1 alpha-2 region tag, e.g. 'DE'.
        consecutive_failures: Count of consecutive health-check failures.
        last_failed_at: Timestamp of most recent failure.
        cooldown_seconds: Cooldown duration in seconds (defaults to 600s / 10m).
    """

    id: str
    url: str
    type: str = "http"
    label: str = ""
    region: str = ""
    consecutive_failures: int = 0
    last_failed_at: float | None = None
    cooldown_seconds: float = 600.0  # 10 minutes

    @property
    def is_available(self) -> bool:
        """Return True if proxy is not currently in failure cooldown."""
        if self.last_failed_at is None:
            return True
        return (time.time() - self.last_failed_at) >= self.cooldown_seconds

    def record_success(self) -> None:
        """Reset failure count upon successful health check or request."""
        self.consecutive_failures = 0
        self.last_failed_at = None

    def record_failure(self) -> None:
        """Increment failure counter and initiate cooldown period."""
        self.consecutive_failures += 1
        self.last_failed_at = time.time()


class ProxyPool:
    """Manages proxy pool loading, health-checking, and availability for geo-tests."""

    def __init__(
        self,
        proxies: Sequence[ProxyItem] | None = None,
        cooldown_seconds: float = 600.0,
        health_check_interval_s: float = 60.0,
        test_url: str = "https://www.tiktok.com",
    ) -> None:
        """Initialize ProxyPool.

        Args:
            proxies: Optional initial sequence of ProxyItems.
            cooldown_seconds: Cooldown period after failure in seconds (default 600s).
            health_check_interval_s: Health check interval in seconds (default 60s).
            test_url: Endpoint used for health verification GET request.
        """
        self._proxies: dict[str, ProxyItem] = {}
        self._cooldown_seconds = cooldown_seconds
        self._health_check_interval_s = health_check_interval_s
        self._test_url = test_url
        self._monitor_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

        if proxies:
            for p in proxies:
                p.cooldown_seconds = self._cooldown_seconds
                self._proxies[p.id] = p

    @property
    def proxies(self) -> list[ProxyItem]:
        """Return all loaded proxies."""
        return list(self._proxies.values())

    def load_file(self, path: str | Path) -> None:
        """Load proxies from a JSON file.

        Expected JSON format:
        [
          {
            "id": "proxy_us_1",
            "url": "http://user:pass@us.proxy.example:8080",
            "type": "http",
            "label": "US-Virginia-Probe",
            "region": "US"
          }
        ]
        """
        p_path = Path(path)
        if not p_path.is_file():
            raise FileNotFoundError(f"Proxy file not found: {p_path}")

        raw = json.loads(p_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(
                f"Proxy file must contain a JSON array, got {type(raw).__name__}"
            )

        for item in raw:
            proxy_id = str(item.get("id", ""))
            url = str(item.get("url", ""))
            if not proxy_id or not url:
                continue
            self._proxies[proxy_id] = ProxyItem(
                id=proxy_id,
                url=url,
                type=str(item.get("type", "http")),
                label=str(item.get("label", "")),
                region=str(item.get("region", "")).upper(),
                cooldown_seconds=self._cooldown_seconds,
            )

        logger.info("proxies_loaded", count=len(self._proxies), path=str(p_path))

    def get_proxy(self, proxy_id: str) -> ProxyItem | None:
        """Retrieve proxy by ID."""
        return self._proxies.get(proxy_id)

    def get_healthy_proxy(self, region: str | None = None) -> ProxyItem | None:
        """Retrieve the first available proxy, optionally filtered by region.

        Args:
            region: Optional ISO region code filter (e.g. 'US').

        Returns:
            An available ProxyItem or None if all are cooling down or missing.
        """
        target_region = region.upper() if region else None

        for proxy in self._proxies.values():
            if not proxy.is_available:
                continue
            if target_region and proxy.region and proxy.region != target_region:
                continue
            return proxy
        return None

    def mark_failed(self, proxy_id: str) -> None:
        """Manually mark a proxy as failed and place into cooldown."""
        proxy = self._proxies.get(proxy_id)
        if proxy:
            proxy.record_failure()
            logger.warning(
                "proxy_marked_failed",
                proxy_id=proxy_id,
                label=proxy.label,
                consecutive_failures=proxy.consecutive_failures,
                cooldown_seconds=proxy.cooldown_seconds,
            )

    def mark_success(self, proxy_id: str) -> None:
        """Mark a proxy as successful and reset failure count."""
        proxy = self._proxies.get(proxy_id)
        if proxy:
            proxy.record_success()

    async def check_proxy_health(
        self,
        proxy: ProxyItem,
        session: aiohttp.ClientSession | None = None,
    ) -> bool:
        """Perform a single health check (GET /, 10s timeout) via aiohttp.

        Args:
            proxy: Proxy to check.
            session: Optional existing aiohttp ClientSession.

        Returns:
            True if reachable and healthy, False otherwise.
        """
        timeout = aiohttp.ClientTimeout(total=10.0)
        own_session = session is None

        try:
            http_session = (
                session
                if session is not None
                else aiohttp.ClientSession(timeout=timeout)
            )
            try:
                async with http_session.get(self._test_url, proxy=proxy.url) as resp:
                    if resp.status < 500:
                        proxy.record_success()
                        logger.debug(
                            "proxy_health_ok", proxy_id=proxy.id, status=resp.status
                        )
                        return True
                    proxy.record_failure()
                    logger.warning(
                        "proxy_health_bad_status", proxy_id=proxy.id, status=resp.status
                    )
                    return False
            finally:
                if own_session:
                    await http_session.close()

        except Exception as exc:  # noqa: BLE001
            proxy.record_failure()
            logger.warning(
                "proxy_health_failed",
                proxy_id=proxy.id,
                label=proxy.label,
                error=str(exc),
                consecutive_failures=proxy.consecutive_failures,
            )
            return False

    async def health_check_all(self) -> dict[str, bool]:
        """Concurrently check health of all proxies in the pool.

        Returns:
            Mapping of proxy_id -> is_healthy bool.
        """
        timeout = aiohttp.ClientTimeout(total=10.0)
        results: dict[str, bool] = {}

        async with aiohttp.ClientSession(timeout=timeout) as session:
            tasks = [
                self.check_proxy_health(p, session=session)
                for p in self._proxies.values()
            ]
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)

            for proxy, outcome in zip(self._proxies.values(), outcomes, strict=False):
                is_healthy = bool(outcome is True)
                results[proxy.id] = is_healthy

        logger.info(
            "proxy_pool_health_check_complete",
            total=len(self._proxies),
            healthy_count=sum(1 for v in results.values() if v),
        )
        return results

    async def start_periodic_monitoring(self) -> None:
        """Start background loop health-checking proxies every 60 seconds."""
        if self._monitor_task is not None and not self._monitor_task.done():
            return

        self._stop_event.clear()

        async def _loop() -> None:
            while not self._stop_event.is_set():
                try:
                    await self.health_check_all()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("periodic_health_check_error", error=str(exc))

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._health_check_interval_s,
                    )
                except asyncio.TimeoutError:
                    pass

        self._monitor_task = asyncio.create_task(_loop())

    async def stop_periodic_monitoring(self) -> None:
        """Stop background health-check loop."""
        self._stop_event.set()
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
