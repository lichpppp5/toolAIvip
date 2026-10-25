"""Unit tests for configuration loading, validation, migrations, and env overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from liveguard.config.loader import load_config
from liveguard.exit_codes import ExitCode

VALID_CONFIG_YAML = """
version: 1
pipeline:
  input_path: "raw.mp4"
  output_dir: "./output"
modules:
  m1:
    enabled: true
    crf: 22
  m2:
    enabled: false
  m3:
    enabled: true
    region: "US"
  m4:
    enabled: false
    obs_host: "localhost"
    obs_port: 4455
    scenes:
      - "Scene A"
      - "Scene B"
"""


def test_load_valid_config(tmp_path: Path) -> None:
    """Loading a valid configuration file returns LiveGuardConfig."""
    cfg_file = tmp_path / "liveguard.yaml"
    cfg_file.write_text(VALID_CONFIG_YAML, encoding="utf-8")

    cfg = load_config(cfg_file)
    assert cfg.version == 1
    assert cfg.pipeline.input_path == "raw.mp4"
    assert cfg.modules.m1.crf == 22
    assert cfg.modules.m2.enabled is False
    assert cfg.modules.m4.obs_port == 4455


def test_validation_error_exits_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invalid fields cause load_config to exit code 2 and print field path."""
    cfg_file = tmp_path / "invalid.yaml"
    bad_yaml = """
version: 1
modules:
  m1:
    crf: 999  # Valid range is [0, 51]
"""
    cfg_file.write_text(bad_yaml, encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        load_config(cfg_file)

    assert exc_info.value.code == ExitCode.USAGE
    captured = capsys.readouterr()
    assert "modules" in captured.err
    assert "crf" in captured.err


def test_unknown_version_raises_usage_error(tmp_path: Path) -> None:
    """Config with unsupported version causes load_config to exit code 2."""
    cfg_file = tmp_path / "v99.yaml"
    cfg_file.write_text("version: 99\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        load_config(cfg_file)

    assert exc_info.value.code == ExitCode.USAGE


def test_env_overrides_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Double-underscore env overrides are applied before validation."""
    cfg_file = tmp_path / "liveguard.yaml"
    cfg_file.write_text(VALID_CONFIG_YAML, encoding="utf-8")

    monkeypatch.setenv("LIVEGUARD__OBS__HOST", "obs.custom.local")
    monkeypatch.setenv("LIVEGUARD__MODULES__M1__CRF", "18")
    monkeypatch.setenv("LIVEGUARD__MODULES__M4__OBS_PORT", "4460")

    cfg = load_config(cfg_file)
    assert cfg.modules.m4.obs_host == "obs.custom.local"
    assert cfg.modules.m1.crf == 18
    assert cfg.modules.m4.obs_port == 4460
