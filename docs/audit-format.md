# LiveGuard Cryptographic Audit Log Format

LiveGuard maintains an append-only, tamper-evident cryptographic audit log located at `audit/events.jsonl`.

Every critical pipeline action, compliance gate, EULA agreement, and module transition is permanently committed with a chained SHA-256 hash and authenticated with an HMAC-SHA256 signature derived from the local age private key.

---

## 1. Entry Structure

Each line in `audit/events.jsonl` is a self-contained JSON object:

```json
{
  "ts": 1729814400.123456,
  "event": "eula_accepted",
  "module": "eula",
  "actor_hash": "a1b2c3d4e5f6...",
  "payload": {
    "machine_hash": "a1b2c3d4e5f6...",
    "accepted_at": 1729814400.123
  },
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "hmac": "9f83ac6d5b4e..."
}
```

### Fields

| Field | Type | Description |
|---|---|---|
| `ts` | `float` | POSIX timestamp in seconds with microsecond resolution. |
| `event` | `string` | Unique event identifier (e.g. `genesis`, `eula_accepted`, `phase_start`, `phase_end`, `m4_compliance_pass`). |
| `module` | `string` | Originating component identifier (`eula`, `m1`, `m2`, `m3`, `m4`, `orchestrator`, `cli`). |
| `actor_hash` | `string` | SHA-256 digest identifying the local execution context (`node:user:mac`). |
| `payload` | `object` | Arbitrary structured event metadata (parameters, report summaries, error traces). |
| `prev_hash` | `string` | SHA-256 hex digest of the verbatim previous JSON line (excluding trailing newline). |
| `hmac` | `string` | Hex-encoded HMAC-SHA256 signature over the canonical JSON of the entry without the `hmac` field. |

---

## 2. Cryptographic Construction

### Genesis Sentinel

The very first entry in a new audit log is a deterministic genesis record:
- `event`: `"genesis"`
- `prev_hash`: `"0" * 64` (64 zeros)
- Signed with the HMAC key like all subsequent entries.

### Hash Chaining (`prev_hash`)

For entry $i > 0$:
$$\text{prev\_hash}_i = \text{SHA-256}(\text{line}_{i-1})$$

Where $\text{line}_{i-1}$ is the exact UTF-8 encoded string of the preceding JSON line as written to disk. Any modification, truncation, or reordering of earlier entries immediately invalidates all subsequent `prev_hash` links.

### HMAC Authentication (`hmac`)

Every record is signed using a symmetric key derived from the creator's age private key via HKDF (RFC 5869):

1. **Key Derivation (HKDF-SHA256)**:
   $$\text{PRK} = \text{HMAC-SHA256}(\text{salt}=\text{None}, \text{IKM}=\text{age\_secret\_key})$$
   $$\text{HMAC\_KEY} = \text{HKDF-Expand}(\text{PRK}, \text{info}=b"\text{audit}", L=32)$$

2. **Canonical Payload Serialization**:
   All fields except `hmac` are serialized to canonical JSON:
   ```python
   canonical = json.dumps(entry_without_hmac, sort_keys=True, separators=(",", ":"))
   ```

3. **Signature**:
   $$\text{hmac} = \text{HMAC-SHA256}(\text{HMAC\_KEY}, \text{canonical})$$

---

## 3. Concurrency & Atomicity

To prevent race conditions between CLI commands, background monitoring tasks, and subprocesses:
- Files are opened with flags `os.O_CREAT | os.O_RDWR | os.O_APPEND`.
- An exclusive advisory lock is acquired via `fcntl.flock(fd, fcntl.LOCK_EX)` before reading the previous line or appending the new entry.
- File lock is released and the file descriptor closed immediately after flush.

---

## 4. Verification CLI

The integrity of the audit log can be verified at any time using:

```bash
liveguard audit verify --log audit/events.jsonl --key secrets/age.key
```

### Exit Codes:
- **`0` (Success)**: Entire hash chain and all HMAC signatures verified cleanly.
- **`6` (Audit Failure)**: Verification failed. The CLI outputs the exact broken line index and discrepancy:
  ```text
  [liveguard] AUDIT INTEGRITY FAILURE at entry 4: HMAC signature mismatch at index 4: record was tampered with or signed with different key
  ```

To inspect recent audit events:
```bash
liveguard audit tail -n 20
```
