"""M4 — OBS Orchestrator implementation with obs-websocket v5.

Automates OBS Studio streaming operations under strict compliance gating.
Refuses to initiate any WebSocket connection or RTMP streaming unless:
1. The AI disclosure banner is explicitly enabled (ComplianceError, exit 3).
2. The Terms of Service acknowledgement is fresh within 30 days (StaleAckError, exit 4).

Usage example::

    orch = M4OBSOrchestrator(config=m4_cfg, compliance_config=comp_cfg)
    await orch.connect()
    status = await orch.start_stream(stream_key="live_12345")
    await orch.stop_stream()
"""

from __future__ import annotations

import asyncio
import random
import signal
import time
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import structlog

from liveguard.config.schema import ComplianceConfig, M4Config
from liveguard.errors import (
    ComplianceError,
    ExternalDependencyError,
    StaleAckError,
)
from liveguard.interfaces import OBSOrchestrator, StreamState, StreamStatus
from liveguard.modules.m4.assets import (
    get_disclosure_banner_data_uri,
    get_live_badge_data_uri,
    get_timer_data_uri,
)

logger = structlog.get_logger("liveguard.m4")


class CircuitBreaker:
    """Circuit breaker for OBS WebSocket status queries.

    After ``threshold`` consecutive failures, opens the circuit for
    ``cooldown_seconds`` to protect system resources and log incident state.
    """

    def __init__(self, threshold: int = 5, cooldown_seconds: float = 60.0) -> None:
        """Initialize circuit breaker.

        Args:
            threshold: Number of consecutive failures before opening circuit.
            cooldown_seconds: Cooldown period in seconds before half-open retry.
        """
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = 0
        self.opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        """Return True if circuit is currently open and cooldown has not expired."""
        if self.opened_at is None:
            return False
        if time.time() - self.opened_at < self.cooldown_seconds:
            return True
        # Cooldown expired: half-open state
        return False

    def record_success(self) -> None:
        """Reset failure counter on successful request."""
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self) -> bool:
        """Record a failure and open circuit if threshold is reached.

        Returns:
            True if the circuit was just opened by this failure.
        """
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold and self.opened_at is None:
            self.opened_at = time.time()
            return True
        return False


