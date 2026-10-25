"""Unit tests for the pipeline orchestrator and state management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from liveguard.audit.chain import AuditChain
from liveguard.config.schema import (
    ComplianceConfig,
    DisclosureBannerConfig,
    LiveGuardConfig,
    M1Config,
    M2Config,
    M3Config,
    M4Config,
    ModulesConfig,
    PipelineConfig,
)
from liveguard.errors import ExternalDependencyError, StaleAckError, UsageError
from liveguard.exit_codes import ExitCode
from liveguard.orchestrator.runner import run_pipeline
from liveguard.orchestrator.state import RuntimeState
from liveguard.secrets.vault import SecretVault


@pytest.fixture
def test_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Prepare mock EULA marker and test vault/audit chain."""
    marker = tmp_path / "eula_accepted"
    marker.write_text("eula_accepted_at=100\n", encoding="utf-8")
    monkeypatch.setattr("liveguard.eula.DEFAULT_MARKER_PATH", marker)

    vault = SecretVault(
        vault_path=tmp_path / "vault.age", key_path=tmp_path / "age.key"
    )
    chain = AuditChain(log_path=tmp_path / "events.jsonl", vault=vault)
    monkeypatch.setattr("liveguard.orchestrator.runner.get_audit_chain", lambda: chain)
    monkeypatch.setattr("liveguard.audit.chain.get_audit_chain", lambda: chain)
    return tmp_path


def test_runtime_state_snapshot(tmp_path: Path) -> None:
    """RuntimeState writes atomic snapshot to JSON file."""
    state_file = tmp_path / "runtime" / "state.json"
    state = RuntimeState()
    state.update_phase("testing_phase", stream_active=True, bitrate_kbps=5000)
    state.save_snapshot(state_file)

    assert state_file.is_file()
    loaded = RuntimeState.load_snapshot(state_file)
    assert loaded.active_phase == "testing_phase"
    assert loaded.stream_active is True
    assert loaded.bitrate_kbps == 5000


def test_orchestrator_all_modules_disabled_noop_success(test_setup: Path) -> None:
    """When all modules are disabled, pipeline completes with no-op success."""
    cfg = LiveGuardConfig(
        version=1,
        modules=ModulesConfig(
            m1=M1Config(enabled=False),
            m2=M2Config(enabled=False),
            m3=M3Config(enabled=False),
            m4=M4Config(enabled=False),
        ),
    )

    final_state = run_pipeline(cfg)
    assert final_state.active_phase == "finished"
    assert final_state.pipeline_active is False


