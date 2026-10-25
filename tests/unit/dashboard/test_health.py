r"""Unit tests for the HTTP health endpoint (:8080/healthz)."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from liveguard.config.schema import (
    ComplianceConfig,
    DisclosureBannerConfig,
    LiveGuardConfig,
)
from liveguard.health import (
    HealthServer,
    calculate_tos_age_days,
    generate_health_report,
    get_last_audit_timestamp,
)
from liveguard.orchestrator.state import RuntimeState


def test_calculate_tos_age_days() -> None:
    """Assert ToS age calculation from timestamps."""
    assert calculate_tos_age_days(None) is None

    # 10 days ago
    ten_days_ago = datetime.now(timezone.utc) - timedelta(days=10)
    age = calculate_tos_age_days(ten_days_ago)
    assert age is not None
    assert 9.9 <= age <= 10.1


def test_get_last_audit_timestamp(tmp_path: Path) -> None:
    """Assert reading last audit event timestamp from file."""
    non_existent = tmp_path / "non_existent.jsonl"
    assert get_last_audit_timestamp(non_existent) is None

    audit_file = tmp_path / "events.jsonl"
    entry1 = json.dumps({"ts": "2026-10-25T01:00:00Z", "event": "start"})
    entry2 = json.dumps({"ts": "2026-10-25T02:00:00Z", "event": "finish"})
    audit_file.write_text(f"{entry1}\n{entry2}\n", encoding="utf-8")

    assert get_last_audit_timestamp(audit_file) == "2026-10-25T02:00:00Z"


def test_generate_health_report_healthy() -> None:
    """Assert generate_health_report returns 200 when all modules are healthy."""
    state = RuntimeState(
        pipeline_active=False,
        active_phase="idle",
        m1_status={"status": "ok"},
        m2_status={"status": "idle"},
        m3_status={"status": "ok"},
        m4_status={"status": "idle"},
    )
    cfg = LiveGuardConfig(
        version=1,
        compliance=ComplianceConfig(
            disclosure_banner=DisclosureBannerConfig(enabled=True),
            tos_ack_at=datetime.now(timezone.utc) - timedelta(days=5),
        ),
    )

    code, body = generate_health_report(state=state, cfg=cfg)
    assert code == 200
    assert body["status"] == "ok"
    assert body["compliance"]["banner_enabled"] is True
    assert body["compliance"]["tos_ack_age_days"] is not None
    assert body["modules"]["m1"] == "ok"


def test_generate_health_report_error_status_503() -> None:
    """Assert generate_health_report returns 503 when any module is in error."""
    state = RuntimeState(
        pipeline_active=True,
        active_phase="error",
        m1_status={"status": "error", "error": "FFmpeg failed"},
    )
    code, body = generate_health_report(state=state)
    assert code == 503
    assert body["status"] == "error"
    assert body["modules"]["m1"] == "error"


def test_health_server_http_endpoint() -> None:
    """Assert real HTTP server responds with JSON on /healthz."""
    server = HealthServer(port=8991, host="127.0.0.1")
    server.start()
    try:
        url = "http://127.0.0.1:8991/healthz"
        req = urllib.request.urlopen(url, timeout=3.0)
        assert req.status == 200
        data = json.loads(req.read().decode("utf-8"))
        assert "status" in data
        assert "compliance" in data
        assert "modules" in data
    finally:
        server.stop()
