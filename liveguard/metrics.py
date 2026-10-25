r"""Prometheus metrics exposition for LiveGuard.

Exposes metrics at :9090/metrics in a daemon thread:
  lg_frames_processed_total{module}   Counter
  lg_stream_bitrate_kbps              Gauge (updated from M4 poll)
  lg_proxy_health{proxy_id}           Gauge (1/0)
  lg_audit_write_failures_total       Counter
  lg_module_status{module}            Gauge (0 idle, 1 running, 2 error)
"""

from __future__ import annotations

import logging
from typing import Any

from prometheus_client import REGISTRY, Counter, Gauge, start_http_server

from liveguard.orchestrator.state import RuntimeState

logger = logging.getLogger("liveguard.metrics")

# ---------------------------------------------------------------------------
# Metric Registry Helpers (Idempotent for safe re-importing)
# ---------------------------------------------------------------------------


def _get_or_create_counter(
    name: str,
    documentation: str,
    labelnames: tuple[str, ...] = (),
) -> Counter:
    """Retrieve existing Counter from REGISTRY or create and register a new one.

    Args:
        name: Metric name.
        documentation: Help string.
        labelnames: Tuple of dimension label names.

    Returns:
        Counter instance.
    """
    if name in REGISTRY._names_to_collectors:
        collector = REGISTRY._names_to_collectors[name]
        if isinstance(collector, Counter):
            return collector
    # In prometheus_client, counter names also register with `_total` suffix
    alt_name = name[:-6] if name.endswith("_total") else f"{name}_total"
    if alt_name in REGISTRY._names_to_collectors:
        collector = REGISTRY._names_to_collectors[alt_name]
        if isinstance(collector, Counter):
            return collector

    return Counter(name, documentation, labelnames=labelnames, registry=REGISTRY)


def _get_or_create_gauge(
    name: str,
    documentation: str,
    labelnames: tuple[str, ...] = (),
) -> Gauge:
    """Retrieve existing Gauge from REGISTRY or create and register a new one.

    Args:
        name: Metric name.
        documentation: Help string.
        labelnames: Tuple of dimension label names.

    Returns:
        Gauge instance.
    """
    if name in REGISTRY._names_to_collectors:
        collector = REGISTRY._names_to_collectors[name]
        if isinstance(collector, Gauge):
            return collector
    return Gauge(name, documentation, labelnames=labelnames, registry=REGISTRY)


# ---------------------------------------------------------------------------
# Prometheus Metric Instances
# ---------------------------------------------------------------------------

FRAMES_PROCESSED_TOTAL = _get_or_create_counter(
    "lg_frames_processed_total",
    "Total video frames processed by module",
    labelnames=("module",),
)

STREAM_BITRATE_KBPS = _get_or_create_gauge(
    "lg_stream_bitrate_kbps",
    "Current stream output bitrate in kbps",
)

PROXY_HEALTH = _get_or_create_gauge(
    "lg_proxy_health",
    "Proxy health status (1=healthy, 0=unhealthy)",
    labelnames=("proxy_id",),
)

AUDIT_WRITE_FAILURES_TOTAL = _get_or_create_counter(
    "lg_audit_write_failures_total",
    "Total failures writing to audit chain log",
)

MODULE_STATUS = _get_or_create_gauge(
    "lg_module_status",
    "Module status (0=idle, 1=running, 2=error)",
    labelnames=("module",),
)

# ---------------------------------------------------------------------------
# Status Mapping Helpers
# ---------------------------------------------------------------------------

STATUS_MAP: dict[str, int] = {
    "idle": 0,
    "ok": 0,
    "success": 0,
    "finished": 0,
    "running": 1,
    "active": 1,
    "in_progress": 1,
    "error": 2,
    "failed": 2,
    "stale": 2,
}


def parse_module_status_code(status: int | str) -> int:
    """Convert integer or string status into numeric 0, 1, or 2.

    Args:
        status: Integer code (0, 1, 2) or status string.

    Returns:
        0 (idle/ok), 1 (running), or 2 (error).
    """
    if isinstance(status, int):
        return status if status in (0, 1, 2) else 0
    return STATUS_MAP.get(str(status).strip().lower(), 0)


# ---------------------------------------------------------------------------
# Metric Recording API
# ---------------------------------------------------------------------------


def record_frame_processed(module: str, count: int = 1) -> None:
    """Increment frame processed counter for a specific module.

    Args:
        module: Module identifier (e.g. 'm1_privacy', 'm2_robustness').
        count: Number of frames to increment.
    """
    FRAMES_PROCESSED_TOTAL.labels(module=module).inc(count)


def update_stream_bitrate(bitrate_kbps: float) -> None:
    """Update current OBS streaming bitrate gauge.

    Args:
        bitrate_kbps: Instantaneous bitrate in kbps.
    """
    STREAM_BITRATE_KBPS.set(float(bitrate_kbps))


def update_proxy_health(proxy_id: str, is_healthy: bool) -> None:
    """Set proxy health gauge to 1 (healthy) or 0 (unhealthy).

    Args:
        proxy_id: Identifier or label of proxy.
        is_healthy: Boolean indicating reachability and functionality.
    """
    PROXY_HEALTH.labels(proxy_id=proxy_id).set(1.0 if is_healthy else 0.0)


def record_audit_write_failure(count: int = 1) -> None:
    """Increment audit log write failure counter.

    Args:
        count: Number of failure events.
    """
    AUDIT_WRITE_FAILURES_TOTAL.inc(count)


def update_module_status(module: str, status: int | str) -> None:
    """Update module status gauge (0=idle, 1=running, 2=error).

    Args:
        module: Module identifier ('m1', 'm2', 'm3', 'm4', etc.).
        status: Numeric status (0, 1, 2) or string.
    """
    code = parse_module_status_code(status)
    MODULE_STATUS.labels(module=module).set(code)


def sync_metrics_from_state(state: RuntimeState) -> None:
    """Synchronize Prometheus gauges from current RuntimeState snapshot.

    Args:
        state: Active or loaded RuntimeState.
    """
    update_stream_bitrate(state.bitrate_kbps)

    # Map phases
    active = state.active_phase
    for mod in ("m1", "m2", "m3", "m4"):
        if mod in active and state.pipeline_active:
            update_module_status(mod, 1)
        elif len(state.errors) > 0 and mod in active:
            update_module_status(mod, 2)
        else:
            mod_dict: dict[str, Any] = getattr(state, f"{mod}_status", {})
            st = mod_dict.get("status", "idle")
            update_module_status(mod, st)


# ---------------------------------------------------------------------------
# Server Lifecycle
# ---------------------------------------------------------------------------

_SERVER_STARTED: bool = False


def start_metrics_server(port: int = 9090, host: str = "0.0.0.0") -> None:
    """Start Prometheus HTTP server on the specified port in a daemon thread.

    Args:
        port: Port to listen on (default 9090).
        host: Address to bind to (default '0.0.0.0').
    """
    global _SERVER_STARTED
    if _SERVER_STARTED:
        logger.debug("Prometheus metrics server is already running")
        return

    try:
        start_http_server(port, addr=host, registry=REGISTRY)
        _SERVER_STARTED = True
        logger.info(
            "Prometheus metrics server started", extra={"port": port, "host": host}
        )
    except OSError as err:
        logger.warning(
            "Prometheus metrics server port may already be in use: %s",
            err,
        )
