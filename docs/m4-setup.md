# M4 OBS Studio Setup Guide

This guide details how to configure OBS Studio and `obs-websocket v5` for automation with LiveGuard M4.

---

## 1. Prerequisites

- **OBS Studio**: Version 28.0 or newer (includes built-in `obs-websocket v5`).
- **Python**: 3.10+ with `obsws-python` installed (included in `liveguard` dependencies).

---

## 2. Enable obs-websocket v5 in OBS Studio

1. Launch **OBS Studio**.
2. From the top menu bar, select:
   - **Tools** → **WebSocket Server Settings**
3. Check **Enable WebSocket server**.
4. Configure the server parameters:
   - **Server Port**: `4455` (default)
   - **Enable Authentication**: Checked (strongly recommended)
   - **Server Password**: Set a strong, unique password (e.g., `SecureObsPass2026!`)
5. Click **Apply** and then **OK**.

---

## 3. Configure Credentials in LiveGuard

LiveGuard M4 retrieves the OBS WebSocket password from the environment or SecretVault:

```bash
# Export the OBS WebSocket password in your shell
export OBS_WS_PASSWORD="SecureObsPass2026!"
```

In `liveguard.yaml`, ensure the host and port match your OBS Studio settings:

```yaml
version: 1

compliance:
  disclosure_banner:
    enabled: true
    text: "[LIVEGUARD] AI-Enhanced Stream (Synthetic Pipeline)"
  tos_ack_hash: "..."
  tos_ack_at: "2026-10-24T00:00:00Z"

modules:
  m4:
    enabled: true
    obs_host: "localhost"
    obs_port: 4455
    rtmp_url: "rtmp://live.example.com/app"
    scenes:
      - "Scene_Main"
      - "Scene_Secondary"
```

> **Note**: LiveGuard validates that `scenes` contains at least **2** scenes for automated scene rotation.

---

## 4. Verify Connection

You can verify connection to OBS Studio using either `obs-cli` or LiveGuard's CLI:

### Option A: Using `obs-cli`
```bash
# Install obs-cli if available
brew install obs-cli

# Test connection
obs-cli -p 4455 -w "$OBS_WS_PASSWORD" version
```

### Option B: Using LiveGuard CLI
```bash
liveguard obs start --config liveguard.yaml --stream-key "test_key_xyz"
```

If OBS Studio is running and credentials match, LiveGuard will automatically:
1. Verify compliance gates (banner enabled, ToS acknowledgement < 30 days old).
2. Establish the WebSocket connection.
3. Automatically provision missing standard sources:
   - `MainVideo`: Loopable media source.
   - `OverlayTimer`: Inline HTML digital clock (1s refresh).
   - `LiveBadge`: Blinking 'LIVE' badge (~600ms pulse).
   - `MusicBed`: Background music playlist source.
   - `AIDisclosure`: Persistent compliance banner.
4. Configure the custom RTMP endpoint with the user-provided stream key.
