"""Configuration schema migrations for LiveGuard.

Ensures backward and forward compatibility across configuration versions.
"""

from __future__ import annotations

from typing import Any

from liveguard.errors import UsageError


def migrate_config(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate raw configuration dictionary to the current schema version.

    Args:
        data: Raw configuration dictionary parsed from YAML.

    Returns:
        Migrated configuration dictionary conforming to version 1.

    Raises:
        UsageError: If the configuration version is unknown or unsupported.
    """
    version = data.get("version", 1)

    # v1 is the current baseline schema; no-op
    if version == 1 or version == "1":
        data["version"] = 1
        return data

    raise UsageError(
        f"Unsupported configuration schema version: {version}. "
        "Supported versions: [1]."
    )
