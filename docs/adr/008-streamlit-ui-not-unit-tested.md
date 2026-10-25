# ADR 008: Decoupled Architecture and Testing Strategy for Streamlit Dashboard UI

## Status
Accepted (Phase 6)

## Context
LiveGuard includes a local operations control panel built with Streamlit (`liveguard/dashboard/app.py`). The dashboard displays real-time pipeline status, OBS bitrate sparklines, system log tails, compliance alerts, and an encrypted vault form for manual RTMP stream key entry.

Testing Streamlit web applications in automated test suites presents well-known software engineering challenges:
1. **Runtime Execution Model**: Streamlit scripts execute sequentially from top to bottom on each interaction or rerun (`st.rerun()`), making them tightly coupled to an active Streamlit server runtime and WebSocket browser session.
2. **Headless Browser Overhead**: Automating UI browser tests (e.g. via Playwright or Selenium) for every unit test run significantly slows down CI/CD pipelines, introduces flakiness due to DOM render timings, and adds heavy external dependencies.
3. **Security Invariant Verification**: Crucial security and compliance requirements—such as ensuring that RTMP stream keys are never written to audit payloads or logs—are fundamentally domain logic concerns, not presentation concerns.

## Decision
We decouple the presentation layer from business logic using a pure helper architecture:
1. **Pure Helper Layer (`liveguard/dashboard/helpers.py`)**:
   - All core business operations, compliance evaluations, uptime formatting, log file tailing, and cryptographic vault interactions are implemented as pure, deterministic Python functions.
   - Specifically, `save_stream_key_to_vault` and `clear_stream_key_from_vault` isolate all credential handling, ensuring that stream keys are saved into `SecretVault` and audit events are appended with strictly boolean payloads (`{"set": true}`) without leaking secrets into logs or return values.
2. **Comprehensive Unit Testing (`tests/unit/dashboard/test_helpers.py`)**:
   - All pure functions in `helpers.py` are unit-tested without Streamlit dependencies, achieving high test execution speed (< 0.1s) and deterministic assertions.
   - Security invariants (e.g. verifying that stream keys do not appear in audit event payloads or log streams) are verified directly in unit tests.
3. **Streamlit UI Exception**:
   - The presentation wrapper in `liveguard/dashboard/app.py` is excluded from direct unit test execution.
   - CLI invocation (`liveguard dashboard`) is verified via Typer runner tests (`tests/unit/cli/test_cli_phase6.py`) ensuring the Streamlit process launches with the appropriate host and port flags.

## Consequences
- **Positive**:
  - Extremely fast unit test execution without spinning up browser drivers.
  - 100% deterministic test results with zero flaky UI timing failures.
  - Complete test coverage ($\ge$ 60% for `liveguard/dashboard/` and overall > 85%) on all business logic, compliance checks, and security invariants.
  - Security invariant (stream key never logged or serialized in audit payloads) is strongly enforced.
- **Negative / Trade-offs**:
  - Visual layout regressions (e.g., CSS styling, widget positioning) must be manually reviewed during end-to-end smoke testing rather than caught in unit test suites.
