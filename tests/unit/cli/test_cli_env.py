"""Unit tests for LiveGuard CLI env commands (M3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from liveguard.cli.main import app
from liveguard.interfaces import GeoResult, SessionReport

runner = CliRunner()


def test_cli_env_help() -> None:
    """Verify liveguard env --help exits with code 0."""
    result = runner.invoke(app, ["env", "--help"])
    assert result.exit_code == 0
    assert "Environment geo-testing and session management" in result.output
    assert "check" in result.output
    assert "login" in result.output


def test_cli_env_check_help() -> None:
    """Verify liveguard env check --help exits with code 0."""
    result = runner.invoke(app, ["env", "check", "--help"])
    assert result.exit_code == 0
    assert "--profile" in result.output
    assert "--config" in result.output


def test_cli_env_login_help() -> None:
    """Verify liveguard env login --help exits with code 0."""
    result = runner.invoke(app, ["env", "login", "--help"])
    assert result.exit_code == 0
    assert "--profile" in result.output
    assert "--config" in result.output


def test_cli_env_check_mocked(tmp_path: Path) -> None:
    """env check runs report when check() succeeds."""
    cfg_file = tmp_path / "test_cfg.yaml"
    cfg_file.write_text("version: 1\n")

    fake_report = SessionReport(
        region_probed="US",
        geo_result=GeoResult.AVAILABLE,
        latency_ms=95.2,
        session_valid=True,
        probe_url="https://www.tiktok.com",
    )

    with patch(
        "liveguard.modules.m3.session.M3GeoSessionManager.check",
        new_callable=AsyncMock,
        return_value=fake_report,
    ):
        result = runner.invoke(
            app,
            ["env", "check", "--profile", "creator_us", "--config", str(cfg_file)],
        )
        assert result.exit_code == 0
        assert "Geo-test report for profile 'creator_us'" in result.output
        assert "Region probed : US" in result.output
        assert "Geo result    : AVAILABLE" in result.output


def test_cli_env_login_mocked(tmp_path: Path) -> None:
    """env login triggers manual login flow and succeeds."""
    cfg_file = tmp_path / "test_cfg.yaml"
    cfg_file.write_text("version: 1\n")

    fake_report = SessionReport(
        region_probed="US",
        geo_result=GeoResult.AVAILABLE,
        latency_ms=0.0,
        session_valid=True,
        probe_url="https://www.tiktok.com/login",
    )

    with patch(
        "liveguard.modules.m3.session.M3GeoSessionManager.login",
        new_callable=AsyncMock,
        return_value=fake_report,
    ):
        result = runner.invoke(
            app,
            ["env", "login", "--profile", "creator_us", "--config", str(cfg_file)],
        )
        assert result.exit_code == 0
        assert "Login successful for profile 'creator_us'" in result.output
