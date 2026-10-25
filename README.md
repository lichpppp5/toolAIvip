# LiveGuard

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   LEGAL DISCLAIMER                                     │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ LiveGuard is an independent creator-controlled toolchain designed for privacy          │
│ normalization, local content preparation, and compliance enforcement during live       │
│ streaming.                                                                             │
│                                                                                        │
│ 1. NOT LEGAL ADVICE: This software and documentation do not constitute legal advice.   │
│ 2. NO WARRANTY: LiveGuard is provided "AS IS", without warranty of any kind, express   │
│    or implied, including but not limited to the warranties of merchantability,         │
│    fitness for a particular purpose, or non-infringement.                              │
│ 3. CREATOR RESPONSIBILITY: The user is solely responsible for ensuring that their use  │
│    of this software, content rights, data privacy practices, and livestream broadcasts │
│    comply with all applicable laws, intellectual property rights, and platform terms   │
│    of service (including TikTok Community Guidelines and Terms of Service).            │
│ 4. NOT FOR PLATFORM EVASION: LiveGuard is explicitly NOT an evasion tool. It does not  │
│    bypass platform moderation, scrape credentials, falsify device identifiers, or     │
│    rotate residential proxies to circumvent detection.                                 │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## What is LiveGuard?

**LiveGuard** is an open-source, local-first creator toolchain for compliant livestream preparation. It automates media privacy hygiene, geo-availability verification, and OBS Studio orchestration while embedding strict technical guardrails that enforce platform compliance.

### Core Architecture

```
Raw Media ──► [ M1: Privacy Normalizer ] ──► [ M2: Robustness Tester ]
                                                    │ (Opt-in, Research Only)
                                                    ▼
TikTok RTMP ◄── [ M4: OBS Orchestrator ] ◄── [ M3: Geo-Test & Session ]
 (User Key)     (Compliance Gates: Banner + ToS)  (Zero Credential Scraping)
```

- **M1 (Privacy Normalizer)**: Strips sensitive metadata (GPS, EXIF, personal tags) via FFmpeg while **preserving C2PA content provenance** by default.
- **M2 (Robustness Tester)**: Evaluates CLIP vision-language model robustness under adversarial distribution shifts. Strictly opt-in (`--research-only`) with a **mandatory visible watermark**.
- **M3 (Geo-Test & Session Manager)**: Validates RTMP network reachability and session validity without scraping or storing platform credentials.
- **M4 (OBS Orchestrator)**: Automates OBS Studio via obs-websocket v5 with hard compliance gates that **refuse to stream** if the AI disclosure banner is disabled or ToS acknowledgement is stale.

---

## Installation

### Prerequisites
- Python 3.10 or 3.11
- [FFmpeg](https://ffmpeg.org/) $\ge$ 5.0
- [ExifTool](https://exiftool.org/) $\ge$ 12.0
- Node.js $\ge$ 20.0 (for M3 Playwright bridge)

### Option 1: pip (Local Environment)

```bash
# Clone repository
git clone https://github.com/liveguard/liveguard.git
cd liveguard

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install LiveGuard with dashboard and ML extras
pip install -e ".[dashboard,ml]"
```

### Option 2: Docker & Docker Compose

```bash
# Build and run LiveGuard with Streamlit dashboard on :8501
docker compose up -d liveguard
```

---

## Quick Start

### 1. Accept the EULA
On first run, LiveGuard requires explicit acceptance of the ethical use agreement:

```bash
liveguard eula accept
```

### 2. Harden Media (Privacy Normalization)
Normalize a video file by stripping personal metadata while preserving C2PA provenance:

```bash
liveguard harden raw_video.mp4 --output clean_video.mp4
```

### 3. Launch the Operations Dashboard
Start the local operations dashboard on `http://localhost:8501`:

```bash
liveguard dashboard
```

In the dashboard, enter your TikTok RTMP stream key into the age-encrypted vault.

### 4. Start Livestream with OBS
Launch OBS streaming with automated compliance checks:

```bash
liveguard obs start --stream-key "sk_live_..."
```

---

## Configuration Reference

LiveGuard uses a single `liveguard.yaml` configuration file. See the complete reference:

👉 **[Configuration Reference Documentation](docs/config-reference.md)**

```yaml
version: 1
pipeline:
  input_path: "./input/raw.mp4"
  output_dir: "./output"

compliance:
  disclosure_banner:
    enabled: true
    text: "AI-Enhanced Livestream"
  tos_ack_at: "2026-10-01T00:00:00Z"
```

---

## Troubleshooting

### 1. ExternalDependencyError: Required tool 'exiftool' or 'ffmpeg' not found
Ensure both FFmpeg and ExifTool are installed and available on your system `PATH`:
- **macOS**: `brew install ffmpeg exiftool`
- **Ubuntu/Debian**: `sudo apt-get install -y ffmpeg libexiftool-perl`

### 2. ComplianceError: AI Disclosure Banner is DISABLED (Exit Code 3)
LiveGuard refuses to stream if the AI disclosure banner is turned off. Set `compliance.disclosure_banner.enabled: true` in `liveguard.yaml`.

### 3. StaleAckError: ToS acknowledgement older than 30 days (Exit Code 4)
TikTok Terms of Service acknowledgement must be re-certified every 30 days. Re-run:
```bash
liveguard tos-ack
```

---

## FAQ

### Why is C2PA provenance preserved by default?
Content provenance standards prove authentic creator ownership and distinguish organic footage from synthetic media. Stripping provenance blindly misleads viewers and violates emerging content transparency norms. Provenance removal requires explicit dual flags (`--strip-provenance --i-understand-legal-risk`).

### Why is M2 (Robustness Tester) restricted to research use?
Adversarial perturbation can be abused to evade platform moderation. LiveGuard strictly forbids evasion tooling. M2 enforces dual opt-in flags, burns a visible "PERTURBED" corner watermark into every frame, and embeds XMP research tags.

### Why does LiveGuard require manual stream key entry?
Automated credential harvesting via browser scraping violates platform Terms of Service and introduces account security risks. LiveGuard never scrapes credentials. Creators enter their key manually into a local age-encrypted vault (`secrets/vault.age`).

---

## Contributing

Contributions are welcome! Please ensure all pull requests pass static typing, formatting, and the complete test suite:

```bash
# Run linting and formatting checks
ruff check liveguard/ tests/
black --check liveguard/ tests/
mypy --strict liveguard/

# Run unit and integration tests
pytest tests/unit/
LIVEGUARD_TEST_MOCK_MODEL=1 pytest tests/integration/
```

---

## License

This project is licensed under the [MIT License](LICENSE).
