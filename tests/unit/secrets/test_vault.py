"""Unit tests for the age-based secret vault (liveguard/secrets/vault.py)."""

from __future__ import annotations

import logging
import stat
from pathlib import Path

import pytest

from liveguard.secrets.vault import SecretVault


def test_vault_key_generation_and_chmod(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """First run generates age.key with chmod 600 and prints fingerprint warning."""
    key_file = tmp_path / "age.key"
    vault_file = tmp_path / "vault.age"

    vault = SecretVault(vault_path=vault_file, key_path=key_file)
    assert key_file.is_file()

    # Permissions check (0o600 -> user read/write only)
    file_mode = stat.S_IMODE(key_file.stat().st_mode)
    assert file_mode == 0o600

    # Output fingerprint warning
    captured = capsys.readouterr()
    assert "[liveguard:vault] Generated new age identity key" in captured.out
    assert "WARNING: Back up" in captured.out

    # Raw key starts with AGE-SECRET-KEY-
    raw_key = vault.get_raw_age_key()
    assert raw_key.startswith("AGE-SECRET-KEY-")


def test_vault_roundtrip_set_get_list(tmp_path: Path) -> None:
    """Test setting, getting, and listing secrets."""
    key_file = tmp_path / "age.key"
    vault_file = tmp_path / "vault.age"

    vault = SecretVault(vault_path=vault_file, key_path=key_file)

    assert vault.list_keys() == []
    assert vault.get("unknown_key") is None

    vault.set("obs_ws_password", "supersecret123")
    vault.set("api_token", "tok_xyz_987")

    # Verify vault file was created on disk
    assert vault_file.is_file()
    assert vault_file.stat().st_size > 0

    # New vault instance reading same backing files
    vault2 = SecretVault(vault_path=vault_file, key_path=key_file)
    assert sorted(vault2.list_keys()) == ["api_token", "obs_ws_password"]
    assert vault2.get("obs_ws_password") == "supersecret123"
    assert vault2.get("api_token") == "tok_xyz_987"


def test_vault_env_fallback_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test fallback to OBS_WS_PASSWORD with 'vault_env_fallback' warning."""
    key_file = tmp_path / "age.key"
    vault_file = tmp_path / "vault.age"

    vault = SecretVault(vault_path=vault_file, key_path=key_file)
    monkeypatch.setenv("OBS_WS_PASSWORD", "env_fallback_pass")

    with caplog.at_level(logging.WARNING):
        val = vault.get("obs_ws_password")

    assert val == "env_fallback_pass"
    assert "vault_env_fallback" in caplog.text


def test_vault_prefers_stored_secret_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When secret is in vault, env is not used and no warning is logged."""
    key_file = tmp_path / "age.key"
    vault_file = tmp_path / "vault.age"

    vault = SecretVault(vault_path=vault_file, key_path=key_file)
    vault.set("obs_ws_password", "vault_pass")
    monkeypatch.setenv("OBS_WS_PASSWORD", "env_fallback_pass")

    with caplog.at_level(logging.WARNING):
        val = vault.get("obs_ws_password")

    assert val == "vault_pass"
    assert "vault_env_fallback" not in caplog.text
