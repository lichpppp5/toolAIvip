# ADR 003: Manual RTMP Stream Key Entry and Zero-Scraping Policy

## Status
Accepted (Phase 4)

## Context
Automated livestreaming software frequently attempts to extract RTMP stream keys and ingest URLs by scraping the TikTok Web Studio or Live Producer interface, reverse-engineering private mobile GraphQL endpoints, or capturing browser WebSocket traffic during login.

Such scraping techniques violate platform Terms of Service (specifically regarding unauthorized automated access and credential extraction), introduce account security risks, and break unpredictably whenever the host platform updates its front-end DOM or anti-bot heuristics.

## Decision
LiveGuard strictly enforces a **Zero-Scraping, Manual-Entry** architecture:
1. **No Stream Key Scraping**: Neither the Node.js Playwright bridge (`node/m3`) nor Python modules ever inspect, capture, extract, or parse stream keys or ingest credentials from platform web pages or network responses.
2. **Explicit Creator Input**: Creators manually copy their RTMP stream key from their TikTok Live Producer dashboard and submit it via the local Streamlit dashboard or CLI into the age-encrypted secret vault (`secrets/vault.age`).
3. **No Credential Logging or Retention in Audit**: Stream keys are held in memory only for the duration of the OBS streaming session. They are never written to disk unencrypted, never logged in structlog or console output, and audit event payloads record only boolean status flags (`{"set": true}`).
4. **No Residential Proxy Rotation for Evasion**: Geo-testing proxies are explicitly configured by the operator for reachability latency checks, not dynamically rotated to evade platform bot detection.

## Consequences
- **Positive**:
  - Full compliance with platform Terms of Service prohibiting automated credential harvesting.
  - Immunity to platform UI updates and anti-bot obfuscation changes.
  - Creator retains complete custody and visibility over their live stream key.
- **Negative / Trade-offs**:
  - Requires one-time manual copy-pasting of the stream key by the creator rather than fully autonomous 1-click startup.
