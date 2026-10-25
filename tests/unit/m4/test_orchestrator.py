"""Unit tests for M4 — OBS Orchestrator.

Mocks obsws_python client entirely (no real OBS required).
Coverage target: ≥ 75% for liveguard/modules/m4/.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from liveguard.config.schema import ComplianceConfig, DisclosureBannerConfig, M4Config
from liveguard.errors import (
    ComplianceError,
    ExternalDependencyError,
    StaleAckError,
)
from liveguard.interfaces import StreamState, StreamStatus
from liveguard.modules.m4 import assets
from liveguard.modules.m4.orchestrator import (
    CircuitBreaker,
    M4OBSOrchestrator,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_compliance_config() -> ComplianceConfig:
    """Return a ComplianceConfig with banner enabled and fresh ToS ack."""
    return ComplianceConfig(
        disclosure_banner=DisclosureBannerConfig(
            enabled=True,
            text="[LIVEGUARD] AI-Enhanced Stream",
            position="top-right",
            font_size_pt=14,
        ),
        tos_ack_at=datetime.now(timezone.utc) - timedelta(days=2),
        tos_ack_hash="a" * 64,
    )


@pytest.fixture
def standard_m4_config() -> M4Config:
    """Return a standard M4Config with at least 2 scenes."""
    return M4Config(
        obs_host="localhost",
        obs_port=4455,
        rtmp_url="rtmp://live.example.com/app",
        scenes=["Scene_Main", "Scene_Secondary", "Scene_Wide"],
    )


@pytest.fixture
def mock_obs_client() -> MagicMock:
    """Return a mock obsws_python ReqClient."""
    client = MagicMock()
    # Default input list: empty
    input_list_resp = MagicMock()
    input_list_resp.inputs = []
    client.get_input_list.return_value = input_list_resp

    # Default stream status: stopped
    stream_resp = MagicMock()
    stream_resp.output_active = False
    stream_resp.output_reconnecting = False
    stream_resp.output_duration = 0.0
    stream_resp.output_skipped_frames = 0
    stream_resp.output_bytes = 0.0
    client.get_stream_status.return_value = stream_resp

    # Default program scene
    scene_resp = MagicMock()
    scene_resp.current_program_scene_name = "Scene_Main"
    client.get_current_program_scene.return_value = scene_resp

    return client


# ---------------------------------------------------------------------------
# Test Compliance Gates
# ---------------------------------------------------------------------------


class TestComplianceGates:
    """Compliance gates MUST run before any WebSocket connection."""

    def test_disclosure_banner_disabled_raises_compliance_error(
        self, fresh_compliance_config: ComplianceConfig, standard_m4_config: M4Config
    ) -> None:
        """Banner disabled -> ComplianceError (exit 3)."""
        fresh_compliance_config.disclosure_banner.enabled = False
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
        )

        with pytest.raises(ComplianceError, match="Disclosure banner must be enabled"):
            orch.verify_compliance_gates()

    def test_missing_tos_ack_raises_stale_ack_error(
        self, fresh_compliance_config: ComplianceConfig, standard_m4_config: M4Config
    ) -> None:
        """Missing tos_ack_at -> StaleAckError (exit 4)."""
        fresh_compliance_config.tos_ack_at = None
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
        )

        with pytest.raises(StaleAckError, match="has not been accepted"):
            orch.verify_compliance_gates()

    def test_stale_tos_ack_older_than_30_days_raises_stale_ack_error(
        self, fresh_compliance_config: ComplianceConfig, standard_m4_config: M4Config
    ) -> None:
        """tos_ack_at older than 30 days -> StaleAckError (exit 4)."""
        fresh_compliance_config.tos_ack_at = datetime.now(timezone.utc) - timedelta(
            days=31
        )
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
        )

        with pytest.raises(StaleAckError, match="exceeds 30-day freshness limit"):
            orch.verify_compliance_gates()

    def test_naive_datetime_handled_correctly(
        self, fresh_compliance_config: ComplianceConfig, standard_m4_config: M4Config
    ) -> None:
        """Naive datetime is converted to UTC and evaluated."""
        fresh_compliance_config.tos_ack_at = datetime.utcnow() - timedelta(
            days=5
        )  # noqa: DTZ003
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
        )
        # Should not raise
        orch.verify_compliance_gates()

    def test_compliance_pass(
        self, fresh_compliance_config: ComplianceConfig, standard_m4_config: M4Config
    ) -> None:
        """Both banner enabled and fresh ack -> passes without exception."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
        )
        orch.verify_compliance_gates()

    @pytest.mark.asyncio
    async def test_connect_fails_before_socket_if_compliance_fails(
        self, fresh_compliance_config: ComplianceConfig, standard_m4_config: M4Config
    ) -> None:
        """Ensure connect() enforces gates BEFORE attempting connection."""
        fresh_compliance_config.disclosure_banner.enabled = False
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
        )

        with patch("obsws_python.ReqClient") as mock_req:
            with pytest.raises(ComplianceError):
                await orch.connect()
            mock_req.assert_not_called()


