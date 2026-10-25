# LiveGuard Configuration Reference (`liveguard.yaml`)

This document provides a comprehensive reference for every configuration key in `liveguard.yaml`, validated by Pydantic v2.

---

## Top-Level Schema

| Key | Type | Default | Example | Description |
|---|---|---|---|---|
| `version` | `integer` | *(Required)* | `1` | Schema version. Currently must be `1`. |
| `pipeline` | `object` | `{}` | *(See Pipeline block)* | Top-level I/O paths. |
| `modules` | `object` | `{}` | *(See Modules block)* | Configuration for modules M1–M4. |
| `environment` | `object` | `{}` | *(See Environment block)* | Vault and identity paths. |
| `logging` | `object` | `{}` | *(See Logging block)* | Output format and audit paths. |
| `dashboard` | `object` | `{}` | *(See Dashboard block)* | Local Streamlit dashboard server. |
| `compliance` | `object` | `{}` | *(See Compliance block)* | Gating, banner, and ToS ack. |

---

## 1. `pipeline`

Top-level I/O ordering and working directory paths.

| Key | Type | Default | Example | Description |
|---|---|---|---|---|
| `input_path` | `string` | `""` | `"./media/raw.mp4"` | Path to source raw video file. |
| `output_dir` | `string` | `"./output"` | `"./output"` | Directory for processed deliverables. |
| `working_dir` | `string` | `"./tmp"` | `"./tmp"` | Scratch directory for temporary frames/state. |

---

## 2. `modules.m1` (Privacy Normalizer)

Controls EXIF/GPS stripping, C2PA preservation, and neutral injection.

| Key | Type | Default | Example | Description |
|---|---|---|---|---|
| `enabled` | `boolean` | `true` | `true` | Enable/disable M1 in pipeline. |
| `strip_provenance` | `boolean` | `false` | `false` | When true, strips C2PA credentials (requires legal ack). |
| `face_confidence_threshold` | `float` [0.0–1.0] | `0.9` | `0.92` | MTCNN confidence cutoff for face redaction. |
| `redaction_method` | `string` | `"blur"` | `"blur"` / `"pixelate"` | Visual redaction algorithm. |
| `audio_normalize` | `boolean` | `true` | `true` | Apply EBU R128 loudness normalization. |
| `crf` | `integer` [0–51] | `23` | `20` | Constant Rate Factor for x264 video quality. |
| `preset` | `string` | `"medium"` | `"faster"`, `"medium"` | FFmpeg encoding speed preset. |
| `noise` | `integer` [0–8] | `4` | `4` | Subtle uniform noise injection strength (0 = disabled). |

---

## 3. `modules.m2` (Robustness Tester)

Adversarial evaluation under mandatory visible watermarking (`PERTURBED`).

| Key | Type | Default | Example | Description |
|---|---|---|---|---|
| `enabled` | `boolean` | `false` | `false` | M2 is opt-in and research-only. |
| `perturbations` | `list[str]` | `["pgd_clip"]` | `["gaussian_noise", "compression"]` | Perturbation algorithms to evaluate. |
| `watermark_opacity` | `float` [0.1–1.0] | `0.6` | `0.8` | Opacity of visible watermark text. |
| `watermark_text` | `string` | `"PERTURBED"` | `"PERTURBED"` | Mandatory text burned into every frame. |
| `cache_dir` | `string` | `"~/.cache/liveguard/models"` | `"~/.cache/liveguard/models"` | Local cache for CLIP weights. |
| `epsilon` | `float` [0.0–1.0] | `0.03137` | `0.03137` | Maximum L_inf perturbation budget ($8/255$). |
| `iters` | `integer` [1–200] | `40` | `40` | PGD attack iterations per frame. |
| `batch_size` | `integer` [1–64] | `8` | `8` | Inference batch size. |
| `min_ssim` | `float` [0.0–1.0] | `0.95` | `0.95` | SSIM quality floor before reducing epsilon. |
| `min_psnr` | `float` [0.0–100.0] | `40.0` | `40.0` | PSNR quality floor in dB. |
| `quality_retries` | `integer` [1–10] | `3` | `3` | Attempts to back off epsilon on quality drop. |
| `checkpoint_interval` | `integer` [1–1000] | `100` | `100` | Save checkpoint every N frames. |

---

## 4. `modules.m3` (Geo-Test & Session Manager)

Geographic availability checking and Playwright browser session preparation.

