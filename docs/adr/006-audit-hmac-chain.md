# ADR 006: HMAC-SHA256 Cryptographic Hash Chain for Append-Only Audit Logging

## Status
Accepted (Phase 5)

## Context
LiveGuard operates under strict compliance, legal, and ethical constraints:
- C2PA provenance stripping is forbidden without explicit legal acknowledgement.
- M2 robustness testing is strictly restricted to watermarked research use.
- Live stream operations require active disclosure banners and unexpired Terms of Service acknowledgements.
- First run requires an unambiguous End User License Agreement (EULA).

If audit logs are stored in plain text or standard logfiles, an actor or rogue script could alter, delete, or reorder records after the fact (e.g., removing a provenance stripping event or forging a ToS timestamp). To establish non-repudiation, tamper-evidence, and verifiable compliance, the audit subsystem requires cryptographic immutability.

## Decision
We implement an append-only cryptographic audit log (`audit/events.jsonl`) utilizing a hash chain coupled with HMAC-SHA256 signatures:

1. **Hash Chaining (`prev_hash`)**:
   - Each entry contains the SHA-256 digest of the exact preceding JSON line in the file:
     $$\text{prev\_hash}_i = \text{SHA-256}(\text{line}_{i-1})$$
   - The genesis entry is anchored with a deterministic sentinel: `prev_hash = "0" * 64`.

2. **HMAC-SHA256 Signatures**:
   - Every line is signed with an HMAC key derived directly from the creator's age private key via HKDF (RFC 5869) with context string `b"audit"`:
     $$\text{HMAC\_KEY} = \text{HKDF}(\text{age\_secret\_key}, \text{info}=b"\text{audit}", L=32)$$
   - Signing every entry with a key derived from the creator's private key guarantees that an unauthorized party without the private key cannot rewrite or re-sign entries.

3. **Concurrency & Atomicity**:
   - Appends use `os.O_CREAT | os.O_RDWR | os.O_APPEND` and acquire an exclusive lock via `fcntl.flock(fd, fcntl.LOCK_EX)` during line calculation and write.

4. **Self-Verification**:
   - `liveguard audit verify` recomputes the entire chain sequentially and checks every HMAC signature. It reports the first corrupted line index and exits with code `0` on success or code `6` (`AUDIT_WRITE`) on corruption.

## Consequences
- **Positive**:
  - Full cryptographic tamper-evidence: modifying, deleting, or inserting a line breaks the `prev_hash` chain and fails HMAC verification.
  - Zero key management overhead: users manage a single root age key (`secrets/age.key`), and audit keys are derived deterministically.
  - Fast linear verification ($O(N)$ with lightweight SHA-256 operations).
- **Negative / Trade-offs**:
  - Log compaction or pruning requires an explicit re-anchoring procedure with a signed rollover event.
  - Log verify requires read access to `secrets/age.key`.
