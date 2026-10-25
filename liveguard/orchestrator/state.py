r"""Shared runtime state management for the LiveGuard pipeline and dashboard.

Maintains in-memory state and periodically synchronizes JSON snapshots
to `runtime/state.json` for consumption by the dashboard.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_STATE_PATH = Path("runtime/state.json")


@dataclass
class RuntimeState:
    """In-memory and persisted runtime state of the LiveGuard orchestrator."""

    pipeline_active: bool = False
    active_phase: str = "idle"
    started_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    m1_status: dict[str, Any] = field(default_factory=dict)
    m2_status: dict[str, Any] = field(default_factory=dict)
    m3_status: dict[str, Any] = field(default_factory=dict)
    m4_status: dict[str, Any] = field(default_factory=dict)
    stream_active: bool = False
    bitrate_kbps: float = 0.0
    bitrate_history: list[float] = field(default_factory=list)
    fps: float = 0.0
    dropped_frames: int = 0
    total_frames: int = 0
    scene_name: str = "Main"
    errors: list[str] = field(default_factory=list)

    def record_bitrate(self, kbps: float) -> None:
        """Update current bitrate and append to historical series.

        Args:
            kbps: Measured bitrate in kilobits per second.
        """
        self.bitrate_kbps = float(kbps)
        self.bitrate_history.append(float(kbps))
        if len(self.bitrate_history) > 60:
            self.bitrate_history = self.bitrate_history[-60:]
        self.updated_at = time.time()

    def update_phase(self, phase: str, **extra: Any) -> None:
        """Update current active phase and timestamp.

        Args:
            phase: Phase identifier string (e.g. 'm1_privacy', 'm4_obs').
            **extra: Additional state key-values to set.
        """
        self.active_phase = phase
        self.updated_at = time.time()
        for k, v in extra.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def record_error(self, message: str) -> None:
        """Record an error message to the state history.

        Args:
            message: Error description string.
        """
        self.errors.append(f"[{time.strftime('%X')}] {message}")
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Convert state to a plain serializable dictionary.

        Returns:
            Dictionary representation of the state.
        """
        return asdict(self)

    def save_snapshot(self, path: Path | None = None) -> None:
        """Atomically persist runtime state snapshot to JSON file.

        Args:
            path: Target file path. Defaults to `runtime/state.json`.
        """
        target = path or DEFAULT_STATE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)

        data = self.to_dict()
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=target.parent,
            prefix=".state_tmp_",
            suffix=".json",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, target)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    @classmethod
    def load_snapshot(cls, path: Path | None = None) -> RuntimeState:
        """Load state snapshot from JSON file if available.

        Args:
            path: Source file path. Defaults to `runtime/state.json`.

        Returns:
            Populated RuntimeState instance or blank state if missing/corrupt.
        """
        target = path or DEFAULT_STATE_PATH
        if not target.is_file():
            return cls()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            return cls(
                pipeline_active=raw.get("pipeline_active", False),
                active_phase=raw.get("active_phase", "idle"),
                started_at=raw.get("started_at"),
                updated_at=raw.get("updated_at", time.time()),
                m1_status=raw.get("m1_status", {}),
                m2_status=raw.get("m2_status", {}),
                m3_status=raw.get("m3_status", {}),
                m4_status=raw.get("m4_status", {}),
                stream_active=raw.get("stream_active", False),
                bitrate_kbps=float(raw.get("bitrate_kbps", 0.0)),
                bitrate_history=[float(x) for x in raw.get("bitrate_history", [])],
                fps=float(raw.get("fps", 0.0)),
                dropped_frames=int(raw.get("dropped_frames", 0)),
                total_frames=int(raw.get("total_frames", 0)),
                scene_name=str(raw.get("scene_name", "Main")),
                errors=raw.get("errors", []),
            )
        except Exception:
            return cls()


_GLOBAL_RUNTIME_STATE: RuntimeState | None = None


def get_runtime_state() -> RuntimeState:
    """Retrieve the global RuntimeState instance.

    Returns:
        Shared RuntimeState singleton.
    """
    global _GLOBAL_RUNTIME_STATE
    if _GLOBAL_RUNTIME_STATE is None:
        _GLOBAL_RUNTIME_STATE = RuntimeState()
    return _GLOBAL_RUNTIME_STATE
