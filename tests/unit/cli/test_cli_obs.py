"""Unit tests for LiveGuard CLI obs commands (M4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from liveguard.cli.main import app
from liveguard.interfaces import StreamState, StreamStatus

runner = CliRunner()


def test_cli_obs_help() -> None:
    """Verify liveguard obs --help exits with code 0."""
    result = runner.invoke(app, ["obs", "--help"])
    assert result.exit_code == 0
    assert "OBS Studio orchestration and stream management" in result.output
    assert "start" in result.output
    assert "monitor" in result.output


def test_cli_obs_start_help() -> None:
    """Verify liveguard obs start --help exits with code 0."""
    result = runner.invoke(app, ["obs", "start", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output
    assert "--stream-key" in result.output


def test_cli_obs_monitor_help() -> None:
    """Verify liveguard obs monitor --help exits with code 0."""
    result = runner.invoke(app, ["obs", "monitor", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output


def test_cli_obs_start_banner_disabled_exits_3(tmp_path: Path) -> None:
    """When disclosure banner is disabled in config, obs start exits with code 3."""
    cfg_file = tmp_path / "test_comp.yaml"
    cfg_file.write_text(
        """
version: 1
compliance:
  disclosure_banner:
    enabled: false
    text: "[TEST] AI stream"
  tos_ack_hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  tos_ack_at: "2026-10-24T00:00:00Z"
modules:
  m4:
    obs_host: "localhost"
    obs_port: 4455
    scenes: ["Scene1", "Scene2"]
"""
    )
    result = runner.invoke(
        app,
        ["obs", "start", "--config", str(cfg_file), "--stream-key", "test_key"],
    )
    assert result.exit_code == 3
    assert "Compliance gate failed" in result.output


def test_cli_obs_start_stale_tos_exits_4(tmp_path: Path) -> None:
    """When ToS ack is older than 30 days, obs start exits with code 4."""
    cfg_file = tmp_path / "test_comp.yaml"
    cfg_file.write_text(
        """
version: 1
compliance:
  disclosure_banner:
    enabled: true
    text: "[TEST] AI stream"
  tos_ack_hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  tos_ack_at: "2020-01-01T00:00:00Z"
modules:
  m4:
    obs_host: "localhost"
    obs_port: 4455
    scenes: ["Scene1", "Scene2"]
"""
    )
    result = runner.invoke(
        app,
        ["obs", "start", "--config", str(cfg_file), "--stream-key", "test_key"],
    )
    assert result.exit_code == 4
    assert "exceeds 30-day freshness limit" in result.output


def test_cli_obs_start_offline_obs_exits_5(tmp_path: Path) -> None:
    """When OBS is unreachable, obs start exits with code 5."""
    cfg_file = tmp_path / "test_comp.yaml"
    cfg_file.write_text(
        """
version: 1
compliance:
  disclosure_banner:
    enabled: true
    text: "[TEST] AI stream"
  tos_ack_hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  tos_ack_at: "2026-10-24T00:00:00Z"
modules:
  m4:
    obs_host: "127.0.0.1"
    obs_port: 59999
    scenes: ["Scene1", "Scene2"]
"""
    )
    result = runner.invoke(
        app,
        ["obs", "start", "--config", str(cfg_file), "--stream-key", "test_key"],
    )
    assert result.exit_code == 5
    assert "Failed to connect to OBS Studio WebSocket" in result.output


def test_cli_obs_start_success_mocked(tmp_path: Path) -> None:
    """When start_stream succeeds, obs start outputs success message and exits 0."""
    cfg_file = tmp_path / "test_comp.yaml"
    cfg_file.write_text(
        """
version: 1
compliance:
  disclosure_banner:
    enabled: true
    text: "[TEST] AI stream"
  tos_ack_hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  tos_ack_at: "2026-10-24T00:00:00Z"
modules:
  m4:
    obs_host: "localhost"
    obs_port: 4455
    scenes: ["Scene1", "Scene2"]
"""
    )
    fake_status = StreamStatus(
        state=StreamState.LIVE,
        scene_name="Scene1",
        uptime_seconds=10.0,
        dropped_frames=0,
        bitrate_kbps=4500.0,
        disclosure_banner_active=True,
        rtmp_endpoint="rtmp://live.example.com",
    )

    with patch(
        "liveguard.modules.m4.orchestrator.M4OBSOrchestrator.start_stream",
        new_callable=AsyncMock,
        return_value=fake_status,
    ):
        result = runner.invoke(
            app,
            ["obs", "start", "--config", str(cfg_file), "--stream-key", "test_key"],
        )
        assert result.exit_code == 0
        assert "OBS stream started successfully: state=LIVE" in result.output


def test_cli_obs_monitor_success_mocked(tmp_path: Path) -> None:
    """When connect and monitor succeed, obs monitor runs cleanly."""
    cfg_file = tmp_path / "test_comp.yaml"
    cfg_file.write_text(
        """
version: 1
compliance:
  disclosure_banner:
    enabled: true
    text: "[TEST] AI stream"
  tos_ack_hash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  tos_ack_at: "2026-10-24T00:00:00Z"
modules:
  m4:
    obs_host: "localhost"
    obs_port: 4455
    scenes: ["Scene1", "Scene2"]
"""
    )

    with (
        patch(
            "liveguard.modules.m4.orchestrator.M4OBSOrchestrator.connect",
            new_callable=AsyncMock,
        ),
        patch(
            "liveguard.modules.m4.orchestrator.M4OBSOrchestrator.monitor",
            new_callable=AsyncMock,
        ),
    ):
        result = runner.invoke(
            app,
            ["obs", "monitor", "--config", str(cfg_file)],
        )
        assert result.exit_code == 0
        assert "Monitoring OBS stream" in result.output
