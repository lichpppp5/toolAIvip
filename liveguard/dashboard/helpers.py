r"""Pure helper functions for the LiveGuard Streamlit dashboard.

Decoupled from Streamlit execution to allow 100% deterministic unit testing
of compliance checks, log tailing, uptime formatting, and secure stream key management.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from liveguard.audit.chain import AuditChain
from liveguard.config.schema import LiveGuardConfig
from liveguard.orchestrator.state import RuntimeState
from liveguard.secrets.vault import SecretVault

logger = logging.getLogger("liveguard.dashboard.helpers")

DEFAULT_LOG_PATH = Path("logs/liveguard.log")


def format_uptime(seconds: float | None) -> str:
    """Format duration in seconds into a human-readable HH:MM:SS string.

    Args:
        seconds: Uptime duration in seconds, or None if inactive.

    Returns:
        Formatted string (e.g. '02h 15m 30s' or 'Offline').
    """
    if seconds is None or seconds <= 0:
        return "00h 00m 00s"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}h {minutes:02d}m {secs:02d}s"


def calculate_tos_age_days(tos_ack_at: datetime | str | None) -> float | None:
    """Calculate age in days of a ToS acknowledgement timestamp.

    Args:
        tos_ack_at: Datetime or ISO timestamp string, or None.

    Returns:
        Elapsed days as a float rounded to 2 decimals, or None.
    """
    if tos_ack_at is None:
        return None
    if isinstance(tos_ack_at, str):
        try:
            # Parse ISO format
            dt = datetime.fromisoformat(tos_ack_at)
        except ValueError:
            return None
    else:
        dt = tos_ack_at

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = (now - dt).total_seconds()
    return max(0.0, round(diff / 86400.0, 2))


def tail_log_file(path: Path | str | None = None, n: int = 20) -> list[str]:
    """Read the last `n` lines from the specified log file.

    Args:
        path: Path to log file. Defaults to `logs/liveguard.log`.
        n: Maximum number of lines to return.

    Returns:
        List of trailing log lines.
    """
    target = Path(path) if path else DEFAULT_LOG_PATH
    if not target.is_file():
        return []
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        return lines[-n:]
    except Exception as err:
        logger.debug("Failed to tail log file %s: %s", target, err)
        return []


def save_stream_key_to_vault(
    key: str,
    vault: SecretVault,
    audit: AuditChain,
) -> dict[str, Any]:
    """Store the user-entered TikTok RTMP stream key securely into the age vault.

    CRITICAL SECURITY INVARIANT:
      - The key value is NEVER logged.
      - The key value is NEVER written to the audit chain payload.
      - The audit event payload contains only `{"set": True}`.

    Args:
        key: Raw TikTok stream key entered by user.
        vault: SecretVault instance.
        audit: AuditChain instance.

    Returns:
        Dictionary indicating status and feedback message.
    """
    cleaned = key.strip()
    if not cleaned:
        return {"success": False, "message": "Stream key cannot be empty."}

    # Save to vault
    vault.set("tiktok_stream_key", cleaned)

    # Log audit event with ONLY boolean indicator
    audit.append(
        event="stream_key_set",
        module="dashboard",
        payload={"set": True},
    )

    logger.info("TikTok stream key securely saved to vault")
    return {"success": True, "message": "Stream key securely saved to vault."}


def clear_stream_key_from_vault(
    vault: SecretVault,
    audit: AuditChain,
) -> dict[str, Any]:
    """Erase the saved TikTok stream key from the age vault.

    Args:
        vault: SecretVault instance.
        audit: AuditChain instance.

    Returns:
        Dictionary indicating status and feedback message.
    """
    vault.set("tiktok_stream_key", "")

    # Log audit event with ONLY boolean indicator
    audit.append(
        event="stream_key_cleared",
        module="dashboard",
        payload={"set": False},
    )

    logger.info("TikTok stream key cleared from vault")
    return {"success": True, "message": "Stream key erased from vault."}


def has_saved_stream_key(vault: SecretVault) -> bool:
    """Check whether a valid stream key currently exists in the vault.

    Args:
        vault: SecretVault instance.

    Returns:
        True if a non-empty stream key is present, False otherwise.
    """
    val = vault.get("tiktok_stream_key")
    return bool(val and val.strip())


def get_module_statuses(
    state: RuntimeState,
    cfg: LiveGuardConfig | None = None,
) -> dict[str, dict[str, Any]]:
    """Synthesize module operational status and configuration state.

    Args:
        state: Active or loaded RuntimeState.
        cfg: Optional LiveGuardConfig.

    Returns:
        Dictionary mapping module key (m1..m4) to status info.
    """
    results: dict[str, dict[str, Any]] = {}
    modules_meta = {
        "m1": ("Privacy Normalizer", "m1_normalizer"),
        "m2": ("Robustness Tester", "m2_tester"),
        "m3": ("Geo-Test & Session", "m3_geo"),
        "m4": ("OBS Orchestrator", "m4_obs"),
    }

    for mod_key, (label, phase_name) in modules_meta.items():
        enabled = True
        if cfg is not None:
            mod_cfg = getattr(cfg.modules, mod_key, None)
            if mod_cfg is not None:
                enabled = bool(getattr(mod_cfg, "enabled", True))

        mod_dict: dict[str, Any] = getattr(state, f"{mod_key}_status", {})
        status_val = mod_dict.get("status", "idle")

        if not enabled:
            status = "disabled"
        elif phase_name in state.active_phase and state.pipeline_active:
            status = "running"
        elif isinstance(status_val, str) and status_val.lower() in ("error", "failed"):
            status = "error"
        else:
            status = str(status_val) if status_val else "idle"

        results[mod_key] = {
            "name": label,
            "enabled": enabled,
            "status": status,
            "details": mod_dict,
        }

    return results
