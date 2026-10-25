r"""HTTP health endpoint exposition for LiveGuard.

Serves :8080/healthz returning JSON with per-module status and compliance indicators:
  - 200 OK if all modules are healthy/idle.
  - 503 Service Unavailable if any module is in an error state.
  - Includes `compliance`: {banner_enabled, tos_ack_age_days}.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from liveguard.config.loader import load_config
from liveguard.config.schema import LiveGuardConfig
from liveguard.orchestrator.state import RuntimeState, get_runtime_state

logger = logging.getLogger("liveguard.health")

DEFAULT_AUDIT_LOG_PATH = Path("audit/events.jsonl")


def get_last_audit_timestamp(audit_path: Path | None = None) -> str | None:
    """Retrieve timestamp of the latest entry in the audit log.

    Args:
        audit_path: Path to audit log file. Defaults to `audit/events.jsonl`.

    Returns:
        ISO timestamp string if found, otherwise None.
    """
    path = audit_path or DEFAULT_AUDIT_LOG_PATH
    if not path.is_file():
        return None

    try:
        # Read last non-empty line
        raw_lines = path.read_text(encoding="utf-8").splitlines()
        lines = [line.strip() for line in raw_lines if line.strip()]
        if not lines:
            return None
        last_entry = json.loads(lines[-1])
        ts = last_entry.get("ts")
        return str(ts) if ts is not None else None
    except Exception as err:
        logger.debug("Failed to read last audit timestamp: %s", err)
        return None


def calculate_tos_age_days(tos_ack_at: datetime | None) -> float | None:
    """Calculate age in decimal days from UTC timestamp.

    Args:
        tos_ack_at: Datetime when ToS was acknowledged.

    Returns:
        Age in days, or None if tos_ack_at was None.
    """
    if tos_ack_at is None:
        return None
    if tos_ack_at.tzinfo is None:
        tos_ack_at = tos_ack_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff_sec = (now - tos_ack_at).total_seconds()
    return max(0.0, round(diff_sec / 86400.0, 2))


def generate_health_report(
    state: RuntimeState | None = None,
    cfg: LiveGuardConfig | None = None,
    audit_path: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """Produce structured health report and corresponding HTTP status code.

    Args:
        state: RuntimeState instance. If None, loaded from disk snapshot or global.
        cfg: LiveGuardConfig instance. If None, loaded from `liveguard.yaml`.
        audit_path: Path to audit log.

    Returns:
        Tuple of (http_status_code, response_dict).
    """
    # Resolve state
    if state is None:
        state = RuntimeState.load_snapshot()
        if not state.pipeline_active and state.active_phase == "idle":
            state = get_runtime_state()

    # Resolve config
    if cfg is None:
        try:
            cfg_path = Path("liveguard.yaml")
            if cfg_path.is_file():
                cfg = load_config(cfg_path)
        except Exception as err:
            logger.debug("Config load skipped for health report: %s", err)

    # Determine module statuses
    modules: dict[str, str] = {}
    has_module_error = False

    for mod in ("m1", "m2", "m3", "m4"):
        mod_dict: dict[str, Any] = getattr(state, f"{mod}_status", {})
        status_val = mod_dict.get("status", "idle")
        if isinstance(status_val, str) and status_val.lower() in ("error", "failed"):
            modules[mod] = "error"
            has_module_error = True
        elif mod in state.active_phase and state.pipeline_active:
            modules[mod] = "running"
        else:
            modules[mod] = str(status_val) if status_val else "idle"

    # Any recent fatal errors recorded in state
    if len(state.errors) > 0 and state.active_phase == "error":
        has_module_error = True

    # Last audit event timestamp
    last_audit_ts = get_last_audit_timestamp(audit_path)

    # Compliance indicators
    banner_enabled = True
    tos_ack_age_days: float | None = None
    if cfg is not None:
        banner_enabled = bool(cfg.compliance.disclosure_banner.enabled)
        tos_ack_age_days = calculate_tos_age_days(cfg.compliance.tos_ack_at)

    overall_status = "error" if has_module_error else "ok"
    http_code = 503 if has_module_error else 200

    report = {
        "status": overall_status,
        "pipeline_active": state.pipeline_active,
        "active_phase": state.active_phase,
        "uptime_seconds": (
            round(time.time() - state.started_at, 2) if state.started_at else 0.0
        ),
        "modules": modules,
        "last_audit_event_ts": last_audit_ts,
        "compliance": {
            "banner_enabled": banner_enabled,
            "tos_ack_age_days": tos_ack_age_days,
        },
        "errors": state.errors[-5:] if state.errors else [],
    }

    return http_code, report


# ---------------------------------------------------------------------------
# HTTP Server Handler
# ---------------------------------------------------------------------------


class HealthRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for /healthz and /health endpoints."""

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr request logging."""
        pass

    def do_GET(self) -> None:
        """Handle GET requests for health status."""
        if self.path in ("/healthz", "/health", "/"):
            status_code, body = generate_health_report()
            payload = json.dumps(body, indent=2).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error": "Not Found"}\n')


class HealthServer:
    """Wrapper managing the lifecycle of the HTTP health server."""

    def __init__(self, port: int = 8080, host: str = "0.0.0.0") -> None:
        """Initialize health server binding configuration.

        Args:
            port: HTTP port to listen on (default 8080).
            host: Interface address to bind to (default '0.0.0.0').
        """
        self.port = port
        self.host = host
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Bind port and start server in background daemon thread."""
        if self._server is not None:
            return
        self._server = ThreadingHTTPServer((self.host, self.port), HealthRequestHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="liveguard-healthz",
        )
        self._thread.start()
        logger.info(
            "Health endpoint server started",
            extra={"host": self.host, "port": self.port},
        )

    def stop(self) -> None:
        """Shutdown and release listening port."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("Health endpoint server stopped")


_HEALTH_SERVER_INSTANCE: HealthServer | None = None


def start_health_server(port: int = 8080, host: str = "0.0.0.0") -> HealthServer:
    """Start global health server instance if not already running.

    Args:
        port: Listening port (default 8080).
        host: Address to bind to (default '0.0.0.0').

    Returns:
        Active HealthServer instance.
    """
    global _HEALTH_SERVER_INSTANCE
    if _HEALTH_SERVER_INSTANCE is None:
        _HEALTH_SERVER_INSTANCE = HealthServer(port=port, host=host)
        _HEALTH_SERVER_INSTANCE.start()
    return _HEALTH_SERVER_INSTANCE
