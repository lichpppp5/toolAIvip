"""Unit tests for EULA enforcement and acknowledgements (liveguard/eula.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from liveguard.audit.chain import AuditChain
from liveguard.errors import ComplianceError, UsageError
from liveguard.eula import (
    compute_machine_hash,
    enforce_eula,
    is_eula_accepted,
    require_ack,
)
from liveguard.exit_codes import ExitCode
from liveguard.secrets.vault import SecretVault


def test_compute_machine_hash() -> None:
    """Machine hash returns a 64-char hex string."""
    m_hash = compute_machine_hash()
    assert isinstance(m_hash, str)
    assert len(m_hash) == 64


def test_eula_accept_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Accepting EULA with literal 'I AGREE' writes marker file and records audit."""
    marker = tmp_path / "eula_accepted"
    log_file = tmp_path / "events.jsonl"
    vault = SecretVault(
        vault_path=tmp_path / "vault.age", key_path=tmp_path / "age.key"
    )
    chain = AuditChain(log_path=log_file, vault=vault)

    # Mock default audit chain
    monkeypatch.setattr("liveguard.eula.get_audit_chain", lambda: chain)

    assert not is_eula_accepted(marker)

    # Prompt returns "I AGREE"
    enforce_eula(marker_path=marker, prompt_fn=lambda _: "I AGREE")

    assert is_eula_accepted(marker)
    marker_content = marker.read_text(encoding="utf-8")
    assert "machine_hash=" in marker_content

    # Check audit event
    lines = [line for line in log_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 2  # genesis + eula_accepted
    assert "eula_accepted" in lines[1]


def test_eula_refuse_raises_exit_code_2(tmp_path: Path) -> None:
    """Entering anything other than 'I AGREE' raises UsageError with exit code 2."""
    marker = tmp_path / "eula_accepted"

    with pytest.raises(UsageError) as exc_info:
        enforce_eula(marker_path=marker, prompt_fn=lambda _: "no way")

    assert exc_info.value.exit_code == ExitCode.USAGE
    assert not is_eula_accepted(marker)


def test_eula_already_accepted_is_noop(tmp_path: Path) -> None:
    """If marker already exists, enforce_eula does not prompt."""
    marker = tmp_path / "eula_accepted"
    marker.write_text("eula_accepted_at=12345\n", encoding="utf-8")

    called = False

    def mock_prompt(_: str) -> str:
        nonlocal called
        called = True
        return "I AGREE"

    enforce_eula(marker_path=marker, prompt_fn=mock_prompt)
    assert called is False


def test_require_ack_helpers() -> None:
    """Test require_ack helper validation."""
    # When enabled and acknowledged, passes
    require_ack("feature", enabled=True, acknowledged=True, ack_flag_name="--ack")
    require_ack("feature", enabled=False, acknowledged=False, ack_flag_name="--ack")

    # Enabled without ack raises UsageError or ComplianceError
    with pytest.raises(UsageError):
        require_ack("feature", enabled=True, acknowledged=False, ack_flag_name="--ack")

    with pytest.raises(ComplianceError):
        require_ack(
            "feature",
            enabled=True,
            acknowledged=False,
            ack_flag_name="--ack",
            is_compliance=True,
        )

    # Orphan ack without enabled feature
    with pytest.raises(UsageError):
        require_ack("feature", enabled=False, acknowledged=True, ack_flag_name="--ack")