class M4OBSOrchestrator(OBSOrchestrator):
    """M4 OBS Orchestrator implementing OBSOrchestrator protocol."""

    def __init__(
        self,
        config: M4Config | None = None,
        compliance_config: ComplianceConfig | None = None,
        client: Any | None = None,
        audio_playlist: Sequence[str] | None = None,
        video_source_path: str = "",
    ) -> None:
        """Initialize M4 OBS Orchestrator.

        Args:
            config: M4 configuration instance. Defaults to ``M4Config()``.
            compliance_config: Compliance gating settings.
            client: Optional pre-connected or mock ``obsws-python.ReqClient``.
            audio_playlist: Optional list of local audio track paths for rotation.
            video_source_path: Local media path for MainVideo source.
        """
        self._cfg = config if config is not None else M4Config()
        self._compliance_cfg = (
            compliance_config if compliance_config is not None else ComplianceConfig()
        )
        self._client = client
        self._audio_playlist = list(audio_playlist) if audio_playlist else []
        self._audio_index = 0
        self._video_source_path = video_source_path
        self._trace_id = uuid.uuid4().hex[:12]
        self._session_log = logger.bind(trace_id=self._trace_id)

        self._active_stream_key: str | None = None
        self._current_scene_index = 0
        self._circuit_breaker = CircuitBreaker(threshold=5, cooldown_seconds=60.0)
        self._shutdown_event = asyncio.Event()

    # -----------------------------------------------------------------------
    # Compliance verification
    # -----------------------------------------------------------------------

    def verify_compliance_gates(self) -> None:
        """Verify compliance rules BEFORE initiating any WebSocket network activity.

        Raises:
            ComplianceError: If disclosure banner is disabled (exit 3).
            StaleAckError: If ToS ack is missing or older than 30 days (exit 4).
        """
        banner_enabled = bool(
            self._compliance_cfg.disclosure_banner
            and self._compliance_cfg.disclosure_banner.enabled
        )
        if not banner_enabled:
            self._session_log.error(
                "m4_refused_no_disclosure",
                message="AI disclosure banner must be enabled in compliance config.",
            )
            raise ComplianceError(
                "Compliance gate failed: Disclosure banner must be enabled "
                "before streaming."
            )

        ack_at = self._compliance_cfg.tos_ack_at
        if ack_at is None:
            self._session_log.error(
                "m4_refused_stale_tos",
                message="Terms of Service acknowledgement (tos_ack_at) is missing.",
            )
            raise StaleAckError(
                "Compliance gate failed: ToS acknowledgement has not been accepted."
            )

        # Ensure timezone-aware comparison
        if ack_at.tzinfo is None:
            ack_at = ack_at.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)

        age_seconds = (now_utc - ack_at).total_seconds()
        max_allowed_seconds = 30 * 86400  # 30 days

        if age_seconds > max_allowed_seconds:
            age_days = age_seconds / 86400.0
            self._session_log.error(
                "m4_refused_stale_tos",
                age_days=age_days,
                message=(
                    f"ToS acknowledgement is {age_days:.1f} days old "
                    "(exceeds 30-day freshness limit)."
                ),
            )
            raise StaleAckError(
                f"Compliance gate failed: ToS ack is {age_days:.1f} days old "
                "(exceeds 30-day freshness limit)."
            )

        self._session_log.info(
            "m4_compliance_pass",
            message="Compliance verification passed (banner enabled; ToS fresh).",
        )

    # -----------------------------------------------------------------------
    # Connection and sources
    # -----------------------------------------------------------------------

    def _resolve_obs_password(self) -> str:
        """Resolve OBS WebSocket password via SecretVault."""
        from liveguard.secrets.vault import get_vault

        vault = get_vault()
        pw = vault.get("obs_ws_password")
        if pw is not None:
            return pw
        return ""

    async def connect(self) -> None:
        """Connect to OBS WebSocket server after validating compliance gates.

        Raises:
            ComplianceError: If disclosure banner is disabled.
            StaleAckError: If ToS ack is stale.
            ExternalDependencyError: If OBS Studio WebSocket server is unreachable.
        """
        # Compliance check MUST run before connecting
        self.verify_compliance_gates()

        if self._client is not None:
            return

        password = self._resolve_obs_password()

        def _do_connect() -> Any:
            try:
                import obsws_python as obs

                return obs.ReqClient(
                    host=self._cfg.obs_host,
                    port=self._cfg.obs_port,
                    password=password,
                    timeout=5,
                )
            except Exception as exc:  # noqa: BLE001
                raise ExternalDependencyError(
                    f"Failed to connect to OBS Studio WebSocket at "
                    f"{self._cfg.obs_host}:{self._cfg.obs_port}: {exc}"
                ) from exc

        self._session_log.info(
            "m4_connecting",
            host=self._cfg.obs_host,
            port=self._cfg.obs_port,
        )
        self._client = await asyncio.to_thread(_do_connect)
        self._session_log.info("m4_connected")

        # Auto-create missing sources across all configured scenes
        await self.ensure_all_sources()

    async def ensure_all_sources(self) -> None:
        """Ensure all required sources exist across configured scenes."""
        for scene_name in self._cfg.scenes:
            await self.ensure_sources_in_scene(scene_name)

    async def ensure_sources_in_scene(self, scene_name: str) -> None:
        """Create required standard sources in *scene_name* if missing.

        Sources created:
        - "MainVideo" (Media Source)
        - "OverlayTimer" (Browser Source clock)
        - "LiveBadge" (Browser Source blinking badge)
        - "MusicBed" (Audio Source)
        - "AIDisclosure" (Browser Source compliance banner)
        """
        if self._client is None:
            return

        client = self._client

        def _do_ensure() -> None:
            try:
                resp = client.get_input_list()
                existing_names = {
                    (
                        item.get("inputName")
                        if isinstance(item, dict)
                        else getattr(item, "input_name", "")
                    )
                    for item in getattr(resp, "inputs", [])
                }
            except Exception as exc:  # noqa: BLE001
                self._session_log.warning("failed_to_list_inputs", error=str(exc))
                existing_names = set()

            banner_text = self._compliance_cfg.disclosure_banner.text

            # 1. MainVideo
            if "MainVideo" not in existing_names:
                try:
                    client.create_input(
                        scene_name,
                        "MainVideo",
                        "ffmpeg_source",
                        {
                            "is_local_file": True,
                            "looping": True,
                            "restart_on_activate": True,
                            "local_file": self._video_source_path,
                        },
                        True,
                    )
                except Exception:  # noqa: BLE001
                    pass

            # 2. OverlayTimer
            if "OverlayTimer" not in existing_names:
                try:
                    client.create_input(
                        scene_name,
                        "OverlayTimer",
                        "browser_source",
                        {
                            "is_local_file": False,
                            "url": get_timer_data_uri(),
                            "width": 320,
                            "height": 80,
                            "restart_when_active": True,
                        },
                        True,
                    )
                except Exception:  # noqa: BLE001
                    pass

            # 3. LiveBadge
            if "LiveBadge" not in existing_names:
                try:
                    client.create_input(
                        scene_name,
                        "LiveBadge",
                        "browser_source",
                        {
                            "is_local_file": False,
                            "url": get_live_badge_data_uri(),
                            "width": 160,
                            "height": 60,
                            "restart_when_active": True,
                        },
                        True,
                    )
                except Exception:  # noqa: BLE001
                    pass

            # 4. MusicBed
            if "MusicBed" not in existing_names:
                current_track = (
                    self._audio_playlist[self._audio_index]
                    if self._audio_playlist
                    else ""
                )
                try:
                    client.create_input(
                        scene_name,
                        "MusicBed",
                        "ffmpeg_source",
                        {
                            "is_local_file": True,
                            "looping": True,
                            "local_file": current_track,
                        },
                        True,
                    )
                except Exception:  # noqa: BLE001
                    pass

            # 5. AIDisclosure banner (mandatory)
            if "AIDisclosure" not in existing_names:
                try:
                    client.create_input(
                        scene_name,
                        "AIDisclosure",
                        "browser_source",
                        {
                            "is_local_file": False,
                            "url": get_disclosure_banner_data_uri(banner_text),
                            "width": 520,
                            "height": 90,
                            "restart_when_active": True,
                        },
                        True,
                    )
                except Exception:  # noqa: BLE001
                    pass

        await asyncio.to_thread(_do_ensure)

    # -----------------------------------------------------------------------
    # Stream control & lifecycle
    # -----------------------------------------------------------------------

    async def start_stream(self, stream_key: str) -> StreamStatus:
        """Start streaming with exponential backoff and full jitter.

        Args:
            stream_key: Secret RTMP stream key entered by user.

        Returns:
            StreamStatus immediately following stream start.

        Raises:
            ComplianceError: If disclosure banner is off.
            StaleAckError: If ToS ack is older than 30 days.
            ExternalDependencyError: If OBS connection or stream start fails
                after 3 attempts.
        """
        # Always verify compliance first
        self.verify_compliance_gates()
        if self._client is None:
            await self.connect()

        self._active_stream_key = stream_key
        base = 2.0
        max_attempts = 3
        last_error: Exception | None = None

        def _configure_rtmp() -> None:
            assert self._client is not None
            self._client.set_stream_service_settings(
                "rtmp_custom",
                {
                    "server": self._cfg.rtmp_url,
                    "key": stream_key,
                },
            )

        await asyncio.to_thread(_configure_rtmp)

        for attempt in range(max_attempts):
            # Full jitter formula: sleep = random.uniform(0, base * 2^attempt)
            sleep_time = random.uniform(0, base * (2**attempt))
            try:
                self._session_log.info(
                    "stream_start_attempt",
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                )
                assert self._client is not None
                await asyncio.to_thread(self._client.start_stream)
                self._session_log.info("stream_started")
                return await self.get_status()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self._session_log.warning(
                    "stream_start_retry",
                    attempt=attempt + 1,
                    error=str(exc),
                    jitter_sleep_s=sleep_time,
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(sleep_time)

        raise ExternalDependencyError(
            f"Failed to start OBS stream after {max_attempts} attempts: {last_error}"
        )

    async def stop_stream(self) -> StreamStatus:
        """Stop active stream and wait for STOPPED state with a 10s timeout.

        Returns:
            Final StreamStatus snapshot.
        """
        if self._client is None:
            return StreamStatus(
                state=StreamState.STOPPED,
                scene_name="",
                uptime_seconds=0.0,
                dropped_frames=0,
                bitrate_kbps=0.0,
                disclosure_banner_active=True,
                rtmp_endpoint=self._cfg.rtmp_url,
            )

        self._session_log.info("stream_stopping")

        def _do_stop() -> None:
            try:
                assert self._client is not None
                self._client.stop_stream()
            except Exception:  # noqa: BLE001
                pass

        await asyncio.to_thread(_do_stop)

        timeout = 10.0
        start = time.time()
        while time.time() - start < timeout:
            status = await self.get_status()
            if status.state == StreamState.STOPPED:
                self._session_log.info("stream_stopped")
                return status
            await asyncio.sleep(0.5)

        self._session_log.warning("stop_stream_timeout")
        return await self.get_status()

    async def get_status(self) -> StreamStatus:
        """Poll the current streaming status from OBS Studio.

        Handles circuit breaker protection and state mapping.

        Returns:
            Current StreamStatus instance.
        """
        if self._client is None:
            return StreamStatus(
                state=StreamState.IDLE,
                scene_name="",
                uptime_seconds=0.0,
                dropped_frames=0,
                bitrate_kbps=0.0,
                disclosure_banner_active=True,
                rtmp_endpoint=self._cfg.rtmp_url,
            )

        if self._circuit_breaker.is_open:
            self._session_log.error(
                "circuit_open",
                message="Circuit breaker is currently OPEN. Rejecting status calls.",
            )
            raise ExternalDependencyError(
                "Circuit breaker is OPEN: OBS WebSocket failed 5 consecutive calls."
            )

        def _query_obs() -> tuple[Any, str]:
            assert self._client is not None
            stream_resp = self._client.get_stream_status()
            scene_resp = self._client.get_current_program_scene()
            scene_name = (
                getattr(scene_resp, "current_program_scene_name", "")
                or getattr(scene_resp, "scene_name", "")
                or "Unknown"
            )
            return stream_resp, scene_name

        try:
            stream_resp, scene_name = await asyncio.to_thread(_query_obs)
            self._circuit_breaker.record_success()

            output_active = bool(getattr(stream_resp, "output_active", False))
            reconnecting = bool(getattr(stream_resp, "output_reconnecting", False))

            if reconnecting:
                state = StreamState.CONNECTING
            elif output_active:
                state = StreamState.LIVE
            else:
                state = StreamState.STOPPED

            uptime_s = float(getattr(stream_resp, "output_duration", 0.0) / 1000.0)
            dropped = int(getattr(stream_resp, "output_skipped_frames", 0))
            kbits = float(getattr(stream_resp, "output_bytes", 0.0) * 8 / 1024.0)
            bitrate = kbits / max(1.0, uptime_s)

            return StreamStatus(
                state=state,
                scene_name=scene_name,
                uptime_seconds=uptime_s,
                dropped_frames=dropped,
                bitrate_kbps=bitrate,
                disclosure_banner_active=True,
                rtmp_endpoint=self._cfg.rtmp_url,
            )

        except Exception as exc:  # noqa: BLE001
            just_opened = self._circuit_breaker.record_failure()
            if just_opened:
                self._session_log.error(
                    "circuit_open",
                    failures=self._circuit_breaker.consecutive_failures,
                    message="OBS WebSocket failed 5 times; circuit opened for 60s.",
                )
            raise ExternalDependencyError(
                f"Failed to query OBS stream status: {exc}"
            ) from exc

    async def disconnect(self) -> None:
        """Close WebSocket connection to OBS Studio."""
        if self._client is not None:
            self._session_log.info("m4_disconnecting")

            def _do_disconnect() -> None:
                try:
                    assert self._client is not None
                    self._client.disconnect()
                except Exception:  # noqa: BLE001
                    pass

            await asyncio.to_thread(_do_disconnect)
            self._client = None
            self._session_log.info("m4_disconnected")

    # -----------------------------------------------------------------------
    # Background rotations & monitoring
    # -----------------------------------------------------------------------

    async def rotate_scene(self) -> None:
        """Cycle to the next scene in configured scenes list."""
        if not self._cfg.scenes or len(self._cfg.scenes) < 2 or self._client is None:
            return

        self._current_scene_index = (self._current_scene_index + 1) % len(
            self._cfg.scenes
        )
        next_scene = self._cfg.scenes[self._current_scene_index]

        self._session_log.info("scene_rotating", target_scene=next_scene)

        def _do_switch() -> None:
            assert self._client is not None
            self._client.set_current_program_scene(next_scene)

        await asyncio.to_thread(_do_switch)
        self._session_log.info("scene_rotated", active_scene=next_scene)

    async def rotate_audio(self) -> None:
        """Rotate to the next track in the audio playlist with volume crossfade."""
        if not self._audio_playlist or self._client is None:
            return

        self._audio_index = (self._audio_index + 1) % len(self._audio_playlist)
        next_track = self._audio_playlist[self._audio_index]

        self._session_log.info("audio_rotating", track=next_track)

        def _do_crossfade() -> None:
            assert self._client is not None
            try:
                # Fade down over 1s (simulate 0.1s steps)
                for step in range(10, 0, -2):
                    self._client.set_input_volume(
                        "MusicBed", input_volume_mul=step / 10.0
                    )
                    time.sleep(0.1)

                # Change track
                self._client.set_input_settings(
                    "MusicBed", {"local_file": next_track, "looping": True}, True
                )

                # Fade up over 1s
                for step in range(2, 11, 2):
                    self._client.set_input_volume(
                        "MusicBed", input_volume_mul=step / 10.0
                    )
                    time.sleep(0.1)
            except Exception:  # noqa: BLE001
                # Fallback: hard cut + 1s silence
                try:
                    self._client.set_input_settings(
                        "MusicBed", {"local_file": next_track, "looping": True}, True
                    )
                    time.sleep(1.0)
                except Exception:  # noqa: BLE001
                    pass

        await asyncio.to_thread(_do_crossfade)
        self._session_log.info("audio_rotated", active_track=next_track)

    async def monitor(self) -> None:
        """Monitor stream health continuously every 5 seconds.

        If connection drops, reconnects with jitter (max 5 retries).
        """
        reconnect_attempts = 0
        max_reconnects = 5

        while not self._shutdown_event.is_set():
            try:
                status = await self.get_status()
                if status.state == StreamState.LIVE:
                    reconnect_attempts = 0
                elif status.state == StreamState.STOPPED and self._active_stream_key:
                    self._session_log.warning("stream_dropped")
                    if reconnect_attempts < max_reconnects:
                        reconnect_attempts += 1
                        jitter = random.uniform(1.0, 5.0)
                        await asyncio.sleep(jitter)
                        await self.start_stream(self._active_stream_key)
                    else:
                        self._session_log.error("max_reconnects_exceeded")
                        break

            except ExternalDependencyError as exc:
                if self._circuit_breaker.is_open:
                    await asyncio.sleep(5.0)
                else:
                    self._session_log.warning("monitor_poll_failed", error=str(exc))

            await asyncio.sleep(5.0)

    # -----------------------------------------------------------------------
    # Signal handling & graceful shutdown
    # -----------------------------------------------------------------------

    def register_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register SIGINT and SIGTERM handlers for graceful shutdown."""

        def _on_signal(sig_name: str) -> None:
            self._session_log.info("shutdown_signal_received", signal=sig_name)
            self._shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _on_signal, sig.name)
            except (NotImplementedError, RuntimeError):
                signal.signal(
                    sig,
                    lambda s, f, name=sig.name: _on_signal(name),  # type: ignore[misc]
                )

    async def graceful_shutdown(self) -> None:
        """Execute teardown sequence: stop stream -> disconnect -> flush logs."""
        self._session_log.info("graceful_shutdown_begin")
        try:
            await self.stop_stream()
        except Exception as exc:  # noqa: BLE001
            self._session_log.warning("shutdown_stop_stream_failed", error=str(exc))

        try:
            await self.disconnect()
        except Exception as exc:  # noqa: BLE001
            self._session_log.warning("shutdown_disconnect_failed", error=str(exc))

        self._session_log.info("graceful_shutdown_complete")
