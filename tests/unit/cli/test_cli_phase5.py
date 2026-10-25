"""Unit tests for Phase 5 CLI subcommands (audit, config, version, dashboard)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from liveguard.audit.chain import AuditChain
from liveguard.cli.main import app
from liveguard.exit_codes import ExitCode
from liveguard.secrets.vault import SecretVault

runner = CliRunner()


def test_cli_version_flag_and_command() -> None:
    """--version flag and version subcommand print git SHA and dependencies."""
    res_flag = runner.invoke(app, ["--version"])
    assert res_flag.exit_code == ExitCode.SUCCESS
    assert "LiveGuard v0.1.0" in res_flag.stdout
    assert "Dependencies:" in res_flag.stdout

    res_cmd = runner.invoke(app, ["version"])
    assert res_cmd.exit_code == ExitCode.SUCCESS
    assert "LiveGuard v0.1.0" in res_cmd.stdout


def test_cli_config_validate_success_and_failure(tmp_path: Path) -> None:
    """config validate exits 0 on valid YAML and 2 on invalid."""
    valid_cfg = tmp_path / "valid.yaml"
    valid_cfg.write_text("version: 1\n", encoding="utf-8")

    res_valid = runner.invoke(app, ["config", "validate", str(valid_cfg)])
    assert res_valid.exit_code == ExitCode.SUCCESS
    assert "is valid" in res_valid.stdout

    invalid_cfg = tmp_path / "invalid.yaml"
    invalid_cfg.write_text("version: 99\n", encoding="utf-8")

    res_invalid = runner.invoke(app, ["config", "validate", str(invalid_cfg)])
    assert res_invalid.exit_code == ExitCode.USAGE


def test_cli_audit_verify_success_and_tampered_exit_6(tmp_path: Path) -> None:
    """audit verify exits 0 on valid chain and exit 6 on tampered chain."""
    log_file = tmp_path / "events.jsonl"
    key_file = tmp_path / "age.key"
    vault_file = tmp_path / "vault.age"

    vault = SecretVault(vault_path=vault_file, key_path=key_file)
    chain = AuditChain(log_path=log_file, vault=vault)
    chain.append("test_event", "cli", {"status": "ok"})

    # Verify OK -> exit 0
    res_ok = runner.invoke(
        app, ["audit", "verify", "--log", str(log_file), "--key", str(key_file)]
    )
    assert res_ok.exit_code == ExitCode.SUCCESS
    assert "Audit log verified" in res_ok.stdout

    # Tamper with middle entry
    lines = log_file.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[1])
    entry["payload"]["status"] = "tampered"
    lines[1] = json.dumps(entry, separators=(",", ":"))
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Verify Tampered -> exit 6
    res_fail = runner.invoke(
        app, ["audit", "verify", "--log", str(log_file), "--key", str(key_file)]
    )
    assert res_fail.exit_code == ExitCode.AUDIT_WRITE


def test_cli_audit_tail(tmp_path: Path) -> None:
    """audit tail displays recent entries."""
    log_file = tmp_path / "events.jsonl"
    vault = SecretVault(
        vault_path=tmp_path / "vault.age", key_path=tmp_path / "age.key"
    )
    chain = AuditChain(log_path=log_file, vault=vault)

    for i in range(3):
        chain.append("evt", "cli", {"idx": i})

    res = runner.invoke(app, ["audit", "tail", "-n", "2", "--log", str(log_file)])
    assert res.exit_code == ExitCode.SUCCESS
    output_lines = [line for line in res.stdout.splitlines() if line.strip()]
    assert len(output_lines) == 2


def test_cli_dashboard_launch_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """dashboard subcommand displays launch notice."""
    from unittest.mock import MagicMock

    monkeypatch.setattr("subprocess.run", MagicMock())
    res = runner.invoke(app, ["dashboard", "--port", "8505"])
    assert res.exit_code == ExitCode.SUCCESS
    assert "8505" in res.stdout
