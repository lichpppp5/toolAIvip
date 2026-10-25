"""Audit log integrity verifier.

Recomputes the cryptographic SHA-256 prev_hash chain and validates each entry's
HMAC-SHA256 signature against the vault's derived key.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from liveguard.audit.chain import compute_entry_hmac, hkdf_derive_audit_key
from liveguard.secrets.vault import SecretVault, get_vault


def verify_audit_chain(
    audit_path: Path,
    age_key_str: str,
) -> tuple[bool, int, str]:
    """Recompute and verify the entire audit log hash chain and HMAC signatures.

    Args:
        audit_path: Path to the audit JSONL file.
        age_key_str: Secret age identity key string.

    Returns:
        A tuple of (is_valid, broken_index, message).
        If valid: (True, -1, "OK").
        If broken: (False, index, error_detail).
    """
    if not audit_path.is_file():
        return True, -1, "Audit log does not exist yet (clean)."

    lines = [
        line.strip()
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not lines:
        return True, -1, "Audit log is empty."

    hmac_key = hkdf_derive_audit_key(age_key_str)

    for i, line in enumerate(lines):
        try:
            entry: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError as exc:
            return False, i, f"JSON parse error on line {i}: {exc}"

        claimed_hmac = entry.get("hmac", "")
        claimed_prev_hash = entry.get("prev_hash", "")

        # 1. Verify prev_hash
        if i == 0:
            expected_prev_hash = "0" * 64
            if claimed_prev_hash != expected_prev_hash:
                return (
                    False,
                    0,
                    f"Genesis prev_hash mismatch: expected {'0'*64}, "
                    f"got {claimed_prev_hash}",
                )
        else:
            previous_line_text = lines[i - 1]
            expected_prev_hash = hashlib.sha256(
                previous_line_text.encode("utf-8")
            ).hexdigest()
            if claimed_prev_hash != expected_prev_hash:
                return (
                    False,
                    i,
                    f"Hash chain broken at index {i}: "
                    f"prev_hash does not match hash of entry {i-1}",
                )

        # 2. Verify HMAC
        entry_without_hmac = {k: v for k, v in entry.items() if k != "hmac"}
        calculated_hmac = compute_entry_hmac(entry_without_hmac, hmac_key)
        if not claimed_hmac or claimed_hmac != calculated_hmac:
            return (
                False,
                i,
                f"HMAC signature mismatch at index {i}: "
                "record was tampered with or signed with different key",
            )

    return True, -1, f"Audit chain valid ({len(lines)} verified entries)."


def verify_active_audit_log(
    audit_path: Path | None = None,
    vault: SecretVault | None = None,
    age_key_path: Path | None = None,
) -> tuple[bool, int, str]:
    """Verify active audit log using SecretVault or raw key file."""
    if age_key_path is not None and age_key_path.is_file():
        raw_key = age_key_path.read_text(encoding="utf-8").strip()
    else:
        v = vault if vault is not None else get_vault()
        raw_key = v.get_raw_age_key()
    p = Path(audit_path) if audit_path is not None else Path("audit/events.jsonl")

    return verify_audit_chain(p, raw_key)
