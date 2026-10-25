# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Phase 8 automated self-review and benchmark scripts.

---

## [1.0.0] - 2026-10-25

### Added
- **Phase 0 — Foundation**: Core protocols, domain types, exit codes (0 to 6), structured error hierarchy (`LiveGuardError`), and foundational CLI skeleton.
- **Phase 1 — M1 Privacy Normalizer**: Metadata stripping with neutral Comment injection via FFmpeg and ExifTool. C2PA provenance manifests preserved by default; explicit opt-in removal with dual compliance flags (`--strip-provenance` and `--i-understand-legal-risk`).
- **Phase 2 — M2 Robustness Tester**: Research-only adversarial perturbation toolchain for CLIP ViT-B/32 using PGD with quality guardrails (SSIM $\ge 0.95$, PSNR $\ge 40.0$ dB), permanent visible "PERTURBED" corner watermark, and XMP research tags.
- **Phase 3 — M4 OBS Orchestrator**: Async obs-websocket v5 client with thread pool isolation, circuit breakers, jittered exponential backoff, and non-negotiable compliance gates refusing to stream without AI disclosure banner (exit code 3) or with stale ToS acknowledgements (exit code 4).
- **Phase 4 — M3 Geo-Test & Session Manager**: Node.js/Playwright browser bridge for RTMP geo-availability probing, proxy latency testing, and manual user login session verification with zero credential scraping.
- **Phase 5 — Orchestrator, Config, Audit Chain, and Secrets Vault**:
  - Pydantic v2 configuration loader with schema migration and double-underscore environment variable overrides.
  - Asymmetric age-encrypted secret vault (`pyrage`) with automatic key generation (`secrets/age.key`, `chmod 600`) and fallback to `OBS_WS_PASSWORD`.
  - Append-only HMAC-SHA256 chained audit log (`audit/events.jsonl`) with atomic POSIX file locking (`flock`) and cryptographic tamper verification.
  - Interactive first-run EULA gate with machine hashing.
  - Unified pipeline runner executing M1 $\to$ M2 $\to$ M3 $\to$ M4 with runtime snapshot state persistence (`runtime/state.json`).
- **Phase 6 — Streamlit Dashboard, Metrics, and Observability**:
  - Interactive Streamlit dashboard (:8501) with system uptime, compliance alerts, OBS bitrate sparkline, and secure manual RTMP stream key vault form.
  - Prometheus metrics server (:9090/metrics) exposing frame counts, bitrate gauge, proxy health, and module statuses.
  - HTTP health server (:8080/healthz) returning JSON module health and compliance indicators.
- **Phase 7 — Integration Testing, Dockerization, and Documentation**:
  - End-to-end integration tests (`tests/integration/test_e2e_pipeline.py`) validating M1 and M2 against sample video fixtures.
  - Multi-stage `Dockerfile` and `docker-compose.yml` for isolated containerized execution.
  - GitHub Actions CI workflow with linting, testing, security checks, SBOM generation, and Docker builds.
  - Complete set of 8 Architecture Decision Records (ADRs 001–008).
  - Pre-commit hooks enforcing code formatting, type checking, and forbidding `eval()` and hardcoded secrets.
