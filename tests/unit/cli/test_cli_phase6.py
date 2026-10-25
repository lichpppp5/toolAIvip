r"""Unit tests for Phase 6 CLI commands (dashboard, serve-metrics)."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from liveguard.cli.main import app

runner = CliRunner()


def test_cli_dashboard_help() -> None:
    """Assert `liveguard dashboard --help` displays expected options."""
    res = runner.invoke(app, ["dashboard", "--help"])
    assert res.exit_code == 0
    assert "--host" in res.output
    assert "--port" in res.output
    assert "--config" in res.output


def test_cli_dashboard_invokes_streamlit() -> None:
    """Assert `liveguard dashboard` launches streamlit subprocess."""
    with patch("subprocess.run") as mock_run:
        res = runner.invoke(app, ["dashboard", "--port", "8502", "--host", "127.0.0.1"])
        assert res.exit_code == 0
        assert mock_run.called
        cmd_args = mock_run.call_args[0][0]
        assert "streamlit" in cmd_args
        assert "8502" in cmd_args
        assert "127.0.0.1" in cmd_args


def test_cli_serve_metrics_help() -> None:
    """Assert `liveguard serve-metrics --help` displays expected options."""
    res = runner.invoke(app, ["serve-metrics", "--help"])
    assert res.exit_code == 0
    assert "--metrics-port" in res.output
    assert "--health-port" in res.output