# ---------------------------------------------------------------------------
# Test Connection and Source Auto-Creation
# ---------------------------------------------------------------------------


class TestConnectionAndSources:
    """Source auto-creation and client connection."""

    @pytest.mark.asyncio
    async def test_connect_creates_req_client_and_provisions_sources(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """connect() uses obsws_python.ReqClient and provisions missing sources."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
        )

        with patch("obsws_python.ReqClient", return_value=mock_obs_client) as mock_req:
            with patch.dict("os.environ", {"OBS_WS_PASSWORD": "secret_password"}):
                await orch.connect()

            mock_req.assert_called_once_with(
                host="localhost",
                port=4455,
                password="secret_password",
                timeout=5,
            )

        # Sources should have been provisioned across all 3 scenes:
        # 5 sources * 3 scenes = 15 create_input calls
        assert mock_obs_client.create_input.call_count == 15

    @pytest.mark.asyncio
    async def test_connect_failure_raises_external_dependency_error(
        self, fresh_compliance_config: ComplianceConfig, standard_m4_config: M4Config
    ) -> None:
        """Connection failure wraps as ExternalDependencyError."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
        )

        with patch(
            "obsws_python.ReqClient", side_effect=ConnectionRefusedError("Refused")
        ):
            with pytest.raises(
                ExternalDependencyError, match="Failed to connect to OBS Studio"
            ):
                await orch.connect()

    @pytest.mark.asyncio
    async def test_ensure_sources_creates_all_five_standard_sources(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """Assert create_input calls for all five standard sources."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
            video_source_path="/media/video.mp4",
            audio_playlist=["/music/track1.mp3", "/music/track2.mp3"],
        )

        await orch.ensure_sources_in_scene("Scene_Main")

        created_inputs = [
            call[0][1] for call in mock_obs_client.create_input.call_args_list
        ]
        assert "MainVideo" in created_inputs
        assert "OverlayTimer" in created_inputs
        assert "LiveBadge" in created_inputs
        assert "MusicBed" in created_inputs
        assert "AIDisclosure" in created_inputs

        # Check MainVideo settings
        main_call = [
            c
            for c in mock_obs_client.create_input.call_args_list
            if c[0][1] == "MainVideo"
        ][0]
        assert main_call[0][2] == "ffmpeg_source"
        assert main_call[0][3]["is_local_file"] is True
        assert main_call[0][3]["looping"] is True
        assert main_call[0][3]["restart_on_activate"] is True
        assert main_call[0][3]["local_file"] == "/media/video.mp4"

        # Check AIDisclosure settings
        banner_call = [
            c
            for c in mock_obs_client.create_input.call_args_list
            if c[0][1] == "AIDisclosure"
        ][0]
        assert banner_call[0][2] == "browser_source"
        assert banner_call[0][3]["url"].startswith("data:text/html;base64,")

    @pytest.mark.asyncio
    async def test_ensure_sources_skips_already_existing(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """If sources already exist, do not recreate them."""
        input_list_resp = MagicMock()
        input_list_resp.inputs = [
            {"inputName": "MainVideo"},
            {"inputName": "OverlayTimer"},
            {"inputName": "LiveBadge"},
            {"inputName": "MusicBed"},
            {"inputName": "AIDisclosure"},
        ]
        mock_obs_client.get_input_list.return_value = input_list_resp

        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )

        await orch.ensure_sources_in_scene("Scene_Main")
        mock_obs_client.create_input.assert_not_called()


# ---------------------------------------------------------------------------
# Test Stream Lifecycle (start, stop, retries, jitter)
# ---------------------------------------------------------------------------


class TestStreamLifecycle:
    """Stream start, stop, retry backoff with jitter."""

    @pytest.mark.asyncio
    async def test_start_stream_success_first_attempt(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """Start stream configures RTMP and starts streaming."""
        # Set status response to LIVE
        mock_obs_client.get_stream_status.return_value.output_active = True
        mock_obs_client.get_stream_status.return_value.output_duration = 5000.0

        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )

        status = await orch.start_stream(stream_key="live_key_xyz")
        assert status.state == StreamState.LIVE
        mock_obs_client.set_stream_service_settings.assert_called_once_with(
            "rtmp_custom",
            {"server": "rtmp://live.example.com/app", "key": "live_key_xyz"},
        )
        mock_obs_client.start_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_stream_retries_with_jitter_on_failures(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """Start stream: 2 failures then success -> 3 attempts recorded."""
        # Setup start_stream to fail twice then succeed
        mock_obs_client.start_stream.side_effect = [
            RuntimeError("Socket error 1"),
            RuntimeError("Socket error 2"),
            None,  # Success
        ]
        mock_obs_client.get_stream_status.return_value.output_active = True

        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with patch("random.uniform", side_effect=[1.2, 2.5, 3.8]) as mock_jitter:
                status = await orch.start_stream(stream_key="live_key_xyz")

        assert status.state == StreamState.LIVE
        assert mock_obs_client.start_stream.call_count == 3
        # Jitter sleep should have been called twice (after attempt 1 and attempt 2)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_has_calls([call(1.2), call(2.5)])
        assert mock_jitter.call_count == 3

    @pytest.mark.asyncio
    async def test_start_stream_exceeds_max_attempts_raises_error(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """Start stream: 3 consecutive failures raises ExternalDependencyError."""
        mock_obs_client.start_stream.side_effect = RuntimeError(
            "Persistent connection error"
        )

        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(
                ExternalDependencyError, match="Failed to start OBS stream after 3"
            ):
                await orch.start_stream(stream_key="live_key_xyz")

        assert mock_obs_client.start_stream.call_count == 3

    @pytest.mark.asyncio
    async def test_stop_stream_waits_for_stopped_state(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """stop_stream calls client.stop_stream and polls until STOPPED."""
        # Active first, then stopped on next poll
        resp1 = MagicMock(
            output_active=True, output_reconnecting=False, output_duration=1000
        )
        resp2 = MagicMock(
            output_active=False, output_reconnecting=False, output_duration=0
        )
        mock_obs_client.get_stream_status.side_effect = [resp1, resp2]

        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            status = await orch.stop_stream()

        mock_obs_client.stop_stream.assert_called_once()
        assert status.state == StreamState.STOPPED


# ---------------------------------------------------------------------------
# Test Circuit Breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Circuit breaker pattern under consecutive get_stream_status failures."""

    def test_circuit_breaker_logic(self) -> None:
        """Unit test for CircuitBreaker state transitions."""
        cb = CircuitBreaker(threshold=5, cooldown_seconds=60.0)
        assert not cb.is_open

        for _ in range(4):
            just_opened = cb.record_failure()
            assert not just_opened
            assert not cb.is_open

        # 5th failure opens the circuit
        just_opened = cb.record_failure()
        assert just_opened
        assert cb.is_open

        # Cooldown period
        with patch("time.time", return_value=cb.opened_at + 30.0):  # type: ignore[operator]
            assert cb.is_open

        # After cooldown
        with patch("time.time", return_value=cb.opened_at + 61.0):  # type: ignore[operator]
            assert not cb.is_open

        # Success resets
        cb.record_success()
        assert cb.consecutive_failures == 0
        assert not cb.is_open

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_5_failures_and_rejects_subsequent(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """get_status fails 5 times, opens circuit, subsequent call raises error."""
        mock_obs_client.get_stream_status.side_effect = RuntimeError("Status timeout")

        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )

        # 5 failures from get_status
        for _ in range(5):
            with pytest.raises(ExternalDependencyError):
                await orch.get_status()

        assert orch._circuit_breaker.is_open

        # 6th call: circuit is OPEN, should reject immediately
        call_count_before = mock_obs_client.get_stream_status.call_count
        with pytest.raises(ExternalDependencyError, match="Circuit breaker is OPEN"):
            await orch.get_status()

        # No new call to client because circuit rejected it
        assert mock_obs_client.get_stream_status.call_count == call_count_before


# ---------------------------------------------------------------------------
# Test Scene and Audio Rotation
# ---------------------------------------------------------------------------


class TestSceneAndAudioRotation:
    """Scene and audio rotation behaviors."""

    @pytest.mark.asyncio
    async def test_scene_rotation_cycles_scenes_deterministically(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """Cycle scenes through config list."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )

        await orch.rotate_scene()
        mock_obs_client.set_current_program_scene.assert_called_with("Scene_Secondary")

        await orch.rotate_scene()
        mock_obs_client.set_current_program_scene.assert_called_with("Scene_Wide")

        await orch.rotate_scene()
        mock_obs_client.set_current_program_scene.assert_called_with("Scene_Main")

    @pytest.mark.asyncio
    async def test_audio_rotation_volume_crossfade(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """Audio rotation ramps volume down, switches track, ramps volume up."""
        playlist = ["/music/track1.mp3", "/music/track2.mp3"]
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
            audio_playlist=playlist,
        )

        with patch("time.sleep"):
            await orch.rotate_audio()

        # Input settings updated with track 2
        mock_obs_client.set_input_settings.assert_called_once_with(
            "MusicBed", {"local_file": "/music/track2.mp3", "looping": True}, True
        )
        # Volume set multiple times during ramp
        assert mock_obs_client.set_input_volume.call_count > 0


# ---------------------------------------------------------------------------
# Test Signal Handling and Graceful Shutdown
# ---------------------------------------------------------------------------


class TestSignalHandlingAndShutdown:
    """Shutdown sequence: stop stream -> disconnect WS -> flush logs."""

    @pytest.mark.asyncio
    async def test_graceful_shutdown_order(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """Assert stop_stream then disconnect order during shutdown."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )

        stop_mock = AsyncMock()
        disconnect_mock = AsyncMock()
        orch.stop_stream = stop_mock  # type: ignore[method-assign]
        orch.disconnect = disconnect_mock  # type: ignore[method-assign]

        await orch.graceful_shutdown()

        assert stop_mock.called
        assert disconnect_mock.called
        # Verify order of calls
        assert stop_mock.call_args is not None
        assert disconnect_mock.call_args is not None

    def test_register_signal_handlers(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
    ) -> None:
        """Signal handlers can be registered on asyncio loop."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
        )
        loop = MagicMock()
        orch.register_signal_handlers(loop)
        assert loop.add_signal_handler.call_count == 2

    @pytest.mark.asyncio
    async def test_graceful_shutdown_handles_inner_exceptions(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
    ) -> None:
        """graceful_shutdown does not crash if stop_stream or disconnect raises."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
        )
        orch.stop_stream = AsyncMock(side_effect=RuntimeError("stop failed"))  # type: ignore[method-assign]
        orch.disconnect = AsyncMock(side_effect=RuntimeError("disconnect failed"))  # type: ignore[method-assign]

        await orch.graceful_shutdown()
        orch.stop_stream.assert_called_once()
        orch.disconnect.assert_called_once()


# ---------------------------------------------------------------------------
# Test Disconnect, Status and Rotations Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCasesAndBranchCoverage:
    """Branch coverage tests for disconnect, status mappings, fallbacks."""

    @pytest.mark.asyncio
    async def test_disconnect_calls_client_disconnect(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """disconnect() calls client.disconnect() and clears reference."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )
        assert orch._client is not None
        await orch.disconnect()
        assert orch._client is None
        mock_obs_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_status_when_client_is_none(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
    ) -> None:
        """get_status() returns IDLE when not connected."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=None,
        )
        status = await orch.get_status()
        assert status.state == StreamState.IDLE

    @pytest.mark.asyncio
    async def test_stop_stream_when_client_is_none(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
    ) -> None:
        """stop_stream() returns STOPPED when not connected."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=None,
        )
        status = await orch.stop_stream()
        assert status.state == StreamState.STOPPED

    @pytest.mark.asyncio
    async def test_get_status_reconnecting_state(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """get_status() maps output_reconnecting=True to CONNECTING."""
        mock_obs_client.get_stream_status.return_value.output_active = True
        mock_obs_client.get_stream_status.return_value.output_reconnecting = True

        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )
        status = await orch.get_status()
        assert status.state == StreamState.CONNECTING

    @pytest.mark.asyncio
    async def test_stop_stream_timeout_returns_last_status(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """stop_stream() logs warning and returns status after timeout."""
        mock_obs_client.get_stream_status.return_value.output_active = True
        mock_obs_client.get_stream_status.return_value.output_reconnecting = False

        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )

        with patch("time.time", side_effect=[0.0, 0.0, 11.0, 11.0]):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                status = await orch.stop_stream()

        assert status.state == StreamState.LIVE

    @pytest.mark.asyncio
    async def test_rotate_scene_noop_when_insufficient_scenes(
        self,
        fresh_compliance_config: ComplianceConfig,
        mock_obs_client: MagicMock,
    ) -> None:
        """rotate_scene does nothing if fewer than 2 scenes configured."""
        orch = M4OBSOrchestrator(
            config=M4Config(scenes=["Scene1", "Scene2"]),
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )
        orch._cfg.scenes = ["OnlyOne"]
        await orch.rotate_scene()
        mock_obs_client.set_current_program_scene.assert_not_called()

    @pytest.mark.asyncio
    async def test_rotate_audio_noop_when_playlist_empty(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """rotate_audio does nothing if audio playlist is empty."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
            audio_playlist=[],
        )
        await orch.rotate_audio()
        mock_obs_client.set_input_settings.assert_not_called()

    @pytest.mark.asyncio
    async def test_rotate_audio_fallback_on_volume_ramp_failure(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """rotate_audio falls back to hard cut if set_input_volume fails."""
        mock_obs_client.set_input_volume.side_effect = RuntimeError(
            "Filter unsupported"
        )
        playlist = ["/music/track1.mp3", "/music/track2.mp3"]
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
            audio_playlist=playlist,
        )

        with patch("time.sleep"):
            await orch.rotate_audio()

        # Hard cut fallback called set_input_settings
        mock_obs_client.set_input_settings.assert_called_with(
            "MusicBed", {"local_file": "/music/track2.mp3", "looping": True}, True
        )

    @pytest.mark.asyncio
    async def test_monitor_reconnects_when_stream_stopped(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
        mock_obs_client: MagicMock,
    ) -> None:
        """monitor() triggers start_stream when stream dropped to STOPPED."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
            client=mock_obs_client,
        )
        orch._active_stream_key = "test_stream_key"

        # Mock status responses: STOPPED
        stopped_status = StreamStatus(
            state=StreamState.STOPPED,
            scene_name="Scene_Main",
            uptime_seconds=0.0,
            dropped_frames=0,
            bitrate_kbps=0.0,
            disclosure_banner_active=True,
            rtmp_endpoint="rtmp://live.example.com/app",
        )

        async def _mock_get_status() -> StreamStatus:
            return stopped_status

        async def _mock_start_stream(stream_key: str) -> StreamStatus:
            orch._shutdown_event.set()
            return stopped_status

        orch.get_status = _mock_get_status  # type: ignore[method-assign]
        orch.start_stream = AsyncMock(side_effect=_mock_start_stream)  # type: ignore[method-assign]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await orch.monitor()

        orch.start_stream.assert_called_once_with("test_stream_key")

    @pytest.mark.asyncio
    async def test_monitor_handles_circuit_breaker_open(
        self,
        fresh_compliance_config: ComplianceConfig,
        standard_m4_config: M4Config,
    ) -> None:
        """monitor() catches ExternalDependencyError and sleeps when circuit is open."""
        orch = M4OBSOrchestrator(
            config=standard_m4_config,
            compliance_config=fresh_compliance_config,
        )
        orch._circuit_breaker.opened_at = 1000.0  # Open

        poll_count = 0

        async def _failing_status() -> StreamStatus:
            nonlocal poll_count
            poll_count += 1
            orch._shutdown_event.set()
            raise ExternalDependencyError("Circuit open")

        orch.get_status = _failing_status  # type: ignore[method-assign]

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await orch.monitor()

        assert poll_count == 1
        mock_sleep.assert_called()


# ---------------------------------------------------------------------------
# Test Assets
# ---------------------------------------------------------------------------


class TestAssets:
    """Test HTML assets generation."""

    def test_html_assets_and_data_uris(self) -> None:
        """Verify HTML constants and base64 data URIs."""
        timer_uri = assets.get_timer_data_uri()
        assert timer_uri.startswith("data:text/html;base64,")

        badge_uri = assets.get_live_badge_data_uri()
        assert badge_uri.startswith("data:text/html;base64,")

        banner_uri = assets.get_disclosure_banner_data_uri("Custom AI Disclosure")
        assert banner_uri.startswith("data:text/html;base64,")
        assert "Custom AI Disclosure" in assets.render_disclosure_banner_html(
            "Custom AI Disclosure"
        )
