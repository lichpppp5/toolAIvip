"""Unit tests for LiveGuard CLI harden command.

Uses Typer's CliRunner to test CLI invocations, option handling,
exit codes, and report generation.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from liveguard.cli.main import app
from liveguard.modules.m1.normalizer import M1Report

runner = CliRunner()


def test_cli_harden_help():
    """Verify harden --help runs and exits with code 0."""
    result = runner.invoke(app, ["harden", "--help"])
    assert result.exit_code == 0
    assert "Normalise privacy metadata" in result.output
    assert "--strip-provenance" in result.output
    assert "--i-understand-legal-risk" in result.output


def test_cli_harden_compliance_error_when_strip_provenance_without_ack(tmp_path: Path):
    """Verify exiting with code 3 (compliance violation) if legal risk is not acked."""
    dummy_input = tmp_path / "in.mp4"
    dummy_input.write_bytes(b"dummy")
    dummy_out = tmp_path / "out.mp4"

    result = runner.invoke(
        app,
        [
            "harden",
            str(dummy_input),
            "--output",
            str(dummy_out),
            "--strip-provenance",
        ],
    )
    assert result.exit_code == 3
    assert "Compliance error" in result.output or "strip-provenance" in result.output


def test_cli_harden_compliance_error_when_ack_without_strip_provenance(tmp_path: Path):
    """Verify exiting with code 3 if ack is provided without --strip-provenance."""
    dummy_input = tmp_path / "in.mp4"
    dummy_input.write_bytes(b"dummy")
    dummy_out = tmp_path / "out.mp4"

    result = runner.invoke(
        app,
        [
            "harden",
            str(dummy_input),
            "--output",
            str(dummy_out),
            "--i-understand-legal-risk",
        ],
    )
    assert result.exit_code == 3
    assert "Both flags must be provided together" in result.output


def test_cli_harden_success_flow(tmp_path: Path):
    """Verify successful run produces report and calls normalizer."""
    dummy_input = tmp_path / "in.mp4"
    dummy_input.write_bytes(b"dummy input")
    dummy_out = tmp_path / "out.mp4"
    report_file = tmp_path / "report.json"

    fake_report = M1Report(
        input_path=dummy_input,
        output_path=dummy_out,
        duration_seconds=1.2,
        input_hash="aaa",
        output_hash="bbb",
        tags_removed=["EXIF:All", "GPS:All"],
        c2pa_preserved=True,
        noise_strength=2,
    )

    with patch("liveguard.cli.main.M1Normalizer") as mock_cls:
        instance = MagicMock()
        instance.process = AsyncMock(return_value=fake_report)
        mock_cls.return_value = instance

        result = runner.invoke(
            app,
            [
                "harden",
                str(dummy_input),
                "--output",
                str(dummy_out),
                "--report",
                str(report_file),
                "--noise",
                "2",
            ],
        )

        assert result.exit_code == 0
        assert report_file.exists()
        saved_data = json.loads(report_file.read_text())
        assert saved_data["input_hash"] == "aaa"
        assert saved_data["output_hash"] == "bbb"
        assert saved_data["c2pa_preserved"] is True
