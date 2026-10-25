"""Unit tests for LiveGuard CLI protect command (M2)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from liveguard.cli.main import app
from liveguard.modules.m2.tester import M2Report

runner = CliRunner()


def test_cli_protect_help():
    """Verify protect --help runs and exits with code 0."""
    result = runner.invoke(app, ["protect", "--help"])
    assert result.exit_code == 0
    assert "Evaluate adversarial robustness" in result.output
    assert "--research-only" in result.output
    assert "--i-understand-research-use" in result.output


def test_cli_protect_missing_research_only_flag_exits_2(tmp_path: Path):
    """Missing --research-only must exit with code 2."""
    dummy_input = tmp_path / "in.mp4"
    dummy_input.write_bytes(b"dummy")

    result = runner.invoke(
        app,
        [
            "protect",
            str(dummy_input),
            "--i-understand-research-use",
        ],
    )
    assert result.exit_code == 2
    assert "strictly for research purposes" in result.output


def test_cli_protect_missing_ack_research_use_flag_exits_2(tmp_path: Path):
    """Missing --i-understand-research-use must exit with code 2."""
    dummy_input = tmp_path / "in.mp4"
    dummy_input.write_bytes(b"dummy")

    result = runner.invoke(
        app,
        [
            "protect",
            str(dummy_input),
            "--research-only",
        ],
    )
    assert result.exit_code == 2
    assert "strictly for research purposes" in result.output


def test_cli_protect_success_flow(tmp_path: Path):
    """Successful run produces JSON report and exits 0."""
    dummy_input = tmp_path / "in.mp4"
    dummy_input.write_bytes(b"dummy input")
    dummy_out = tmp_path / "out.mp4"
    report_file = tmp_path / "report.json"

    fake_report = M2Report(
        input_path=dummy_input,
        watermarked_path=dummy_out,
        perturbations_applied=["pgd_clip"],
        mean_ssim=0.97,
        mean_psnr=42.5,
        eps_used=8.0 / 255.0,
        iters=40,
        device="cpu",
        duration_seconds=3.5,
        frames_processed=10,
        eps_reductions=0,
        watermark_visible=True,
        input_hash="111",
        output_hash="222",
    )

    with (
        patch("time.sleep"),
        patch("liveguard.cli.main.M2RobustnessTester") as mock_tester_cls,
    ):
        instance = MagicMock()
        instance.test = AsyncMock(return_value=fake_report)
        mock_tester_cls.return_value = instance

        result = runner.invoke(
            app,
            [
                "protect",
                str(dummy_input),
                "--output",
                str(dummy_out),
                "--research-only",
                "--i-understand-research-use",
                "--report",
                str(report_file),
            ],
        )

        assert result.exit_code == 0
        assert report_file.exists()
        saved = json.loads(report_file.read_text())
        assert saved["mean_ssim"] == 0.97
        assert saved["mean_psnr"] == 42.5
        assert saved["watermark_visible"] is True
