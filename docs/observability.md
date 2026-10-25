# LiveGuard Observability & Metrics

LiveGuard provides a complete local-only observability stack designed for creators and operators to monitor pipeline health, compliance states, RTMP streaming bitrate, and module execution.

## Ports & Endpoints Overview

| Port | Protocol | Path | Service | Description |
|------|----------|------|---------|-------------|
| **8501** | HTTP | `/` | Streamlit Dashboard | Visual operations control panel, compliance banner status, OBS sparkline, and manual stream key management. |
| **8080** | HTTP | `/healthz` | Health Endpoint | JSON health check returning module states, last audit event timestamp, and ToS compliance age. |
| **9090** | HTTP | `/metrics` | Prometheus Exposition | Standard Prometheus scraping endpoint exposing runtime counters and gauges. |

---

## 1. Prometheus Metrics (:9090/metrics)

Prometheus metrics are collected in a background daemon thread and exposed over HTTP.

### Exposed Metrics

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `lg_frames_processed_total` | Counter | `module` | Cumulative number of video frames processed by module (e.g., `m1_privacy`, `m2_robustness`). |
| `lg_stream_bitrate_kbps` | Gauge | none | Current RTMP output bitrate in kbps, polled from OBS Studio. |
| `lg_proxy_health` | Gauge | `proxy_id` | Health status of tested proxies (1 = reachable & valid, 0 = unhealthy). |
| `lg_audit_write_failures_total` | Counter | none | Cumulative failures writing to the HMAC-chained audit log. |
| `lg_module_status` | Gauge | `module` | Operational status of each pipeline module: `0` = Idle/OK, `1` = Running, `2` = Error. |

### Prometheus Scrape Configuration (`prometheus.yml`)

Add the following scrape job to your Prometheus server configuration:

```yaml
scrape_configs:
  - job_name: "liveguard"
    scrape_interval: 2s
    static_configs:
      - targets: ["localhost:9090"]
        labels:
          app: "liveguard"
          environment: "local"
```

### Grafana Dashboard Integration

Grafana dashboards can visualize:
- **Bitrate vs Target**: Gauge and Time Series panel tracking `lg_stream_bitrate_kbps`.
- **Pipeline Activity**: Single-stat status panels querying `lg_module_status`.
- **Audit Integrity**: Alerting rule triggered when `rate(lg_audit_write_failures_total[1m]) > 0`.

---

## 2. Health Endpoint (:8080/healthz)

The health server returns JSON payload indicating operational readiness and compliance.

### HTTP Response Codes

- **`200 OK`**: All modules are healthy or idle, and no unhandled pipeline errors exist.
- **`503 Service Unavailable`**: At least one module is in an `error` state or the pipeline encountered an unhandled exception.

### Example Response Payload

```json
{
  "status": "ok",
  "pipeline_active": true,
  "active_phase": "m4_obs",
  "uptime_seconds": 342.5,
  "modules": {
    "m1": "ok",
    "m2": "idle",
    "m3": "ok",
    "m4": "running"
  },
  "last_audit_event_ts": "2026-10-25T08:00:15.123456Z",
  "compliance": {
    "banner_enabled": true,
    "tos_ack_age_days": 14.2
  },
  "errors": []
}
```

---

## 3. Streamlit Operations Dashboard (:8501)

The Streamlit dashboard runs locally to provide interactive oversight without cloud reliance:
- **Top Bar**: Real-time pipeline uptime, config path, AI disclosure banner status, and ToS acknowledgement age in days.
  - If ToS acknowledgement age exceeds **25 days**, a prominent warning appears.
  - If ToS acknowledgement age exceeds **30 days**, a red error alert blocks streaming.
- **Critical Refusal Banner**: When the AI disclosure banner is turned off in `liveguard.yaml`, an un-dismissible **"REFUSE TO STREAM"** alert is displayed.
- **OBS Real-time Monitor**: Active OBS scene name, live status, dropped frame count, FPS, and an interactive bitrate sparkline.
- **Manual Stream Key Vault Entry**:
  - Masked password input field.
  - Saves directly to the local age vault under key `tiktok_stream_key`.
  - The secret key is **never displayed** after saving, **never logged**, and **never included in audit payloads**.
  - Appends audit event `stream_key_set` with payload `{"set": true}` only.

### Running Headless Metrics & Health Servers

To run the metrics and health servers without launching the graphical dashboard:

```bash
liveguard serve-metrics --metrics-port 9090 --health-port 8080
```

To run the Streamlit dashboard:

```bash
liveguard dashboard --port 8501 --host 0.0.0.0
```
