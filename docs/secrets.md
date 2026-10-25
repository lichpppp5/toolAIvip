# LiveGuard Secret Vault

LiveGuard provides a zero-leak, age-encrypted secrets vault (`liveguard/secrets/vault.py`) for managing sensitive credentials (OBS WebSocket passwords, stream keys, and authentication tokens).

---

## 1. Principles & Rules

- **Zero Direct Environment Access for Modules**: Modules (such as M4 OBS Orchestrator) never query `os.environ` directly for secrets. All secret lookups must go through `SecretVault.get(key)`.
- **Modern Asymmetric Encryption**: Backed by `age` (via `pyrage` Rust bindings). Secrets are encrypted using an X25519 recipient public key and decrypted with the corresponding age private key.
- **Strict File Permissions**: The identity key file (`secrets/age.key`) is generated with `0o600` permissions (`-rw-------`), accessible solely by the local operating system user.

---

## 2. First Run & Key Generation

When LiveGuard starts for the first time without an existing identity key:

1. A new age X25519 keypair is generated.
2. The private identity key is written to `secrets/age.key` with permissions `chmod 600`.
3. The public recipient fingerprint is printed to `stdout` alongside a prominent backup reminder:
   ```text
   [liveguard:vault] Generated new age identity key: age1...
   [liveguard:vault] WARNING: Back up your secret key at secrets/age.key!
   ```

> [!WARNING]
> If `secrets/age.key` is deleted or lost, secrets stored in `secrets/vault.age` cannot be decrypted and the HMAC audit log cannot be verified. Back up this file to secure offline storage.

---

## 3. Storage Format (`secrets/vault.age`)

The backing store is an encrypted JSON dictionary payload:
1. All secret key-value pairs are serialized to JSON.
2. The JSON payload is encrypted to the recipient public key via age.
3. The resulting ciphertext is written atomically to `secrets/vault.age`.

---

## 4. API Reference

```python
from liveguard.secrets.vault import get_vault

vault = get_vault()

# Store a secret
vault.set("obs_ws_password", "MySecureOBSPassword123!")

# Retrieve a secret
password = vault.get("obs_ws_password")

# List stored secret keys (values are never exposed)
keys = vault.list_keys()
# -> ["obs_ws_password"]
```

---

## 5. Backward-Compatible Environment Fallback

During initial migration or in ephemeral developer containers where the vault has not yet been populated:

If `vault.get("obs_ws_password")` is called and the key does not exist in the encrypted vault:
1. The vault inspects `os.environ.get("OBS_WS_PASSWORD")`.
2. If found, it emits a `vault_env_fallback` warning log to structlog and the audit chain:
   ```text
   [warning] vault_env_fallback: SecretVault not yet populated with obs_ws_password; using environment.
   ```
3. Once the creator stores the password in the vault using `vault.set("obs_ws_password", "...")`, the vault value takes precedence and no warning is emitted.
