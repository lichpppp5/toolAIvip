# ADR 007: Pydantic v2 vs Standard Dataclasses for System Configuration

## Status
Accepted (Phase 5)

## Context
LiveGuard requires a robust configuration management system to parse user YAML files (`liveguard.yaml`), environment variable overrides (`LIVEGUARD__*`), validate strict compliance policies (e.g. disclosure banner settings, ToS acknowledgement timestamps), and handle schema versioning over time.

We evaluated two architectural approaches for configuration modeling:
1. Python standard library `@dataclass`.
2. Pydantic v2 (`pydantic.BaseModel`).

## Decision
We chose **Pydantic v2** for all configuration models (`liveguard.config.schema`):
1. **Rich Validation**: Pydantic v2 provides compiled Rust validation via `pydantic-core`, offering microsecond validation speeds alongside expressive field validators (e.g. URL validation for proxies, range checking on CRF and port numbers, regex parsing).
2. **Actionable Error Messages**: On validation failure, Pydantic produces structured error metadata with exact field paths (e.g. `modules.m1.crf`), input values, and validation contexts. The config loader translates these into concise CLI error hints, exiting cleanly with exit code 2.
3. **Environment Overrides**: Deeply nested configuration fields can be overridden using double-underscore environment variables (`LIVEGUARD__OBS__HOST=192.168.1.10`) with automatic type coercion and validation before the application boots.
4. **Schema Migrations**: Configuration models declare a required `version: int` field. A dedicated migration registry (`liveguard.config.migrations`) intercepts raw dictionary trees prior to validation, enabling seamless automated forward-migrations between schema versions.

In contrast, `@dataclass` was retained for internal runtime models (`RuntimeState`, `StreamStatus`, `NormalizeReport`) where schema parsing and external input sanitization are not required.

## Consequences
- **Positive**:
  - Eliminates invalid runtime configuration states before any module initializes.
  - Clear, user-friendly CLI diagnostic hints on syntax or schema errors.
  - Transparent forward-migration path for configuration schema evolution.
- **Negative / Trade-offs**:
  - Adds dependency on `pydantic>=2.9.0` and `pydantic-core`.
