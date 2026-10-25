"""Config loader for LiveGuard.

Reads a YAML config file, applies environment overrides (LIVEGUARD__*),
runs version migrations, and validates with pydantic v2. Converts validation
errors into friendly, field-path-annotated messages before exiting with code 2.

Usage example::

    from liveguard.config.loader import load_config

    cfg = load_config("liveguard.yaml")
    print(cfg.modules.m1.crf)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, NoReturn

import yaml
from pydantic import ValidationError

from liveguard.config.migrations import migrate_config
from liveguard.config.schema import LiveGuardConfig
from liveguard.errors import UsageError
from liveguard.exit_codes import ExitCode


def _parse_env_value(val: str) -> Any:
    """Parse string environment value to appropriate Python scalar."""
    lowered = val.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        return val


def apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply environment overrides with LIVEGUARD__ prefix.

    Double underscore represents dictionary nesting:
    LIVEGUARD__MODULES__M4__OBS_HOST="obs.local"
    -> raw["modules"]["m4"]["obs_host"] = "obs.local"

    Also supports common shorthand aliases:
    LIVEGUARD__OBS__HOST -> raw["modules"]["m4"]["obs_host"]
    LIVEGUARD__OBS__PORT -> raw["modules"]["m4"]["obs_port"]

    Args:
        raw: Mutable raw dictionary parsed from YAML.

    Returns:
        Dictionary with environment overrides merged in.
    """
    prefix = "LIVEGUARD__"
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue

        stripped = env_key[len(prefix) :].lower()
        parts = stripped.split("__")
        parsed_val = _parse_env_value(env_val)

        # Handle alias: obs__host / obs__port -> modules__m4__obs_host / obs_port
        if parts == ["obs", "host"]:
            parts = ["modules", "m4", "obs_host"]
        elif parts == ["obs", "port"]:
            parts = ["modules", "m4", "obs_port"]

        cursor = raw
        for _i, part in enumerate(parts[:-1]):
            if part not in cursor or not isinstance(cursor[part], dict):
                cursor[part] = {}

            cursor = cursor[part]

        cursor[parts[-1]] = parsed_val

    return raw


def load_config(path: str | Path) -> LiveGuardConfig:
    """Load, migrate, and validate a LiveGuard YAML config file.

    Reads *path*, parses it as YAML, applies any LIVEGUARD__* environment
    variable overrides, migrates version schema, and validates against
    :class:`~liveguard.config.schema.LiveGuardConfig`.

    Args:
        path: Path to the ``liveguard.yaml`` configuration file.

    Returns:
        A fully-validated :class:`~liveguard.config.schema.LiveGuardConfig`
        instance.

    Raises:
        SystemExit: With exit code 2 if the file is missing, unreadable,
            not valid YAML, or fails pydantic validation.
    """
    config_path = Path(path)

    # --- file existence -------------------------------------------------
    if not config_path.exists():
        _fatal(f"Config file not found: {config_path}")

    # --- YAML parse -----------------------------------------------------
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _fatal(f"Failed to parse YAML in {config_path}:\n  {exc}")

    if not isinstance(raw, dict):
        _fatal(
            f"Config file {config_path} must be a YAML mapping, "
            f"got {type(raw).__name__}"
        )

    # --- environment variable overrides ---------------------------------
    raw = apply_env_overrides(raw)

    # --- version migration ----------------------------------------------
    try:
        raw = migrate_config(raw)
    except UsageError as exc:
        _fatal(f"Configuration migration error in {config_path}:\n  {exc.message}")

    # --- pydantic validation --------------------------------------------
    try:
        return LiveGuardConfig.model_validate(raw)
    except ValidationError as exc:
        lines = [f"Configuration error in {config_path}:"]
        for error in exc.errors():
            field_path = " → ".join(str(loc) for loc in error["loc"])
            msg = error["msg"]
            lines.append(f"  [{field_path}] {msg}")
        _fatal("\n".join(lines))


def _fatal(message: str) -> NoReturn:
    """Print *message* to stderr and exit with code 2.

    Args:
        message: Human-readable error description.
    """
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(ExitCode.USAGE)
