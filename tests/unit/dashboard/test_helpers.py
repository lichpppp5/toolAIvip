r"""Unit tests for dashboard helper functions and secure stream key management."""

from __future__ import annotations

from pathlib import Path

from liveguard.audit.chain import AuditChain
from liveguard.config.schema import LiveGuardConfig, M1Config, M2Config, ModulesConfig
from liveguard.dashboard.helpers import (
    clear_stream_key_from_vault,
    format_uptime,
    get_module_statuses,
    has_saved_stream_key,
    save_stream_key_to_vault,
    tail_log_file,
)
from liveguard.orchestrator.state import RuntimeState
from liveguard.secrets.vault import SecretVault


def test_format_uptime() -> None:
    """Assert format_uptime properly converts seconds into HH:MM:SS."""
    assert format_uptime(None) == "00h 00m 00s"
    assert format_uptime(0) == "00h 00m 00s"
    assert format_uptime(-10) == "00h 00m 00s"
    assert format_uptime(45) == "00h 00m 45s"
    assert format_uptime(125) == "00h 02m 05s"
    assert format_uptime(3665) == "01h 01m 05s"


def test_tail_log_file(tmp_path: Path) -> None:
    """Assert tail_log_file returns the requested number of trailing lines."""
    missing = tmp_path / "missing.log"
    assert tail_log_file(missing, n=5) == []

    log_file = tmp_path / "test.log"
    lines = [f"Log line {i}" for i in range(50)]
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    tailed = tail_log_file(log_file, n=10)
    assert len(tailed) == 10
    assert tailed[0] == "Log line 40"
    assert tailed[-1] == "Log line 49"


def test_stream_key_save_and_clear_audit_payload_contains_only_boolean(
    tmp_path: Path,
) -> None:
    """CRITICAL SECURITY TEST:

    Assert that saving a stream key to the vault writes ONLY a boolean
    in the audit event payload, and NEVER exposes the secret key value
    in audit logs, payloads, or return messages.
    """
    key_path = tmp_path / "age.key"
    vault_path = tmp_path / "vault.age"
    vault = SecretVault(key_path=key_path, vault_path=vault_path)

    audit_path = tmp_path / "events.jsonl"
    audit = AuditChain(audit_file=audit_path, vault=vault)

    secret_stream_key = "live_987654321_SuperSecretTikTokRTMPStreamKey"

    # 1. Save stream key
    res = save_stream_key_to_vault(secret_stream_key, vault, audit)
    assert res["success"] is True
    assert secret_stream_key not in res["message"]

    # Verify key is in vault
    assert vault.get("tiktok_stream_key") == secret_stream_key
    assert has_saved_stream_key(vault) is True

    # Verify audit event
    tail = audit.tail(n=5)
    assert len(tail) >= 1
    last_event = tail[-1]
    assert last_event["event"] == "stream_key_set"

    # MUST contain only `{"set": True}`
    assert last_event["payload"] == {"set": True}

    # CRITICAL: Secret key string MUST NOT appear anywhere in the audit log
    audit_raw = audit_path.read_text(encoding="utf-8")
    assert secret_stream_key not in audit_raw
    assert "SuperSecret" not in audit_raw

    # 2. Clear stream key
    clear_res = clear_stream_key_from_vault(vault, audit)
    assert clear_res["success"] is True
    assert has_saved_stream_key(vault) is False
    assert vault.get("tiktok_stream_key") == ""

    # Verify clear audit event
    tail_after_clear = audit.tail(n=5)
    clear_event = tail_after_clear[-1]
    assert clear_event["event"] == "stream_key_cleared"
    assert clear_event["payload"] == {"set": False}


def test_save_empty_stream_key_rejected(tmp_path: Path) -> None:
    """Assert empty or whitespace-only stream keys are rejected."""
    vault = SecretVault(key_path=tmp_path / "k.key", vault_path=tmp_path / "v.age")
    audit = AuditChain(audit_file=tmp_path / "a.jsonl", vault=vault)

    res = save_stream_key_to_vault("   ", vault, audit)
    assert res["success"] is False
    assert "cannot be empty" in res["message"]


def test_get_module_statuses() -> None:
    """Assert get_module_statuses compiles statuses from runtime state and config."""
    state = RuntimeState(
        pipeline_active=True,
        active_phase="m1_normalizer",
        m1_status={"status": "running"},
        m2_status={"status": "idle"},
    )
    cfg = LiveGuardConfig(
        version=1,
        modules=ModulesConfig(
            m1=M1Config(enabled=True),
            m2=M2Config(enabled=False),
        ),
    )

    statuses = get_module_statuses(state, cfg)
    assert statuses["m1"]["status"] == "running"
    assert statuses["m2"]["status"] == "disabled"
