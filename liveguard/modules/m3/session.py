"""M3 — Geo-Test & Session Manager implementation (Playwright).

Spawns Node Playwright runtime subprocess, streams newline-delimited JSON events,
and coordinates manual-login session management and geo-availability probes.

CRITICAL INVARIANTS:
- NO stream credential scraping.
- NO residential proxy rotation for ban evasion.
- NO auto-login (manual login once).
- Session cookies written with `# TODO: encrypt in Phase 5`, chmod 600,
  and warning log 'session_unencrypted'.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import stat
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from liveguard.config.schema import M3Config
from liveguard.errors import ExternalDependencyError, UsageError
from liveguard.interfaces import GeoResult, GeoSessionManager, SessionReport
from liveguard.modules.m3.profile import BrowserProfile, save_profile
from liveguard.modules.m3.proxy_pool import ProxyPool

logger = structlog.get_logger("liveguard.m3.session")


def calculate_warmup_delay(mean: float = 800.0, std: float = 300.0) -> float:
    """Calculate a single Gaussian warm-up delay in milliseconds.

    Args:
        mean: Mean delay in milliseconds (default 800ms).
        std: Standard deviation in milliseconds (default 300ms).

    Returns:
        Sampled delay clamped to a minimum of 100ms.
    """
    delay = random.gauss(mean, std)
    return max(100.0, delay)


class M3GeoSessionManager(GeoSessionManager):
    """M3 GeoSessionManager implementing GeoSessionManager Protocol."""

    def __init__(
        self,
        config: M3Config | None = None,
        node_script_path: Path | None = None,
        sessions_dir: Path | None = None,
        profiles_dir: Path | None = None,
        proxy_pool: ProxyPool | None = None,
    ) -> None:
        """Initialize M3 GeoSessionManager.

        Args:
            config: M3 configuration instance.
            node_script_path: Path to `node/m3/src/browser.js`.
            sessions_dir: Directory where session cookie JSON files are stored.
            profiles_dir: Directory where browser profile JSON files are stored.
            proxy_pool: Optional configured ProxyPool instance.
        """
        self._cfg = config if config is not None else M3Config()

        repo_root = Path(__file__).resolve().parents[3]
        self._node_script = (
            node_script_path
            if node_script_path is not None
            else repo_root / "node" / "m3" / "src" / "browser.js"
        )
        self._sessions_dir = (
            sessions_dir
            if sessions_dir is not None
            else repo_root / "secrets" / "sessions"
        )
        self._profiles_dir = (
            profiles_dir if profiles_dir is not None else repo_root / "profiles"
        )
        self._proxy_pool = proxy_pool
        self._active_subprocess: asyncio.subprocess.Process | None = None

    # -----------------------------------------------------------------------
    # Session Persistence
    # -----------------------------------------------------------------------

    def get_session_path(self, profile_id: str) -> Path:
        """Return the target path for a profile's session JSON file."""
        return self._sessions_dir / f"{profile_id}.json"

    def write_session_file(
        self, profile_id: str, cookies: list[dict[str, Any]]
    ) -> Path:
        """Write session cookies to disk with required security markers and permissions.

        Enforces:
        - Marker: `# TODO: encrypt in Phase 5`
        - Permissions: chmod 600 (read/write only by owner)
        - Warning log: `session_unencrypted`
        """
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        session_path = self.get_session_path(profile_id)

        session_payload = {
            "profile_id": profile_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cookies": cookies,
        }

        # Write with TODO marker header
        content = (
            f"# TODO: encrypt in Phase 5\n" f"{json.dumps(session_payload, indent=2)}\n"
        )
        session_path.write_text(content, encoding="utf-8")

        # Set chmod 600
        os.chmod(session_path, stat.S_IRUSR | stat.S_IWUSR)

        # Log unencrypted warning
        logger.warning(
            "session_unencrypted",
            profile_id=profile_id,
            path=str(session_path),
            message="Session cookies stored in plaintext; vault active in Phase 5.",
        )
        return session_path

    def read_session_file(self, profile_id: str) -> list[dict[str, Any]]:
        """Read cookies from a stored session file, stripping comment lines."""
        session_path = self.get_session_path(profile_id)
        if not session_path.is_file():
            return []

        raw = session_path.read_text(encoding="utf-8")
        clean_lines = [
            line for line in raw.splitlines() if not line.strip().startswith("#")
        ]
        try:
            data = json.loads("\n".join(clean_lines))
            cookies: list[dict[str, Any]] = data.get("cookies", [])
            return cookies
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "session_read_failed", error=str(exc), path=str(session_path)
            )
            return []

    # -----------------------------------------------------------------------
    # Node subprocess bridge
    # -----------------------------------------------------------------------

    def _resolve_node_binary(self) -> str:
        """Locate node executable in environment PATH."""
        node_bin = shutil.which("node")
        if not node_bin:
            raise ExternalDependencyError(
                "Node.js runtime not found in PATH. Please install Node.js >= 20."
            )
        return node_bin

    async def _run_node_bridge(
        self,
        profile_id: str,
        action: str,
        headless: bool = True,
        profile_file: Path | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Spawn Node bridge subprocess and stream NDJSON events from stdout."""
        node_bin = self._resolve_node_binary()

        if not self._node_script.is_file():
            raise ExternalDependencyError(
                f"M3 Playwright bridge script not found at {self._node_script}"
            )

        session_path = self.get_session_path(profile_id)
        cmd = [
            node_bin,
            str(self._node_script),
            "--profile",
            profile_id,
            "--action",
            action,
            "--session-file",
            str(session_path),
            "--timeout",
            str(self._cfg.browser_timeout_ms),
        ]

        if headless:
            cmd.append("--headless")

        if profile_file and profile_file.is_file():
            cmd.extend(["--profile-file", str(profile_file)])

        logger.info("m3_subprocess_launching", action=action, profile_id=profile_id)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._active_subprocess = proc

        try:
            assert proc.stdout is not None
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8").strip()
                if not line:
                    continue

                try:
                    event_data = json.loads(line)
                    if isinstance(event_data, dict) and "event" in event_data:
                        yield event_data
                except json.JSONDecodeError:
                    # Ignore non-JSON log lines
                    continue

            await proc.wait()
            if proc.returncode != 0:
                stderr_text = ""
                if proc.stderr:
                    err_bytes = await proc.stderr.read()
                    stderr_text = err_bytes.decode("utf-8").strip()
                logger.error(
                    "m3_subprocess_failed",
                    exit_code=proc.returncode,
                    stderr=stderr_text,
                )
                raise ExternalDependencyError(
                    f"Node bridge exited with code {proc.returncode}: {stderr_text}"
                )

        finally:
            self._active_subprocess = None

    # -----------------------------------------------------------------------
    # GeoSessionManager Protocol Methods
    # -----------------------------------------------------------------------

    async def probe(self, region: str) -> SessionReport:
        """Probe TikTok availability for the given region.

        Args:
            region: ISO-3166-1 alpha-2 region code, e.g. "US".

        Returns:
            A SessionReport with geo result and latency.
        """
        region_clean = region.strip().upper()
        if len(region_clean) != 2:
            raise UsageError(
                f"Invalid region code '{region}': must be a 2-letter ISO alpha-2 code."
            )

        # Check proxy pool for region if available
        proxy_url = self._cfg.proxy_url
        if not proxy_url and self._proxy_pool:
            healthy_proxy = self._proxy_pool.get_healthy_proxy(region=region_clean)
            if healthy_proxy:
                proxy_url = healthy_proxy.url

        # Ephemeral profile for probe
        probe_profile = BrowserProfile(
            id=f"probe_{region_clean.lower()}",
            timezone="UTC",
            locale="en-US",
            proxy=(
                {"url": proxy_url, "type": "http", "label": f"{region_clean}-Probe"}  # type: ignore[arg-type]
                if proxy_url
                else None
            ),
        )

        tmp_dir = self._profiles_dir / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        probe_profile_path = tmp_dir / f"{probe_profile.id}.json"
        save_profile(probe_profile, probe_profile_path)

        try:
            return await self.check(
                profile_id=probe_profile.id,
                profile_path=probe_profile_path,
                headless=True,
            )
        finally:
            if probe_profile_path.is_file():
                probe_profile_path.unlink()

    async def validate_session(self) -> bool:
        """Check whether existing session cookies are still valid.

        Returns:
            True if session is valid, False otherwise.
        """
        cookies = self.read_session_file(self._cfg.region.lower())
        if not cookies:
            return False

        has_auth = any(
            c.get("name") in ("sessionid", "sid_guard", "sessionid_ss") for c in cookies
        )
        return has_auth

    async def close(self) -> None:
        """Terminate any running subprocess and clean up resources."""
        if self._active_subprocess and self._active_subprocess.returncode is None:
            try:
                self._active_subprocess.terminate()
                await self._active_subprocess.wait()
            except Exception:  # noqa: BLE001
                pass
            self._active_subprocess = None

    # -----------------------------------------------------------------------
    # High-level actions: check and login
    # -----------------------------------------------------------------------

    async def check(
        self,
        profile_id: str,
        profile_path: Path | None = None,
        headless: bool = True,
    ) -> SessionReport:
        """Run geographic availability check for a profile."""
        report: SessionReport | None = None

        async for event in self._run_node_bridge(
            profile_id=profile_id,
            action="check",
            headless=headless,
            profile_file=profile_path,
        ):
            event_name = event.get("event")
            payload = event.get("payload", {})

            if event_name == "probe_completed":
                raw_res = payload.get("geo_result", "PROBE_FAILED")
                geo_res = getattr(GeoResult, raw_res, GeoResult.PROBE_FAILED)
                report = SessionReport(
                    region_probed=str(payload.get("region_probed", "US")),
                    geo_result=geo_res,
                    latency_ms=float(payload.get("latency_ms", 0.0)),
                    session_valid=bool(payload.get("session_valid", False)),
                    probe_url=str(payload.get("probe_url", "https://www.tiktok.com")),
                    completed_at=datetime.now(timezone.utc),
                )
            elif event_name == "warmup_action":
                logger.info(
                    "m3_warmup_action",
                    step=payload.get("step"),
                    action=payload.get("action"),
                    delay_ms=payload.get("delay_ms"),
                )

        if report is None:
            raise ExternalDependencyError(
                "Bridge failed to return probe_completed event."
            )

        logger.info(
            "m3_check_complete",
            region=report.region_probed,
            geo_result=report.geo_result.name,
            latency_ms=report.latency_ms,
            session_valid=report.session_valid,
        )
        return report

    async def login(
        self,
        profile_id: str,
        profile_path: Path | None = None,
        headless: bool = False,
    ) -> SessionReport:
        """Open visible browser for manual user login and persist session."""
        session_saved = False
        cookie_count = 0

        async for event in self._run_node_bridge(
            profile_id=profile_id,
            action="login",
            headless=headless,
            profile_file=profile_path,
        ):
            event_name = event.get("event")
            payload = event.get("payload", {})

            if event_name == "manual_login_waiting":
                logger.info(
                    "manual_login_waiting",
                    message="Manual login required in browser. Waiting for user...",
                )
            elif event_name == "session_saved":
                session_saved = True
                cookie_count = int(payload.get("cookie_count", 0))
                logger.info("session_saved", cookie_count=cookie_count)

        # Check persisted session file
        session_file = self.get_session_path(profile_id)
        if session_file.is_file():
            # Ensure chmod 600 and warning log
            os.chmod(session_file, stat.S_IRUSR | stat.S_IWUSR)
            logger.warning(
                "session_unencrypted",
                profile_id=profile_id,
                path=str(session_file),
            )

        return SessionReport(
            region_probed="US",
            geo_result=GeoResult.AVAILABLE if session_saved else GeoResult.PROBE_FAILED,
            latency_ms=0.0,
            session_valid=session_saved,
            probe_url="https://www.tiktok.com/login",
            completed_at=datetime.now(timezone.utc),
        )
