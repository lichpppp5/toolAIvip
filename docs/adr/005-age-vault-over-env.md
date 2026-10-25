# ADR 005: age Encrypted Vault over Plain Environment Variables for Secret Storage

## Status
Accepted (Phase 5)

## Context
LiveGuard orchestrates live creator streams requiring sensitive credentials, including OBS WebSocket authentication passwords, RTMP stream keys, and platform API tokens.

Common practices rely on reading secrets directly from environment variables (e.g. `export OBS_WS_PASSWORD=...`) or unencrypted `.env` files. However, this introduces critical security vulnerabilities in creator environments:
1. **Process Exposure**: On UNIX systems, process environment tables are readable by any process running under the same UID (via `/proc/<pid>/environ` or `ps e`).
2. **Subprocess Leakage**: Forked child processes (e.g., FFmpeg, Playwright Node bridges, system tools) inherit the parent environment by default unless rigorously sanitized.
3. **Crash Dump & Telemetry Leaks**: Stack traces, unhandled exceptions, and diagnostic dumps frequently serialize `os.environ` dictionaries.
4. **Tool Sprawl**: Traditional PGP/GPG toolchains are notoriously complex, requiring daemon management (`gpg-agent`), large keyrings, and error-prone key configuration.

## Decision
We mandate that no module may access `os.environ` directly for credentials. All secrets are managed through an encrypted local vault (`liveguard.secrets.vault.SecretVault`) backed by `age` encryption using `pyrage` (Rust bindings for the official `rage` implementation).

Key implementation choices:
1. **X25519 Asymmetric Encryption**: First run automatically generates an age identity key at `secrets/age.key` with strict `chmod 600` POSIX permissions, printing the public recipient fingerprint to stdout with a backup reminder.
2. **Encrypted Backing File**: Secrets are serialized to JSON and encrypted to the local recipient key, producing `secrets/vault.age`.
3. **pyrge Library**: We selected `pyrage>=1.4.0` because it provides audited Rust cryptographic primitives (X25519, ChaCha20-Poly1305), pre-compiled wheels for macOS and Linux, memory-safe key handling, and zero external binary dependencies.
4. **Graceful Migration Fallback**: To support developer environments and existing workflows, `vault.get("obs_ws_password")` falls back to `OBS_WS_PASSWORD` if the vault is unpopulated, emitting a mandatory `vault_env_fallback` warning to structlog and the audit chain.

## Consequences
- **Positive**:
  - Eliminates plaintext credential exposure in process listings, crash reports, and child processes.
  - Zero-configuration cryptographic bootstrap for creators without needing GPG.
  - Clear audit trail when unencrypted environment fallback is used.
- **Negative / Trade-offs**:
  - Requires creators to safeguard `secrets/age.key`. Loss of this file prevents vault recovery.
  - Adds dependency on `pyrage`.
