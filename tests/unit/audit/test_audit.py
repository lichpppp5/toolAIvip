"""Unit tests for the HMAC-chained audit log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveguard.audit.chain import AuditChain
from liveguard.audit.verify import verify_active_audit_log
from liveguard.secrets.vault import SecretVault


@pytest.fixture
def test_vault(tmp_path: Path) -> SecretVault:
    """Create a temporary test vault."""
    key_path = tmp_path / "age.key"
    vault_path = tmp_path / "vault.age"
    return SecretVault(vault_path=vault_path, key_path=key_path)


def test_audit_genesis_and_append_verify(
    tmp_path: Path, test_vault: SecretVault
) -> None:
    """Audit log starts with genesis sentinel and verifies cleanly."""
    log_file = tmp_path / "events.jsonl"
    chain = AuditChain(log_path=log_file, vault=test_vault)

    # First append creates genesis + entry
    entry1 = chain.append(
        event="test_event_1",
        module="m1",
        payload={"action": "start"},
        actor_hash="user123",
    )
    assert entry1["event"] == "test_event_1"

    lines = [
        json.loads(line) for line in log_file.read_text().splitlines() if line.strip()
    ]
    assert len(lines) == 2  # genesis + entry1

    genesis = lines[0]
    assert genesis["event"] == "genesis"
    assert genesis["prev_hash"] == "0" * 64

    # Entry 1 has prev_hash matching hash of genesis line
    entry2 = chain.append(
        event="test_event_2",
        module="m2",
        payload={"action": "step"},
        actor_hash="user123",
    )
    assert entry2["event"] == "test_event_2"

    # Verify chain
    is_valid, broken_idx, msg = verify_active_audit_log(
        audit_path=log_file, vault=test_vault
    )
    assert is_valid is True
    assert broken_idx == -1
    assert "Audit chain valid (3 verified entries)" in msg


def test_audit_tail(tmp_path: Path, test_vault: SecretVault) -> None:
    """AuditChain.tail returns the last N entries."""
    log_file = tmp_path / "events.jsonl"
    chain = AuditChain(log_path=log_file, vault=test_vault)

    for i in range(5):
        chain.append(
            event=f"event_{i}",
            module="test",
            payload={"i": i},
            actor_hash="actor",
        )

    # 1 genesis + 5 events = 6 entries total
    tail_3 = chain.tail(3)
    assert len(tail_3) == 3
    assert tail_3[-1]["payload"]["i"] == 4
    assert tail_3[-2]["payload"]["i"] == 3
    assert tail_3[-3]["payload"]["i"] == 2


def test_audit_tamper_payload_detected(tmp_path: Path, test_vault: SecretVault) -> None:
    """Tampering with a middle entry's payload is detected by verify."""
    log_file = tmp_path / "events.jsonl"
    chain = AuditChain(log_path=log_file, vault=test_vault)

    chain.append("event_0", "m1", {"v": 0})
    chain.append("event_1", "m2", {"v": 1})
    chain.append("event_2", "m3", {"v": 2})

    # Read all lines
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4  # genesis + 3 events

    # Tamper with index 2 (event_1)
    tampered_entry = json.loads(lines[2])
    tampered_entry["payload"]["v"] = 999
    lines[2] = json.dumps(tampered_entry, separators=(",", ":"))
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Verify: HMAC mismatch on entry 2
    is_valid, broken_idx, msg = verify_active_audit_log(
        audit_path=log_file, vault=test_vault
    )
    assert is_valid is False
    assert broken_idx == 2
    assert "HMAC signature mismatch" in msg


def test_audit_tamper_prev_hash_detected(
    tmp_path: Path, test_vault: SecretVault
) -> None:
    """Tampering with prev_hash breaks chain integrity."""
    log_file = tmp_path / "events.jsonl"
    chain = AuditChain(log_path=log_file, vault=test_vault)

    chain.append("e1", "m1", {})
    chain.append("e2", "m2", {})

    lines = log_file.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[2])
    entry["prev_hash"] = "a" * 64
    lines[2] = json.dumps(entry, separators=(",", ":"))
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    is_valid, broken_idx, msg = verify_active_audit_log(
        audit_path=log_file, vault=test_vault
    )
    assert is_valid is False
    assert broken_idx == 2


def test_audit_verify_wrong_key(tmp_path: Path, test_vault: SecretVault) -> None:
    """Verifying with a different age key fails HMAC check."""
    log_file = tmp_path / "events.jsonl"
    chain = AuditChain(log_path=log_file, vault=test_vault)
    chain.append("e1", "m1", {})

    # Create another vault with a different key
    other_vault = SecretVault(
        vault_path=tmp_path / "other.age",
        key_path=tmp_path / "other.key",
    )

    is_valid, broken_idx, msg = verify_active_audit_log(
        audit_path=log_file, vault=other_vault
    )
    assert is_valid is False
    assert broken_idx == 0  # genesis HMAC fails first
    assert "HMAC signature mismatch" in msg
