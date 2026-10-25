r"""Pipeline orchestrator for LiveGuard.

Coordinates execution across modules:
  Input -> M1 (Privacy Normalizer)
        -> M2 (Robustness Tester, research-only)
        -> M3 (Geo-Test & Session Manager)
        -> M4 (OBS Orchestrator & Live Stream Monitor)

Enforces compliance gates, EULA agreement, HMAC audit logging,
and graceful shutdown on SIGINT/SIGTERM.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from liveguard.audit.chain import get_audit_chain
from liveguard.config.schema import LiveGuardConfig
from liveguard.errors import (
    StaleAckError,
    UsageError,
)
from liveguard.eula import enforce_eula
from liveguard.modules.m1.normalizer import M1Normalizer
from liveguard.modules.m2.tester import M2RobustnessTester
from liveguard.modules.m3.session import M3GeoSessionManager
from liveguard.modules.m4.orchestrator import M4OBSOrchestrator
from liveguard.orchestrator.state import RuntimeState, get_runtime_state
from liveguard.secrets.vault import get_vault

logger = logging.getLogger("liveguard.orchestrator")


def _safe_asdict(obj: Any) -> dict[str, Any]:
    if is_dataclass(obj) and not isinstance(obj, type):
        try:
            return asdict(obj)
        except Exception:
            pass
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        try:
            res = obj.to_dict()
            if isinstance(res, dict):
                return res
        except Exception:
            pass
    if isinstance(obj, dict):
        return obj
    return {"raw": str(obj)}


class OrchestratorRunner:
    """Manages the lifecycle and phased execution of the LiveGuard pipeline."""

    def __init__(
        self,
        config: LiveGuardConfig,
        state: RuntimeState | None = None,
        research_only: bool = False,
        ack_research_use: bool = False,
        strip_provenance: bool = False,
        ack_legal_risk: bool = False,
        interactive_eula: bool = False,
        stream_key: str | None = None,
    ) -> None:
        """Initialize the pipeline runner with configuration and switches."""
        self.cfg = config
        self.state = state or get_runtime_state()
        self.research_only = research_only
        self.ack_research_use = ack_research_use
        self.strip_provenance = strip_provenance
        self.ack_legal_risk = ack_legal_risk
        self.interactive_eula = interactive_eula
        self.stream_key = stream_key

        self._m4_instance: M4OBSOrchestrator | None = None
        self._stop_event = asyncio.Event()

    def _check_tos_ack_freshness(self) -> None:
        """Validate that ToS acknowledgement is present and not older than 30 days.

        Raises:
            StaleAckError: If ToS ack is missing or expired.
        """
        ack_at = self.cfg.compliance.tos_ack_at
        if ack_at is None:
            raise StaleAckError(
                "ToS acknowledgement missing. Acknowledge ToS within 30 days."
            )

        if ack_at.tzinfo is None:
            ack_at = ack_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        age_seconds = (now - ack_at).total_seconds()
        age_days = age_seconds / 86400.0

        if age_days > 30.0:
            raise StaleAckError(
                f"ToS acknowledgement expired ({age_days:.1f} days > 30 days). "
                "Re-acknowledgement required."
            )

    def _setup_signal_handlers(self) -> None:
        """Register SIGINT and SIGTERM handlers for graceful shutdown."""

        def _handle_signal(signum: int, frame: Any) -> None:
            logger.info("Signal %s received; initiating graceful stop...", signum)
            self._stop_event.set()
            if self._m4_instance is not None:
                # Schedule stop_stream
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self._m4_instance.stop_stream())
                except Exception:
                    pass
            self.state.update_phase("stopped", pipeline_active=False)
            self.state.save_snapshot()
            sys.exit(0)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handle_signal)
            except (ValueError, AttributeError):
                # Signals only work in main thread
                pass

    async def run(self) -> RuntimeState:
        """Execute the end-to-end pipeline.

        Returns:
            Updated RuntimeState upon completion.
        """
        self._setup_signal_handlers()

        # Step 1: EULA Gate
        enforce_eula(interactive=self.interactive_eula)

        audit = get_audit_chain()
        self.state.pipeline_active = True
        self.state.started_at = time.time()
        self.state.save_snapshot()

        audit.append(
            event="pipeline_start",
            module="orchestrator",
            payload={"version": self.cfg.version},
        )

        current_video = self.cfg.pipeline.input_path

        # Step 2: ToS Check if M4 is enabled
        if self.cfg.modules.m4.enabled:
            self._check_tos_ack_freshness()

        # Step 3: Run enabled modules in order
        try:
            # --- M1: Privacy Normalizer ---
            if self.cfg.modules.m1.enabled:
                if not current_video or not Path(current_video).is_file():
                    raise UsageError(
                        f"M1 enabled but input video '{current_video}' not found."
                    )

                audit.append("phase_start", "orchestrator", {"phase": "m1_normalizer"})
                self.state.update_phase("m1_normalizer")
                self.state.save_snapshot()

                out_dir = Path(self.cfg.pipeline.output_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                clean_path = out_dir / f"m1_{Path(current_video).name}"

                normalizer = M1Normalizer(self.cfg.modules.m1)
                report = await normalizer.process(
                    input=Path(current_video),
                    output=clean_path,
                    strip_provenance=self.strip_provenance
                    or self.cfg.modules.m1.strip_provenance,
                    ack_legal_risk=self.ack_legal_risk,
                )
                self.state.m1_status = _safe_asdict(report)
                audit.append(
                    "phase_end",
                    "orchestrator",
                    {"phase": "m1_normalizer", "output": str(clean_path)},
                )
                self.state.save_snapshot()
                current_video = str(clean_path)

            # --- M2: Robustness Tester ---
            if self.cfg.modules.m2.enabled:
                if not (self.research_only and self.ack_research_use):
                    raise UsageError(
                        "M2 Robustness Tester requires research acknowledgement: "
                        "pass both --research-only and --i-understand-research-use."
                    )

                audit.append("phase_start", "orchestrator", {"phase": "m2_tester"})
                self.state.update_phase("m2_tester")
                self.state.save_snapshot()

                out_dir = Path(self.cfg.pipeline.output_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                m2_out = out_dir / f"m2_perturbed_{Path(current_video).name}"

                tester = M2RobustnessTester(self.cfg.modules.m2)
                m2_report = await tester.test(
                    input=Path(current_video),
                    output=m2_out,
                    research_only=self.research_only,
                    ack_research_use=self.ack_research_use,
                )
                self.state.m2_status = _safe_asdict(m2_report)

                audit.append(
                    "phase_end",
                    "orchestrator",
                    {"phase": "m2_tester", "output": str(m2_out)},
                )
                self.state.save_snapshot()
                current_video = str(m2_out)

            # --- M3: Geo-Test & Session Manager ---
            if self.cfg.modules.m3.enabled:
                audit.append("phase_start", "orchestrator", {"phase": "m3_geo"})
                self.state.update_phase("m3_geo")
                self.state.save_snapshot()

                m3_mgr = M3GeoSessionManager(config=self.cfg.modules.m3)
                m3_report = await m3_mgr.check(profile_id="default")
                self.state.m3_status = {
                    "region": m3_report.region_probed,
                    "geo_result": m3_report.geo_result.name,
                    "session_valid": m3_report.session_valid,
                    "latency_ms": m3_report.latency_ms,
                }
                audit.append(
                    "phase_end",
                    "orchestrator",
                    {"phase": "m3_geo", "geo_result": m3_report.geo_result.name},
                )
                self.state.save_snapshot()

            # --- M4: OBS Orchestrator ---
            if self.cfg.modules.m4.enabled:
                audit.append("phase_start", "orchestrator", {"phase": "m4_obs"})
                self.state.update_phase("m4_obs")
                self.state.save_snapshot()

                self._m4_instance = M4OBSOrchestrator(
                    config=self.cfg.modules.m4,
                    compliance_config=self.cfg.compliance,
                    video_source_path=current_video,
                )

                vault = get_vault()
                effective_key = (
                    self.stream_key or vault.get("stream_key") or "live_test_stream_key"
                )
                status = await self._m4_instance.start_stream(stream_key=effective_key)
                self.state.stream_active = True
                self.state.bitrate_kbps = status.bitrate_kbps
                self.state.dropped_frames = status.dropped_frames
                self.state.save_snapshot()

                try:
                    updated = await self._m4_instance.get_status()
                    self.state.bitrate_kbps = updated.bitrate_kbps
                    self.state.dropped_frames = updated.dropped_frames
                    self.state.save_snapshot()
                finally:
                    await self._m4_instance.stop_stream()
                    self.state.stream_active = False
                    audit.append("phase_end", "orchestrator", {"phase": "m4_obs"})
                    self.state.save_snapshot()

            # Finalize
            self.state.update_phase("finished", pipeline_active=False)
            self.state.save_snapshot()
            audit.append(
                "pipeline_complete",
                "orchestrator",
                {"status": "success"},
            )
            return self.state

        except Exception as exc:
            self.state.record_error(str(exc))
            self.state.update_phase("error", pipeline_active=False)
            self.state.save_snapshot()
            audit.append(
                "pipeline_error",
                "orchestrator",
                {"error": str(exc)},
            )
            raise


def run_pipeline(
    cfg: LiveGuardConfig,
    research_only: bool = False,
    ack_research_use: bool = False,
    strip_provenance: bool = False,
    ack_legal_risk: bool = False,
    interactive_eula: bool = False,
    stream_key: str | None = None,
) -> RuntimeState:
    """Convenience synchronous entry point to execute the pipeline.

    Args:
        cfg: Validated LiveGuardConfig instance.
        research_only: Whether M2 research mode is enabled.
        ack_research_use: Acknowledgement for M2 research mode.
        strip_provenance: Whether to strip C2PA in M1.
        ack_legal_risk: Legal acknowledgement for C2PA stripping.
        interactive_eula: Whether EULA prompt can be interactive.
        stream_key: Optional stream key override for M4.

    Returns:
        RuntimeState after pipeline completion.
    """
    runner = OrchestratorRunner(
        config=cfg,
        research_only=research_only,
        ack_research_use=ack_research_use,
        strip_provenance=strip_provenance,
        ack_legal_risk=ack_legal_risk,
        interactive_eula=interactive_eula,
        stream_key=stream_key,
    )
    return asyncio.run(runner.run())
