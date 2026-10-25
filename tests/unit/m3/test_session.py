"""Unit tests for M3 — Geo-Test & Session Manager.

Mocks Node subprocess and Playwright events; no real browser required.
Coverage target: ≥ 70% of liveguard/modules/m3/.
"""

from __future__ import annotations

import json
import random
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from liveguard.config.schema import M3Config
from liveguard.errors import ExternalDependencyError, UsageError
from liveguard.interfaces import GeoResult, SessionReport
from liveguard.modules.m3.session import (
    M3GeoSessionManager,
    calculate_warmup_delay,
)


class TestSessionPersistenceAndPermissions:
    """Session file persistence, chmod 600, and TODO marker."""

    def test_session_write_includes_marker_and_chmod_600(self, tmp_path: Path) -> None:
        """Verify session file has TODO marker, chmod 600, and unencrypted warning."""
        sessions_dir = tmp_path / "sessions"
        mgr = M3GeoSessionManager(sessions_dir=sessions_dir)

        cookies = [
            {"name": "sessionid", "value": "auth_token_123", "domain": ".tiktok.com"},
            {"name": "sid_guard", "value": "guard_xyz", "domain": ".tiktok.com"},
        ]

        with patch("liveguard.modules.m3.session.logger.warning") as mock_warn:
            session_file = mgr.write_session_file(
                profile_id="creator_1", cookies=cookies
            )

        assert session_file.is_file()

        # Check TODO marker at top of file
        content = session_file.read_text(encoding="utf-8")
        assert content.startswith("# TODO: encrypt in Phase 5\n")

        # Check file permissions are chmod 600 (-rw-------)
        mode = stat.S_IMODE(session_file.stat().st_mode)
        assert mode == 0o600

        # Check warning log emitted
        mock_warn.assert_called_once()
        call_kwargs = mock_warn.call_args[1]
        assert call_kwargs.get("profile_id") == "creator_1"

        # Check reading session file back
        read_cookies = mgr.read_session_file(profile_id="creator_1")
        assert len(read_cookies) == 2
        assert read_cookies[0]["name"] == "sessionid"
        assert read_cookies[0]["value"] == "auth_token_123"

    def test_read_missing_session_returns_empty(self, tmp_path: Path) -> None:
        """Missing session file returns empty list without error."""
        mgr = M3GeoSessionManager(sessions_dir=tmp_path / "sessions")
        assert mgr.read_session_file("nonexistent") == []


class TestWarmupDelayDistribution:
    """Gaussian warm-up delay calculation."""

    def test_warmup_delay_distribution_seeded(self) -> None:
        """Seed random for deterministic Gaussian delay assertion."""
        random.seed(42)
        delays = [calculate_warmup_delay(mean=800.0, std=300.0) for _ in range(5)]
        # All delays must be >= 100ms
        assert all(d >= 100.0 for d in delays)
        # Expected seeded values around 800ms
        assert 700.0 <= sum(delays) / len(delays) <= 900.0


