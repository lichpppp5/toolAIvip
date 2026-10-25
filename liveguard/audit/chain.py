"""Append-only cryptographic audit chain for LiveGuard.

Implements an append-only JSONL log (audit/events.jsonl) where every entry
is chained to the previous entry via SHA-256 and authenticated with HMAC-SHA256
derived from the vault's age private key via HKDF.

CRITICAL INVARIANTS:
- Append operations MUST be atomic: O_APPEND + fcntl.flock.
- Genesis entry is a sentinel record with prev_hash = "0" * 64.
- Any tampering (modifying, deleting, or reordering lines) breaks the chain.
"""

from __future__ import annotations

import fcntl
import getpass
import hashlib
import hmac
import json
import os
import platform
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from liveguard.errors import AuditWriteError
from liveguard.secrets.vault import SecretVault, get_vault

logger = structlog.get_logger("liveguard.audit.chain")


def hkdf_derive_audit_key(age_key_str: str, info: bytes = b"audit") -> bytes:
    """Derive a 32-byte HMAC key using HKDF-SHA256 (RFC 5869).

    Args:
        age_key_str: Secret age identity key string (e.g. AGE-SECRET-KEY-...).
        info: Context and application specific information bytes.

    Returns:
        32-byte derived symmetric key.
    """
    ikm = age_key_str.encode("utf-8")
    salt = b"\x00" * 32  # Standard zero-filled salt
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = hmac.new(prk, info + b"\x01", hashlib.sha256).digest()
    return okm[:32]


def compute_actor_hash() -> str:
    """Compute anonymous machine actor hash from node, username, and MAC address."""
    ident_str = f"{platform.node()}:{getpass.getuser()}:{uuid.getnode()}"
    return hashlib.sha256(ident_str.encode("utf-8")).hexdigest()


def compute_entry_hmac(entry_without_hmac: dict[str, Any], key: bytes) -> str:
    """Compute HMAC-SHA256 hex digest for an audit entry dictionary.

    Args:
        entry_without_hmac: Entry dictionary (must NOT contain the 'hmac' key).
        key: Derived 32-byte HMAC key.

    Returns:
        64-character lowercase hex digest string.
    """
    canonical_json = json.dumps(
        entry_without_hmac, sort_keys=True, separators=(",", ":")
    )
    return hmac.new(key, canonical_json.encode("utf-8"), hashlib.sha256).hexdigest()


class AuditChain:
    """Append-only HMAC-authenticated audit log manager."""

    def __init__(
        self,
        audit_file: Path | None = None,
        vault: SecretVault | None = None,
        log_path: Path | None = None,
    ) -> None:
        """Initialize AuditChain.

        Args:
            audit_file: Path to audit log JSONL file.
            vault: SecretVault instance used to obtain the root age identity key.
            log_path: Alias for audit_file.
        """
        target = (
            log_path
            if log_path is not None
            else (audit_file if audit_file is not None else Path("audit/events.jsonl"))
        )
        self._audit_file = Path(target)
        self._vault = vault if vault is not None else get_vault()
        self._hmac_key: bytes | None = None

    def _get_hmac_key(self) -> bytes:
        """Retrieve and cache the derived HMAC key from the vault's age identity."""
        if self._hmac_key is None:
            raw_age_key = self._vault.get_raw_age_key()
            self._hmac_key = hkdf_derive_audit_key(raw_age_key)
        return self._hmac_key

    def append(
        self,
        event: str,
        module: str,
        payload: dict[str, Any] | None = None,
        actor_hash: str | None = None,
    ) -> dict[str, Any]:
        """Atomically append a new entry to the audit log.

        Uses O_APPEND + fcntl.flock to guarantee atomic execution across
        processes and threads.

        Args:
            event: Event identifier, e.g. 'm4_compliance_pass', 'eula_accepted'.
            module: Originating module name, e.g. 'M1', 'M2', 'M4', 'CLI'.
            payload: Freeform structured event attributes.
            actor_hash: Optional actor hash override.

        Returns:
            The newly created and committed audit entry dict.

        Raises:
            AuditWriteError: If file permissions or write operations fail.
        """
        self._audit_file.parent.mkdir(parents=True, exist_ok=True)
        key = self._get_hmac_key()
        effective_actor = actor_hash if actor_hash else compute_actor_hash()
        effective_payload = payload if payload is not None else {}

        try:
            fd = os.open(
                str(self._audit_file),
                os.O_CREAT | os.O_RDWR | os.O_APPEND,
                stat.S_IRUSR | stat.S_IWUSR,  # chmod 600
            )
        except OSError as exc:
            raise AuditWriteError(
                f"Failed to open audit log at {self._audit_file}: {exc}"
            ) from exc

        try:
            # Acquire exclusive lock
            fcntl.flock(fd, fcntl.LOCK_EX)

            # Read all existing lines while locked
            with open(fd, "r+", encoding="utf-8", closefd=False) as f:
                f.seek(0)
                content = f.read()
                lines = [line.strip() for line in content.splitlines() if line.strip()]

                # Initialize genesis entry if file is brand new
                if not lines:
                    genesis_base: dict[str, Any] = {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "event": "genesis",
                        "module": "audit",
                        "actor_hash": effective_actor,
                        "payload": {
                            "version": "0.1.0",
                            "message": "LiveGuard audit chain initialized",
                        },
                        "prev_hash": "0" * 64,
                    }
                    genesis_base["hmac"] = compute_entry_hmac(genesis_base, key)
                    genesis_line = json.dumps(genesis_base, sort_keys=True)
                    f.write(f"{genesis_line}\n")
                    f.flush()
                    lines.append(genesis_line)

                # Compute prev_hash from the exact text of the preceding line
                last_line = lines[-1]
                prev_hash = hashlib.sha256(last_line.encode("utf-8")).hexdigest()

                # Build new entry
                entry: dict[str, Any] = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": event,
                    "module": module,
                    "actor_hash": effective_actor,
                    "payload": effective_payload,
                    "prev_hash": prev_hash,
                }
                entry["hmac"] = compute_entry_hmac(entry, key)

                new_line = json.dumps(entry, sort_keys=True)
                f.write(f"{new_line}\n")
                f.flush()
                os.fsync(fd)

            return entry

        except Exception as exc:  # noqa: BLE001
            logger.error("audit_append_failed", error=str(exc), event=event)
            raise AuditWriteError(f"Failed to append to audit log: {exc}") from exc

        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        """Return the last n entries from the audit log."""
        if not self._audit_file.is_file():
            return []

        entries: list[dict[str, Any]] = []
        try:
            with open(self._audit_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError as exc:
            logger.warning("audit_read_failed", error=str(exc))
            return []

        return entries[-n:]


_GLOBAL_AUDIT_CHAIN: AuditChain | None = None


def get_audit_chain(log_path: Path | None = None) -> AuditChain:
    """Retrieve the shared AuditChain singleton.

    Args:
        log_path: Optional custom path to audit events log.

    Returns:
        AuditChain instance.
    """
    global _GLOBAL_AUDIT_CHAIN
    if _GLOBAL_AUDIT_CHAIN is None or log_path is not None:
        chain = AuditChain(log_path=log_path)
        if log_path is None:
            _GLOBAL_AUDIT_CHAIN = chain
        return chain
    return _GLOBAL_AUDIT_CHAIN