| Key | Type | Default | Example | Description |
|---|---|---|---|---|
| `enabled` | `boolean` | `true` | `true` | Enable/disable M3 geo testing. |
| `region` | `string` | `"US"` | `"US"`, `"SG"`, `"DE"` | Target ISO-3166-1 alpha-2 region code. |
| `proxy_url` | `string` or `null` | `null` | `"http://10.0.0.1:8080"` | Explicit proxy for availability testing. |
| `browser_timeout_ms` | `integer` (>0) | `30000` | `15000` | Browser navigation timeout in milliseconds. |

---

## 5. `modules.m4` (OBS Orchestrator)

Automated OBS WebSocket v5 scene management, stream starting, and health monitoring.

| Key | Type | Default | Example | Description |
|---|---|---|---|---|
| `enabled` | `boolean` | `true` | `true` | Enable/disable M4 in pipeline. |
| `obs_host` | `string` | `"localhost"` | `"127.0.0.1"` | OBS WebSocket server hostname. |
| `obs_port` | `integer` [1–65535] | `4455` | `4455` | OBS WebSocket port. |
| `obs_password_secret`| `string` | `"obs_ws_password"` | `"obs_ws_password"` | Vault secret key storing the password. |
| `rtmp_url` | `string` | `"rtmp://live.tiktok.com/live/"` | `"rtmp://live.tiktok.com/live/"` | Base RTMP ingest URL (no stream key). |
| `scenes` | `list[str]` (min 2) | `["Intro", "Main"]` | `["Intro", "Live"]` | Required scene names in OBS setup. |
| `sample_rate` | `integer` (>0) | `44100` | `48000` | Audio sample rate in Hz. |

---

## 6. `environment`

Local filesystem paths for encrypted secrets storage.

| Key | Type | Default | Example | Description |
|---|---|---|---|---|
| `vault_path` | `string` | `"./secrets/vault.age"` | `"./secrets/vault.age"` | Backing file for age-encrypted secrets. |
| `identity_path` | `string` | `"~/.config/liveguard/identity.age"` | `"./secrets/age.key"` | Path to the age private identity key. |

---

## 7. `logging`

Structured logging and audit log output locations.

| Key | Type | Default | Example | Description |
|---|---|---|---|---|
| `level` | `string` | `"INFO"` | `"DEBUG"`, `"INFO"`, `"WARNING"` | Minimum log verbosity level. |
| `format` | `string` | `"json"` | `"json"`, `"console"` | Format for stderr/stdout logs. |
| `audit_log_path` | `string` | `"./audit/audit.jsonl"` | `"./audit/events.jsonl"` | Filepath for HMAC-chained audit log. |

---

## 8. `dashboard`

Local real-time monitoring web dashboard.

| Key | Type | Default | Example | Description |
|---|---|---|---|---|
| `enabled` | `boolean` | `false` | `true` | Launch dashboard during pipeline run. |
| `host` | `string` | `"127.0.0.1"` | `"127.0.0.1"` | Bind host for Streamlit server. |
| `port` | `integer` [1–65535] | `8501` | `8501` | HTTP port for Streamlit server. |

---

## 9. `compliance`

Legal gating, disclosure requirements, and terms of service freshness checks.

| Key | Type | Default | Example | Description |
|---|---|---|---|---|
| `disclosure_banner.enabled` | `boolean` | `true` | `true` | Must be `true` to stream; `false` raises Exit 3. |
| `disclosure_banner.text` | `string` | `"This stream uses AI-assisted preparation tools."` | `"AI-assisted"` | Viewer banner text. |
| `disclosure_banner.position` | `string` | `"bottom-right"` | `"bottom-right"` | Position in OBS overlay. |
| `tos_ack_hash` | `string` or `null` | `null` | `"e3b0c44..."` | SHA-256 hash of agreed ToS text. |
| `tos_ack_at` | `datetime` or `null` | `null` | `"2026-10-24T00:00:00Z"` | Timestamp of ToS acknowledgement; must be $\le$ 30 days old or raises Exit 4. |

---

## Environment Variable Overrides

Any configuration value can be overridden using environment variables prefixed with `LIVEGUARD__`:
- Use double underscores (`__`) to denote nested paths.
- Examples:
  - `LIVEGUARD__OBS__HOST="192.168.1.100"` overrides `modules.m4.obs_host`.
  - `LIVEGUARD__MODULES__M1__CRF="18"` overrides `modules.m1.crf`.
  - `LIVEGUARD__COMPLIANCE__DISCLOSURE_BANNER__ENABLED="true"` overrides the banner setting.
