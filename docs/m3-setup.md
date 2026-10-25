# M3 Geo-Test & Session Manager Guide

This guide covers session preparation, browser profiling, and geo-testing with LiveGuard M3.

---

## 1. Ethical Proxy Sourcing & Policy

LiveGuard provides a proxy pool specifically for **geographic availability testing** of creator streams.

### Mandatory Compliance Rules:
- **Legitimate Testing Only**: Use proxies solely to evaluate platform ingest availability and network latency from target broadcast regions (e.g., verifying whether your stream is accessible to viewers in London or Tokyo).
- **NO Ban Evasion**: Never use residential proxies or proxy rotation to circumvent platform geographic bans or account restrictions.
- **NO Credential Scraping**: LiveGuard explicitly forbids automated scraping of stream keys or user credentials. Session management is strictly limited to preserving cookies from a one-time manual user login.
- **Explicit Labeling**: Every proxy in `proxies.json` must be assigned a descriptive label indicating its purpose and datacenter/region.

---

## 2. Browser Profiles

Browser profiles simulate a realistic browser environment for geo-testing and manual authentication without device fingerprint spoofing.

### Configuration Format (`profiles.json` or `profiles/{profile_id}.json`):

```json
{
  "id": "creator_us",
  "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
  "viewport": {
    "w": 1920,
    "h": 1080
  },
  "timezone": "America/New_York",
  "locale": "en-US",
  "proxy": {
    "url": "http://user:secret@us-east.proxy.example.com:8080",
    "type": "http",
    "label": "US-Virginia-Probe"
  },
  "createdAt": "2026-10-24T00:00:00Z",
  "allow_passive_feed_scroll": false,
  "allow_interactions": false
}
```

### Safety Flags:
- `allow_passive_feed_scroll`: Defaults to `false`. When explicitly enabled, executes 5–10 gentle scrolls with Gaussian delay (mean=800ms, std=300ms) to simulate natural browsing.
- `allow_interactions`: Defaults to `false`. Automated likes, comments, and follows are strictly forbidden and never executed.

---

## 3. Manual Login Flow

LiveGuard enforces manual one-time login for creator account authentication.

### Step-by-Step Flow:

1. **Trigger Login via CLI**:
   ```bash
   liveguard env login --profile creator_us
   ```
2. **Browser Opens**:
   A Chromium browser window opens navigating directly to TikTok's login interface.

   ```text
   +--------------------------------------------------------------+
   | [Screenshot Placeholder: TikTok Login Window]                |
   | User enters credentials and completes 2FA verification.      |
   +--------------------------------------------------------------+
   ```

3. **Complete 2FA / Authentication**:
   Log in manually using your standard creator credentials and verify any security prompts.

4. **Automatic Cookie Preservation**:
   Once logged in, the bridge detects active session cookies and exports them securely to:
   `secrets/sessions/creator_us.json`
   - File permissions are locked to `chmod 600` (read/write only by current OS user).
   - Marked with `# TODO: encrypt in Phase 5` for vault encryption.

---

## 4. Geographic Availability Check

To verify availability and round-trip latency to the platform ingest before going live:

```bash
liveguard env check --profile creator_us
```

### Example Output:
```text
Geo-test report for profile 'creator_us':
  Region probed : US
  Geo result    : AVAILABLE
  Latency       : 84.3 ms
  Session valid : True
  Probe endpoint: https://www.tiktok.com
```
