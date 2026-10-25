# M4 OBS Orchestrator Troubleshooting Guide

This guide details common failure modes in the M4 OBS Orchestrator and how to resolve them.

---

## 1. Connection Refused / Port Closed (Exit Code 5)

### Symptoms
```text
[liveguard] ERROR (exit 5): Failed to connect to OBS Studio WebSocket at localhost:4455: [Errno 61] Connection refused
```

### Causes
- OBS Studio is not running.
- The WebSocket server is disabled in OBS Studio settings.
- Port mismatch (e.g. OBS is listening on 4444 or 4456 instead of 4455).
- Local firewall blocking loopback connections.

### Resolution
1. Verify OBS Studio is running.
2. Open **Tools** → **WebSocket Server Settings** in OBS Studio.
3. Confirm that **Enable WebSocket server** is checked and the port is set to `4455`.
4. Verify port listening state with terminal tools:
   ```bash
   lsof -i :4455
   # or
   nc -zv localhost 4455
   ```

---

## 2. Authentication Failure (Exit Code 5)

### Symptoms
```text
[liveguard] ERROR (exit 5): Failed to connect to OBS Studio WebSocket: Authentication failed
```

### Causes
- The password in `OBS_WS_PASSWORD` does not match the server password configured in OBS Studio.
- Special characters in the password were unescaped in your shell environment.

### Resolution
1. In OBS Studio, open **Tools** → **WebSocket Server Settings**.
2. Click **Show Connect Info** to view the configured password.
3. Export the exact password in your terminal:
   ```bash
   export OBS_WS_PASSWORD="<exact_password_here>"
   ```

---

## 3. Compliance Gates Rejections (Exit Codes 3 and 4)

LiveGuard enforces strict compliance gating **before** initiating any network WebSocket connection.

### Case A: Disclosure Banner Disabled (Exit Code 3)
```text
[liveguard] ERROR (exit 3): Compliance gate failed: Disclosure banner must be enabled before streaming.
Audit event: m4_refused_no_disclosure
```
- **Cause**: In `liveguard.yaml`, `compliance.disclosure_banner.enabled` is set to `false`.
- **Resolution**: Under platform policies and LiveGuard safety rules, the AI disclosure banner is mandatory. Set `enabled: true`:
  ```yaml
  compliance:
    disclosure_banner:
      enabled: true
      text: "[LIVEGUARD] AI-Enhanced Stream"
  ```

### Case B: Stale Terms of Service Acknowledgement (Exit Code 4)
```text
[liveguard] ERROR (exit 4): Compliance gate failed: ToS acknowledgement is 35.2 days old (exceeds 30-day freshness limit).
Audit event: m4_refused_stale_tos
```
- **Cause**: `compliance.tos_ack_at` is missing or exceeds the 30-day freshness requirement.
- **Resolution**: Re-acknowledge platform Terms of Service:
  ```bash
  liveguard eula accept
  ```
  Or update `tos_ack_at` in `liveguard.yaml` to the current UTC timestamp after reviewing compliance terms.

---

## 4. Scene Missing in OBS Studio

### Symptoms
```text
Scene 'Scene_Main' does not exist in OBS Studio collection.
```

### Causes
- Scenes listed in `liveguard.yaml` under `modules.m4.scenes` do not match the scene names in your active OBS Studio scene collection.

### Resolution
1. Open OBS Studio and check the list in the **Scenes** dock.
2. Update `liveguard.yaml` with the exact names:
   ```yaml
   modules:
     m4:
       scenes:
         - "Camera 1"
         - "Screen Share"
   ```

---

## 5. Blank Browser Source (Clock or Live Badge)

### Symptoms
- `OverlayTimer`, `LiveBadge`, or `AIDisclosure` appear transparent or blank in OBS Studio preview.

### Causes
- OBS Studio browser source hardware acceleration conflict.
- Base64 data URI string corruption.

### Resolution
1. In OBS Studio:
   - Go to **Settings** → **Advanced** → **Sources**.
   - Toggle **Enable Browser Source Hardware Acceleration**.
   - Restart OBS Studio.
2. In the scene source properties for `OverlayTimer` or `AIDisclosure`, verify that **Shutdown source when not visible** is unchecked if you need it to run continuously in the background.

---

## 6. Circuit Breaker Open (`circuit_open`)

### Symptoms
```text
[liveguard] ERROR (exit 5): Circuit breaker is OPEN: OBS WebSocket failed 5 consecutive calls.
Audit event: circuit_open
```

### Causes
- OBS Studio crashed or became unresponsive during streaming, causing 5 consecutive status queries to fail.
- LiveGuard opened the circuit for 60 seconds to prevent resource exhaustion and log flood.

### Resolution
1. Check OBS Studio logs (`Help` → `Log Files` → `View Current Log`).
2. Verify system CPU/GPU usage is not bottlenecked.
3. After resolving the underlying crash, wait for the 60-second cooldown period, or restart the monitoring process.
