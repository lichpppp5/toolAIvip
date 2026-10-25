"""Secret vault implementation using age encryption (pyrage).

Provides encrypted key-value storage for secrets (stream keys, credentials,
passwords) with automatic key generation, chmod 600 protection, and
environment variable fallback with structured audit warnings.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import structlog
from pyrage import decrypt, encrypt, x25519

from liveguard.errors import LiveGuardError

logger = structlog.get_logger("liveguard.secrets.vault")


class SecretVault:
    """Age-encrypted secrets vault.

    Attributes:
        secrets_dir: Base directory for vault and key storage.
        key_path: Path to the secret age identity key file (secrets/age.key).
        vault_path: Path to the encrypted vault file (secrets/vault.age).
    """

    def __init__(
        self,
        secrets_dir: Path | None = None,
        key_path: Path | None = None,
        vault_path: Path | None = None,
    ) -> None:
        """Initialize SecretVault.

        Args:
            secrets_dir: Base secrets directory. Defaults to ``secrets/``.
            key_path: Explicit path to identity file.
            vault_path: Explicit path to encrypted storage file.
        """
        if secrets_dir is not None:
            self._secrets_dir = Path(secrets_dir)
        else:
            self._secrets_dir = Path("secrets")

        self._key_path = Path(key_path) if key_path else self._secrets_dir / "age.key"
        self._vault_path = (
            Path(vault_path) if vault_path else self._secrets_dir / "vault.age"
        )
        self._identity: x25519.Identity | None = None
        self._std_logger = logging.getLogger("liveguard.secrets")
        self._ensure_key()

    def _ensure_key(self) -> x25519.Identity:
        """Load or generate the recipient key at secrets/age.key (chmod 600)."""
        if self._identity is not None:
            return self._identity

        self._key_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._key_path.is_file():

            # First run: generate new recipient key
            identity = x25519.Identity.generate()
            recipient = identity.to_public()

            key_str = str(identity)
            self._key_path.write_text(f"{key_str}\n", encoding="utf-8")
            os.chmod(self._key_path, stat.S_IRUSR | stat.S_IWUSR)

            msg = (
                f"\n[liveguard:vault] Generated new age identity key: {recipient}\n"
                "[liveguard:vault] WARNING: Back up your secret key at "
                f"{self._key_path}!\n"
            )
            print(msg)

            logger.info(
                "vault_key_generated",
                recipient=str(recipient),
                path=str(self._key_path),
            )
            self._identity = identity
            return self._identity

        # Load existing key
        raw = self._key_path.read_text(encoding="utf-8").strip()
        lines = [
            line.strip()
            for line in raw.splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if not lines:
            raise LiveGuardError(f"Corrupt or empty age key file at {self._key_path}")

        try:
            self._identity = x25519.Identity.from_str(lines[0])
            return self._identity
        except Exception as exc:  # noqa: BLE001
            raise LiveGuardError(
                f"Failed to load age identity from {self._key_path}: {exc}"
            ) from exc

    def _load_vault_data(self) -> dict[str, str]:
        """Decrypt and return the dictionary stored in vault.age."""
        identity = self._ensure_key()

        if not self._vault_path.is_file():
            return {}

        try:
            ciphertext = self._vault_path.read_bytes()
            if not ciphertext:
                return {}
            plaintext_bytes = decrypt(ciphertext, [identity])
            data: dict[str, str] = json.loads(plaintext_bytes.decode("utf-8"))
            return data
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "vault_decrypt_failed", error=str(exc), path=str(self._vault_path)
            )
            raise LiveGuardError(
                f"Failed to decrypt vault at {self._vault_path}: {exc}"
            ) from exc

    def _save_vault_data(self, data: dict[str, str]) -> None:
        """Encrypt and write dictionary to vault.age (chmod 600)."""
        identity = self._ensure_key()
        recipient = identity.to_public()

        plaintext = json.dumps(data, indent=2).encode("utf-8")
        ciphertext = encrypt(plaintext, [recipient])

        self._vault_path.parent.mkdir(parents=True, exist_ok=True)
        self._vault_path.write_bytes(ciphertext)
        os.chmod(self._vault_path, stat.S_IRUSR | stat.S_IWUSR)

    def get(self, key: str) -> str | None:
        """Retrieve a decrypted secret by key, falling back to environment if needed.

        Args:
            key: Name of secret to retrieve.

        Returns:
            The secret string, or None if not set anywhere.
        """
        data = self._load_vault_data()
        val = data.get(key)
        if val is not None:
            return val

        # Check fallback for OBS WebSocket password
        if (
            key.upper() in ("OBS_WS_PASSWORD", "OBS-WS-PASSWORD")
            or key.lower() == "obs_ws_password"
        ):
            env_val = os.environ.get("OBS_WS_PASSWORD")
            if env_val is not None:
                logger.warning(
                    "vault_env_fallback",
                    key=key,
                    message="SecretVault not yet populated; using OBS_WS_PASSWORD.",
                )
                self._std_logger.warning(
                    "vault_env_fallback: SecretVault unpopulated with %s; using env.",
                    key,
                )
                return env_val

        return None

    def set(self, key: str, value: str) -> None:
        """Store an encrypted secret.

        Args:
            key: Secret identifier.
            value: Plaintext secret content to encrypt and save.
        """
        data = self._load_vault_data()
        data[key] = value
        self._save_vault_data(data)
        logger.info("vault_secret_set", key=key)

    def list_keys(self) -> list[str]:
        """List all secret keys stored in the vault."""
        data = self._load_vault_data()
        return sorted(data.keys())

    def get_raw_age_key(self) -> str:
        """Return the raw age secret key string for cryptographic key derivation."""
        identity = self._ensure_key()
        return str(identity)


_default_vault: SecretVault | None = None


def get_vault() -> SecretVault:
    """Return the singleton application SecretVault instance."""
    global _default_vault
    if _default_vault is None:
        _default_vault = SecretVault()
    return _default_vault