class TestSubprocessBridgeAndNDJSON:
    """Subprocess spawning, NDJSON stream parsing, and error handling."""

    @pytest.mark.asyncio
    async def test_check_parses_ndjson_probe_completed(self, tmp_path: Path) -> None:
        """Subprocess outputs NDJSON events -> parses SessionReport."""
        fake_node_script = tmp_path / "browser.js"
        fake_node_script.write_text("// dummy")

        mgr = M3GeoSessionManager(
            node_script_path=fake_node_script,
            sessions_dir=tmp_path / "sessions",
            profiles_dir=tmp_path / "profiles",
        )

        ndjson_lines = [
            json.dumps({"event": "m3_bridge_started", "payload": {}}).encode("utf-8")
            + b"\n",
            json.dumps(
                {"event": "probe_started", "payload": {"url": "https://www.tiktok.com"}}
            ).encode("utf-8")
            + b"\n",
            json.dumps(
                {
                    "event": "probe_completed",
                    "payload": {
                        "region_probed": "US",
                        "geo_result": "AVAILABLE",
                        "latency_ms": 124.5,
                        "session_valid": True,
                        "probe_url": "https://www.tiktok.com",
                    },
                }
            ).encode("utf-8")
            + b"\n",
            json.dumps(
                {"event": "m3_bridge_finished", "payload": {"status": "success"}}
            ).encode("utf-8")
            + b"\n",
        ]

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.stdout.readline.side_effect = ndjson_lines + [b""]
        mock_proc.stderr = None

        with patch("shutil.which", return_value="/usr/local/bin/node"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                report = await mgr.check(profile_id="test_profile")

        assert isinstance(report, SessionReport)
        assert report.region_probed == "US"
        assert report.geo_result == GeoResult.AVAILABLE
        assert report.latency_ms == 124.5
        assert report.session_valid is True

    @pytest.mark.asyncio
    async def test_subprocess_failure_raises_external_dependency_error(
        self, tmp_path: Path
    ) -> None:
        """Non-zero exit code from Node bridge raises ExternalDependencyError."""
        fake_node_script = tmp_path / "browser.js"
        fake_node_script.write_text("// dummy")

        mgr = M3GeoSessionManager(
            node_script_path=fake_node_script,
            sessions_dir=tmp_path / "sessions",
        )

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.stdout.readline.return_value = b""
        mock_proc.stderr.read.return_value = (
            b"Playwright launch failed: Chromium crashed"
        )

        with patch("shutil.which", return_value="/usr/local/bin/node"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                with pytest.raises(ExternalDependencyError, match="Chromium crashed"):
                    await mgr.check(profile_id="err_prof")

    @pytest.mark.asyncio
    async def test_missing_node_runtime_raises_error(self, tmp_path: Path) -> None:
        """If node binary is not in PATH, raises ExternalDependencyError."""
        mgr = M3GeoSessionManager()
        with patch("shutil.which", return_value=None):
            with pytest.raises(
                ExternalDependencyError, match="Node.js runtime not found"
            ):
                await mgr.check(profile_id="no_node")

    @pytest.mark.asyncio
    async def test_probe_validates_region_code(self) -> None:
        """Invalid ISO region (e.g. 'USA' or '1') raises UsageError."""
        mgr = M3GeoSessionManager()
        with pytest.raises(UsageError, match="2-letter ISO"):
            await mgr.probe("USA")

        with pytest.raises(UsageError, match="2-letter ISO"):
            await mgr.probe("X")

    @pytest.mark.asyncio
    async def test_validate_session_checks_cookie_presence(
        self, tmp_path: Path
    ) -> None:
        """validate_session returns True if auth cookies exist."""
        sessions_dir = tmp_path / "sessions"
        mgr = M3GeoSessionManager(
            config=M3Config(region="US"),
            sessions_dir=sessions_dir,
        )

        # No session file
        assert await mgr.validate_session() is False

        # Write session file with sessionid cookie for region 'us'
        mgr.write_session_file("us", [{"name": "sessionid", "value": "val123"}])
        assert await mgr.validate_session() is True

    @pytest.mark.asyncio
    async def test_close_terminates_subprocess(self) -> None:
        """close() gracefully terminates active subprocess."""
        mgr = M3GeoSessionManager()
        mock_proc = AsyncMock()
        mock_proc.terminate = MagicMock()
        mock_proc.returncode = None
        mgr._active_subprocess = mock_proc

        await mgr.close()
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once()
        assert mgr._active_subprocess is None

    @pytest.mark.asyncio
    async def test_login_flow_saves_session(self, tmp_path: Path) -> None:
        """login() runs bridge and verifies saved session."""
        fake_node_script = tmp_path / "browser.js"
        fake_node_script.write_text("// dummy")

        mgr = M3GeoSessionManager(
            node_script_path=fake_node_script,
            sessions_dir=tmp_path / "sessions",
            profiles_dir=tmp_path / "profiles",
        )

        ndjson_lines = [
            json.dumps({"event": "m3_bridge_started", "payload": {}}).encode("utf-8")
            + b"\n",
            json.dumps({"event": "manual_login_waiting", "payload": {}}).encode("utf-8")
            + b"\n",
            json.dumps(
                {"event": "session_saved", "payload": {"cookie_count": 2}}
            ).encode("utf-8")
            + b"\n",
            json.dumps(
                {"event": "m3_bridge_finished", "payload": {"status": "success"}}
            ).encode("utf-8")
            + b"\n",
        ]

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.stdout.readline.side_effect = ndjson_lines + [b""]

        # Simulate written session file
        mgr.write_session_file("creator_login", [{"name": "sessionid", "value": "xyz"}])

        with patch("shutil.which", return_value="/usr/local/bin/node"):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                report = await mgr.login(profile_id="creator_login")

        assert report.session_valid is True
        assert report.geo_result == GeoResult.AVAILABLE

    @pytest.mark.asyncio
    async def test_probe_calls_check_with_ephemeral_profile(
        self, tmp_path: Path
    ) -> None:
        """probe('US') sets up profile and returns SessionReport."""
        fake_node_script = tmp_path / "browser.js"
        fake_node_script.write_text("// dummy")

        mgr = M3GeoSessionManager(
            node_script_path=fake_node_script,
            sessions_dir=tmp_path / "sessions",
            profiles_dir=tmp_path / "profiles",
        )

        fake_report = SessionReport(
            region_probed="US",
            geo_result=GeoResult.AVAILABLE,
            latency_ms=88.0,
            session_valid=True,
            probe_url="https://www.tiktok.com",
        )

        mgr.check = AsyncMock(return_value=fake_report)  # type: ignore[method-assign]
        report = await mgr.probe("US")

        assert report.region_probed == "US"
        assert report.geo_result == GeoResult.AVAILABLE
        mgr.check.assert_called_once()
