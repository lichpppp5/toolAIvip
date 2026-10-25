# LiveGuard — Threat Model

**Version:** 1.0
**Date:** 2025-01-01
**Status:** Active

---

## 1. Overview

LiveGuard is a **local-only** creator toolchain. All processing happens on the
creator's machine. No video frames, metadata, or credentials are transmitted
to any LiveGuard-operated server. The only outbound traffic is:

* The RTMP stream to TikTok's ingest endpoint (user-initiated).
* Geo-availability HTTP probes (read-only, no credentials attached).
* Prometheus metrics scraped locally by a dashboard (loopback only).

---

## 2. Assets

| Asset | Description | Sensitivity |
|---|---|---|
| **Source video** | Raw footage before privacy normalisation | High — may contain faces, location metadata |
| **Stream key** | TikTok RTMP stream key | Critical — enables live publishing |
| **Session cookies** | TikTok browser session (M3 probe only) | High — enables account actions |
| **OBS WS password** | OBS WebSocket authentication secret | Medium — controls stream production |
| **Audit log** | Append-only JSONL with HMAC chain | Medium — tamper evidence |
| **Age identity file** | Private key for the secrets vault | Critical — decrypts all secrets |
| **Config file** | `liveguard.yaml` with pipeline settings | Low-medium — no secrets, but reveals topology |

---

## 3. Threat Actors

| Actor | Capability | Motivation |
|---|---|---|
| **Creator (legitimate)** | Full local access | Operate the tool correctly |
| **Malicious local user** | Read local filesystem, run processes as same user | Steal stream key or cookies |
| **Platform (TikTok)** | Analyse inbound RTMP traffic, session behaviour | Detect ToS violations, ban accounts |
| **Supply-chain attacker** | Compromise a dependency | Exfiltrate secrets or frames |
| **Passive network observer** | Sniff unencrypted traffic | Intercept stream key in transit |

---

## 4. Trust Boundaries

```
┌─────────────────────────────────────────────────────────┐
│  Creator's machine (trusted zone)                       │
│                                                         │
│  liveguard CLI ──► M1 ──► M2(opt) ──► M3 ──► M4 ──►   │
│                                              OBS Studio │
│                                                         │
│  Secrets Vault (age-encrypted, chmod 600)               │
│  Audit log (append-only, HMAC, chmod 600)               │
└───────────────────────────┬─────────────────────────────┘
                            │ RTMP (TLS)
                   ┌────────▼────────┐
                   │  TikTok Ingest  │  ← untrusted / platform
                   └─────────────────┘
                            │
                   ┌────────▼────────┐
                   │  Geo Probe URL  │  ← read-only HTTP, no creds
                   └─────────────────┘
```

**Boundaries:**
1. **Filesystem boundary** — age vault at rest, identity file chmod 600.
2. **Process boundary** — M3 browser subprocess; cookies never written to disk.
3. **Network boundary** — RTMP outbound (TikTok), loopback (OBS WS, dashboard).
4. **User boundary** — stream key entered at runtime prompt only, not stored.

---

## 5. Threat Scenarios & Mitigations

### 5.1 Stream Key Exfiltration

| | Detail |
|---|---|
| **Threat** | Malicious process reads stream key from env, process args, or log file |
| **Mitigation** | Key accepted only via interactive prompt or piped stdin; never written to disk, env, or logs; redacted as `***` in all log output; vault (Phase 5+) encrypts all other secrets |

### 5.2 Source Video Leakage

| | Detail |
|---|---|
| **Threat** | Raw footage (with faces/location data) is read by another process before M1 runs |
| **Mitigation** | Temporary files written to `working_dir` with chmod 600; M1 overwrites intermediates before deletion; no telemetry — frames never leave the machine |

### 5.3 Session Cookie Theft

| | Detail |
|---|---|
| **Threat** | M3 browser session leaks cookies to disk or network |
| **Mitigation** | Playwright uses ephemeral browser context (no persistent profile); cookies never serialised to disk; M3 never logs cookie values; subprocess terminates immediately after probe |

### 5.4 Secrets Vault Compromise

| | Detail |
|---|---|
| **Threat** | Attacker reads `vault.age` and decrypts secrets |
| **Mitigation** | vault.age encrypted with age (X25519); identity file stored at `~/.config/liveguard/identity.age` chmod 600; vault file chmod 600; never committed to git (`.gitignore`) |

### 5.5 Audit Log Tampering

| | Detail |
|---|---|
| **Threat** | Post-hoc deletion or modification of audit entries to hide activity |
| **Mitigation** | Each JSONL entry contains a SHA-256 HMAC over the previous entry's hash (hash chain); `liveguard audit verify` detects any gap or modification; log file chmod 600, written by append-only open |

### 5.6 C2PA Provenance Stripping Without Consent

| | Detail |
|---|---|
| **Threat** | Tool silently removes C2PA provenance, allowing content to be misrepresented |
| **Mitigation** | C2PA stripping disabled by default; requires explicit `--strip-provenance` flag **and** interactive acknowledgement; action recorded in audit log |

### 5.7 Disclosure Banner Bypass

| | Detail |
|---|---|
| **Threat** | Creator disables the disclosure overlay to stream without transparency |
| **Mitigation** | M4 checks `compliance.disclosure_banner.enabled` before starting stream; exits with code 3 if disabled; check cannot be bypassed via config (schema `enabled: bool = True` default) |

### 5.8 Stale ToS Acknowledgement

| | Detail |
|---|---|
| **Threat** | Platform ToS changes after acknowledgement; creator streams against outdated terms |
| **Mitigation** | M4 checks `tos_ack_at`; exits code 4 if older than 30 days; `tos_ack_hash` binds acknowledgement to specific ToS document |

### 5.9 Supply-Chain Attack

| | Detail |
|---|---|
| **Threat** | A compromised dependency exfiltrates secrets or frames |
| **Mitigation** | All Python deps pinned to exact minor versions in `pyproject.toml`; `pip-audit` run in CI; Node deps pinned similarly; Dockerfile uses non-root user; no outbound connections except RTMP and geo probe |

### 5.10 Residential Proxy Misuse

| | Detail |
|---|---|
| **Threat** | Proxy used to rotate IPs for ban evasion or multi-account isolation |
| **Mitigation** | M3 `proxy_url` accepts only a single static proxy URL; rotation logic is explicitly absent; no multi-account context switching |

---

## 6. Non-Threats (Out of Scope)

* Physical access by an attacker (outside local threat model).
* Kernel-level exploits or hardware vulnerabilities.
* TikTok platform-side vulnerabilities.
* Network-level MitM on RTMP (mitigated by TikTok's TLS, not LiveGuard's scope).

---

## 7. Docker Security Posture

* Base image: `python:3.11.9-slim-bookworm` (minimal attack surface).
* Runs as non-root user `liveguard` (UID 1000).
* No capabilities granted; read-only root filesystem where possible.
* Secrets vault mounted read-only via volume; not baked into image.
* No `LIVEGUARD_STREAM_KEY` in Dockerfile or docker-compose defaults.

---

## 8. Residual Risks

| Risk | Likelihood | Impact | Accepted? |
|---|---|---|---|
| Identity file stolen from disk | Low | Critical | No — mitigated by chmod 600 + docs |
| Dependency zero-day before `pip-audit` runs | Low | High | Yes — standard open-source risk |
| Creator manually disables audit log | Low | Medium | Yes — creator controls own machine |
