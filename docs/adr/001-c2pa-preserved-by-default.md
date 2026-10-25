# ADR 001: C2PA Content Provenance Preserved by Default

## Status
Accepted (Phase 1)

## Context
Creators frequently need to strip personal identifying metadata (GPS coordinates, camera serial numbers, lens EXIF tags, creation usernames) from recorded livestream footage before publishing or streaming. However, modern content authentication standards like C2PA (Coalition for Content Provenance and Authenticity) embed cryptographic assertions proving provenance and authenticity.

Naive metadata strippers strip all metadata indiscriminately, including C2PA manifests. Stripping provenance blindly can mislead viewers, violate platform terms or emerging legal frameworks around synthetic and authentic media disclosures, or strip proof of creator copyright ownership.

## Decision
LiveGuard mandates that C2PA provenance manifests are **preserved by default**:
1. **Preservation**: The M1 Privacy Normalizer automatically detects C2PA manifests in input media via ExifTool. If FFmpeg re-encoding drops C2PA data, a structured warning (`c2pa_dropped_by_codec`) is logged in the normalization report.
2. **Explicit Opt-In Only**: C2PA removal requires BOTH `--strip-provenance` AND `--i-understand-legal-risk`. Setting only one flag raises a `ComplianceError` (exit code 3).
3. **Immutable Audit Record**: When provenance is stripped, an audit event `provenance_stripped` is logged with the user's acknowledgement hashes.
4. **No Metadata Spoofing**: Synthetic metadata injection uses only neutral identifiers (e.g. `Comment="Normalized by LiveGuard"`) and strictly forbids spoofing device model, camera serial numbers, or location coordinates.

## Consequences
- **Positive**:
  - Protects creator provenance and aligns with content authenticity standards.
  - Hard compliance gates prevent accidental removal of cryptographic provenance signatures.
  - Transparent audit trail whenever provenance is deliberately stripped.
- **Negative / Trade-offs**:
  - Requires ExifTool alongside FFmpeg on the system PATH.
  - Re-encoding pipelines must explicitly handle container-level manifest preservation.