def test_orchestrator_m1_missing_ffmpeg_surfaced_exit_5(
    test_setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing ffmpeg during M1 raises ExternalDependencyError (exit 5)."""
    # Create dummy input file
    input_video = test_setup / "dummy.mp4"
    input_video.write_bytes(b"dummy video content")

    cfg = LiveGuardConfig(
        version=1,
        pipeline=PipelineConfig(input_path=str(input_video)),
        modules=ModulesConfig(
            m1=M1Config(enabled=True),
            m2=M2Config(enabled=False),
            m3=M3Config(enabled=False),
            m4=M4Config(enabled=False),
        ),
    )

    def mock_init(*args: Any, **kwargs: Any) -> None:
        raise ExternalDependencyError("ffmpeg not found")

    monkeypatch.setattr("liveguard.orchestrator.runner.M1Normalizer", mock_init)

    with pytest.raises(ExternalDependencyError) as exc_info:
        run_pipeline(cfg)

    assert exc_info.value.exit_code == ExitCode.EXTERNAL_DEP


def test_orchestrator_m2_without_ack_raises_usage_error(test_setup: Path) -> None:
    """Running M2 without research acknowledgement raises UsageError (exit 2)."""
    cfg = LiveGuardConfig(
        version=1,
        modules=ModulesConfig(
            m1=M1Config(enabled=False),
            m2=M2Config(enabled=True),
            m3=M3Config(enabled=False),
            m4=M4Config(enabled=False),
        ),
    )

    with pytest.raises(UsageError) as exc_info:
        run_pipeline(cfg, research_only=False, ack_research_use=False)

    assert exc_info.value.exit_code == ExitCode.USAGE


def test_orchestrator_m4_stale_tos_ack_raises_exit_4(test_setup: Path) -> None:
    """ToS ack older than 30 days raises StaleAckError (exit 4)."""
    stale_date = datetime.now(timezone.utc) - timedelta(days=35)
    cfg = LiveGuardConfig(
        version=1,
        compliance=ComplianceConfig(
            disclosure_banner=DisclosureBannerConfig(enabled=True),
            tos_ack_at=stale_date,
        ),
        modules=ModulesConfig(
            m1=M1Config(enabled=False),
            m2=M2Config(enabled=False),
            m3=M3Config(enabled=False),
            m4=M4Config(enabled=True),
        ),
    )

    with pytest.raises(StaleAckError) as exc_info:
        run_pipeline(cfg)

    assert exc_info.value.exit_code == ExitCode.STALE_ACK


def test_orchestrator_m4_missing_tos_ack_raises_exit_4(test_setup: Path) -> None:
    """Missing ToS ack (None) raises StaleAckError (exit 4)."""
    cfg = LiveGuardConfig(
        version=1,
        compliance=ComplianceConfig(
            disclosure_banner=DisclosureBannerConfig(enabled=True),
            tos_ack_at=None,
        ),
        modules=ModulesConfig(
            m1=M1Config(enabled=False),
            m2=M2Config(enabled=False),
            m3=M3Config(enabled=False),
            m4=M4Config(enabled=True),
        ),
    )

    with pytest.raises(StaleAckError) as exc_info:
        run_pipeline(cfg)

    assert exc_info.value.exit_code == ExitCode.STALE_ACK


def test_orchestrator_m1_input_not_found(test_setup: Path) -> None:
    """M1 enabled with non-existent input video raises UsageError."""
    cfg = LiveGuardConfig(
        version=1,
        pipeline=PipelineConfig(input_path="nonexistent_video.mp4"),
        modules=ModulesConfig(
            m1=M1Config(enabled=True),
            m2=M2Config(enabled=False),
            m3=M3Config(enabled=False),
            m4=M4Config(enabled=False),
        ),
    )

    with pytest.raises(UsageError) as exc_info:
        run_pipeline(cfg)

    assert exc_info.value.exit_code == ExitCode.USAGE


def test_orchestrator_full_pipeline_flow(
    test_setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test full sequential pipeline execution with M1, M2, M3, M4."""
    input_video = test_setup / "in.mp4"
    input_video.write_bytes(b"data")

    cfg = LiveGuardConfig(
        version=1,
        pipeline=PipelineConfig(
            input_path=str(input_video),
            output_dir=str(test_setup / "out"),
        ),
        compliance=ComplianceConfig(
            disclosure_banner=DisclosureBannerConfig(enabled=True),
            tos_ack_at=datetime.now(timezone.utc),
        ),
        modules=ModulesConfig(
            m1=M1Config(enabled=True),
            m2=M2Config(enabled=True),
            m3=M3Config(enabled=True),
            m4=M4Config(enabled=True),
        ),
    )

    from unittest.mock import AsyncMock

    # Mock dependencies
    monkeypatch.setattr(
        "liveguard.modules.m1.normalizer.M1Normalizer._require_tool",
        lambda *args, **kwargs: "/mock/tool",
    )
    monkeypatch.setattr(
        "liveguard.modules.m2.tester.M2RobustnessTester.validate_dependencies",
        AsyncMock(),
    )

    monkeypatch.setattr(
        "liveguard.modules.m2.tester.M2RobustnessTester._ensure_model_loaded",
        lambda self: None,
    )

    # Mock M1
    mock_m1_report = MagicMock()
    mock_m1_report.to_dict.return_value = {"duration": 1.2}
    monkeypatch.setattr(
        "liveguard.orchestrator.runner.M1Normalizer.process",
        AsyncMock(return_value=mock_m1_report),
    )

    # Mock M2
    mock_m2_report = MagicMock()
    mock_m2_report.to_dict.return_value = {"ssim": 0.98}
    monkeypatch.setattr(
        "liveguard.orchestrator.runner.M2RobustnessTester.test",
        AsyncMock(return_value=mock_m2_report),
    )

    # Mock M3
    mock_m3_report = MagicMock()
    mock_m3_report.region_probed = "US"
    mock_geo = MagicMock()
    mock_geo.name = "OK"
    mock_m3_report.geo_result = mock_geo
    mock_m3_report.session_valid = True
    mock_m3_report.latency_ms = 45.0

    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "liveguard.orchestrator.runner.M3GeoSessionManager.check",
        AsyncMock(return_value=mock_m3_report),
    )

    # Mock M4
    status_mock = MagicMock()
    status_mock.bitrate_kbps = 4500.0
    status_mock.dropped_frames = 0

    monkeypatch.setattr(
        "liveguard.orchestrator.runner.M4OBSOrchestrator.start_stream",
        AsyncMock(return_value=status_mock),
    )
    monkeypatch.setattr(
        "liveguard.orchestrator.runner.M4OBSOrchestrator.get_status",
        AsyncMock(return_value=status_mock),
    )
    monkeypatch.setattr(
        "liveguard.orchestrator.runner.M4OBSOrchestrator.stop_stream",
        AsyncMock(return_value=status_mock),
    )

    final_state = run_pipeline(
        cfg,
        research_only=True,
        ack_research_use=True,
    )
    assert final_state.active_phase == "finished"
    assert final_state.bitrate_kbps == 4500
    assert final_state.m1_status == {"duration": 1.2}
    assert final_state.m2_status == {"ssim": 0.98}
