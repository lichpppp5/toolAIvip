r"""Unit tests for Prometheus metrics collection and registry safety."""

from __future__ import annotations

import importlib

from liveguard import metrics
from liveguard.orchestrator.state import RuntimeState


def test_metrics_register_on_import_idempotently() -> None:
    """Assert that re-importing metrics module does not cause registry collision."""
    # Re-importing should not raise ValueError (Duplicate timeseries)
    reloaded = importlib.reload(metrics)
    assert reloaded.FRAMES_PROCESSED_TOTAL is not None
    assert reloaded.STREAM_BITRATE_KBPS is not None
    assert reloaded.PROXY_HEALTH is not None
    assert reloaded.AUDIT_WRITE_FAILURES_TOTAL is not None
    assert reloaded.MODULE_STATUS is not None


def test_record_frame_processed() -> None:
    """Assert frame processed counter increments correctly."""
    before = metrics.FRAMES_PROCESSED_TOTAL.labels(module="m1_test")._value.get()
    metrics.record_frame_processed("m1_test", count=5)
    after = metrics.FRAMES_PROCESSED_TOTAL.labels(module="m1_test")._value.get()
    assert after == before + 5


def test_update_stream_bitrate() -> None:
    """Assert stream bitrate gauge reflects updated values."""
    metrics.update_stream_bitrate(4500.5)
    assert metrics.STREAM_BITRATE_KBPS._value.get() == 4500.5


def test_update_proxy_health() -> None:
    """Assert proxy health gauge reflects boolean health states."""
    metrics.update_proxy_health("proxy-us-1", is_healthy=True)
    assert metrics.PROXY_HEALTH.labels(proxy_id="proxy-us-1")._value.get() == 1.0

    metrics.update_proxy_health("proxy-us-1", is_healthy=False)
    assert metrics.PROXY_HEALTH.labels(proxy_id="proxy-us-1")._value.get() == 0.0


def test_record_audit_write_failure() -> None:
    """Assert audit write failure counter increments."""
    before = metrics.AUDIT_WRITE_FAILURES_TOTAL._value.get()
    metrics.record_audit_write_failure(count=1)
    after = metrics.AUDIT_WRITE_FAILURES_TOTAL._value.get()
    assert after == before + 1


def test_update_module_status() -> None:
    """Assert module status gauge maps states to numeric codes."""
    # 0 = idle / ok, 1 = running, 2 = error
    metrics.update_module_status("m1", "idle")
    assert metrics.MODULE_STATUS.labels(module="m1")._value.get() == 0

    metrics.update_module_status("m1", "running")
    assert metrics.MODULE_STATUS.labels(module="m1")._value.get() == 1

    metrics.update_module_status("m1", "error")
    assert metrics.MODULE_STATUS.labels(module="m1")._value.get() == 2

    # Numeric input
    metrics.update_module_status("m2", 1)
    assert metrics.MODULE_STATUS.labels(module="m2")._value.get() == 1


def test_sync_metrics_from_state() -> None:
    """Assert sync_metrics_from_state updates gauges from RuntimeState snapshot."""
    state = RuntimeState(
        pipeline_active=True,
        active_phase="m4_obs",
        bitrate_kbps=6000.0,
        m1_status={"status": "ok"},
        m4_status={"status": "running"},
    )
    metrics.sync_metrics_from_state(state)
    assert metrics.STREAM_BITRATE_KBPS._value.get() == 6000.0
    assert metrics.MODULE_STATUS.labels(module="m4")._value.get() == 1
