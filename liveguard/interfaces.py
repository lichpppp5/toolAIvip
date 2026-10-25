"""LiveGuard public interfaces.

Defines Protocol classes and dataclasses for every module.
No implementation logic lives here; mypy --strict must pass.

Usage example::

    from liveguard.interfaces import PrivacyNormalizer, NormalizeReport
    # Used as a type hint for dependency injection:
    def build_pipeline(normalizer: PrivacyNormalizer) -> None: ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------


class StreamState(Enum):
    """Observable states of the OBS streaming session."""

    IDLE = auto()
    CONNECTING = auto()
    LIVE = auto()
    STOPPING = auto()
    STOPPED = auto()
    ERROR = auto()


class GeoResult(Enum):
    """Outcome of a geographic availability probe."""

    AVAILABLE = auto()
    UNAVAILABLE = auto()
    RATE_LIMITED = auto()
    PROBE_FAILED = auto()


# ---------------------------------------------------------------------------
# Dataclasses — shared data transfer objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FaceRedaction:
    """Record of a single face redaction applied to a frame.

    Attributes:
        frame_index: Zero-based index of the affected frame.
        bbox: Bounding box as (x, y, width, height) in pixels.
        confidence: Detector confidence score in [0.0, 1.0].
        method: Redaction method applied, e.g. ``"blur"`` or ``"pixelate"``.
    """

    frame_index: int
    bbox: tuple[int, int, int, int]
    confidence: float
    method: str


@dataclass(frozen=True)
class MetadataStrip:
    """Record of a metadata field stripped from the media.

    Attributes:
        tag: EXIF/XMP/IPTC tag name, e.g. ``"GPS:GPSLatitude"``.
        original_value: Redacted representation of the original value.
        tool: Tool used to strip the tag, e.g. ``"exiftool"``.
    """

    tag: str
    original_value: str
    tool: str


@dataclass
class NormalizeReport:
    """Output report produced by the Privacy Normalizer (M1).

    Attributes:
        input_path: Absolute path to the source video.
        output_path: Absolute path to the normalized video.
        duration_seconds: Duration of the processed video.
        faces_redacted: List of face redaction records.
        metadata_strips: List of metadata fields removed.
        c2pa_preserved: Whether C2PA provenance data was preserved.
        audio_normalized: Whether audio levels were normalised.
        completed_at: UTC timestamp of completion.
        sha256_output: SHA-256 hex digest of the output file.

    Example::

        report = NormalizeReport(
            input_path=Path("/tmp/raw.mp4"),
            output_path=Path("/tmp/clean.mp4"),
            duration_seconds=120.5,
            faces_redacted=[],
            metadata_strips=[],
            c2pa_preserved=True,
            audio_normalized=True,
            completed_at=datetime.utcnow(),
            sha256_output="abc123...",
        )
    """

    input_path: Path
    output_path: Path
    duration_seconds: float
    faces_redacted: list[FaceRedaction] = field(default_factory=list)
    metadata_strips: list[MetadataStrip] = field(default_factory=list)
    c2pa_preserved: bool = True
    audio_normalized: bool = False
    completed_at: datetime = field(default_factory=datetime.utcnow)
    sha256_output: str = ""


@dataclass
class RobustnessReport:
    """Output report produced by the Robustness Tester (M2).

    Only produced when ``--research-only`` and
    ``--i-understand-research-use`` flags are both set.

    Attributes:
        input_path: Path to the video under test.
        watermarked_path: Path to the watermarked output (mandatory).
        perturbations_applied: Names of perturbation transforms applied.
        psnr_db: Peak signal-to-noise ratio of perturbed vs original.
        ssim: Structural similarity index.
        watermark_visible: Whether the watermark is visually detectable.
        completed_at: UTC timestamp of completion.

    Example::

        report = RobustnessReport(
            input_path=Path("/tmp/clean.mp4"),
            watermarked_path=Path("/tmp/wm.mp4"),
            perturbations_applied=["gaussian_noise", "compression"],
            psnr_db=38.2,
            ssim=0.97,
            watermark_visible=True,
            completed_at=datetime.utcnow(),
        )
    """

    input_path: Path
    watermarked_path: Path
    perturbations_applied: list[str] = field(default_factory=list)
    psnr_db: float = 0.0
    ssim: float = 0.0
    watermark_visible: bool = True
    completed_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class SessionReport:
    """Output report produced by the Geo-Test & Session Manager (M3).

    Attributes:
        region_probed: ISO-3166-1 alpha-2 region code probed.
        geo_result: Outcome of the geographic availability check.
        latency_ms: Round-trip latency to the RTMP ingest endpoint in ms.
        session_valid: Whether TikTok session cookies are valid.
        probe_url: URL used for the geo probe (no credentials).
        completed_at: UTC timestamp of completion.

    Example::

        report = SessionReport(
            region_probed="US",
            geo_result=GeoResult.AVAILABLE,
            latency_ms=45.2,
            session_valid=True,
            probe_url="https://www.tiktok.com/live",
            completed_at=datetime.utcnow(),
        )
    """

    region_probed: str
    geo_result: GeoResult
    latency_ms: float
    session_valid: bool
    probe_url: str
    completed_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class StreamStatus:
    """Real-time status snapshot produced by the OBS Orchestrator (M4).

    Attributes:
        state: Current streaming state.
        scene_name: Active OBS scene name.
        uptime_seconds: Seconds since stream went live.
        dropped_frames: Cumulative dropped frame count.
        bitrate_kbps: Current output bitrate in kbps.
        disclosure_banner_active: Whether the disclosure overlay is shown.
        rtmp_endpoint: Sanitised RTMP URL (key redacted).
        error_message: Human-readable error detail, if any.

    Example::

        status = StreamStatus(
            state=StreamState.LIVE,
            scene_name="Main",
            uptime_seconds=300.0,
            dropped_frames=2,
            bitrate_kbps=4500.0,
            disclosure_banner_active=True,
            rtmp_endpoint="rtmp://live.tiktok.com/live/***",
        )
    """

    state: StreamState
    scene_name: str
    uptime_seconds: float
    dropped_frames: int
    bitrate_kbps: float
    disclosure_banner_active: bool
    rtmp_endpoint: str
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Protocol classes
# ---------------------------------------------------------------------------


@runtime_checkable
class PrivacyNormalizer(Protocol):
    """Contract for M1 — Privacy Normalizer.

    Implementations must redact faces, strip metadata, and preserve
    C2PA provenance unless explicitly overridden.

    Example::

        normalizer: PrivacyNormalizer = M1PrivacyNormalizer(config)
        report = await normalizer.process(
            input=Path("raw.mp4"),
            output=Path("clean.mp4"),
        )
    """

    async def process(
        self,
        input: Path,
        output: Path,
        **kwargs: Any,
    ) -> NormalizeReport:
        """Normalize privacy on *input* and write result to *output*.

        Args:
            input: Absolute path to the source video file.
            output: Absolute path for the normalized output file.
            **kwargs: Optional overrides (e.g. ``strip_provenance=True``).

        Returns:
            A :class:`NormalizeReport` describing every transformation made.

        Raises:
            ExternalDependencyError: If ffmpeg or exiftool is unavailable.
            UsageError: If *input* does not exist or is not a video.
        """
        ...

    async def validate_dependencies(self) -> None:
        """Assert that all required external tools are reachable.

        Raises:
            ExternalDependencyError: If any required tool is missing.

        Example::

            await normalizer.validate_dependencies()
        """
        ...


@runtime_checkable
class RobustnessTester(Protocol):
    """Contract for M2 — Robustness Tester (research-only, opt-in).

    Implementations MUST embed a visible watermark and MUST refuse to
    run without both ``research_only=True`` and ``ack_research_use=True``.

    Example::

        tester: RobustnessTester = M2RobustnessTester(config)
        report = await tester.test(
            input=Path("clean.mp4"),
            output=Path("wm.mp4"),
            research_only=True,
            ack_research_use=True,
        )
    """

    async def test(
        self,
        input: Path,
        output: Path,
        research_only: bool,
        ack_research_use: bool,
        **kwargs: Any,
    ) -> RobustnessReport:
        """Apply robustness perturbations and embed a mandatory watermark.

        Args:
            input: Path to the privacy-normalized video.
            output: Path for the watermarked output.
            research_only: Must be ``True``; refuses otherwise.
            ack_research_use: Must be ``True``; refuses otherwise.
            **kwargs: Optional perturbation overrides.

        Returns:
            A :class:`RobustnessReport` with PSNR/SSIM metrics.

        Raises:
            UsageError: If ``research_only`` or ``ack_research_use`` is False.
            ExternalDependencyError: If ffmpeg is unavailable.
        """
        ...

    async def validate_dependencies(self) -> None:
        """Assert that all required external tools are reachable.

        Raises:
            ExternalDependencyError: If any required tool is missing.

        Example::

            await tester.validate_dependencies()
        """
        ...


@runtime_checkable
class GeoSessionManager(Protocol):
    """Contract for M3 — Geo-Test & Session Manager.

    Runs BEFORE M4. Must not scrape or store TikTok credentials.

    Example::

        manager: GeoSessionManager = M3GeoSessionManager(config)
        report = await manager.probe(region="US")
    """

    async def probe(self, region: str) -> SessionReport:
        """Probe TikTok availability for the given region.

        Args:
            region: ISO-3166-1 alpha-2 region code, e.g. ``"US"``.

        Returns:
            A :class:`SessionReport` with geo result and latency.

        Raises:
            ExternalDependencyError: If the browser runtime is unavailable.
            UsageError: If *region* is not a valid ISO-3166-1 alpha-2 code.
        """
        ...

    async def validate_session(self) -> bool:
        """Check whether existing session cookies are still valid.

        Does NOT scrape or store credentials.

        Returns:
            ``True`` if the session is valid, ``False`` otherwise.

        Example::

            valid = await manager.validate_session()
        """
        ...

    async def close(self) -> None:
        """Release browser resources and close any open pages.

        Example::

            await manager.close()
        """
        ...


@runtime_checkable
class OBSOrchestrator(Protocol):
    """Contract for M4 — OBS Orchestrator.

    Must refuse to stream if:
      * Disclosure banner is disabled (exit 3).
      * ``tos_ack_at`` is older than 30 days (exit 4).

    Example::

        orch: OBSOrchestrator = M4OBSOrchestrator(config)
        status = await orch.start_stream(stream_key="sk_live_…")
    """

    async def connect(self) -> None:
        """Establish a WebSocket connection to OBS Studio.

        Raises:
            ExternalDependencyError: If OBS is not running or unreachable.

        Example::

            await orch.connect()
        """
        ...

    async def start_stream(self, stream_key: str) -> StreamStatus:
        """Configure OBS and begin the RTMP stream.

        Args:
            stream_key: User-supplied TikTok RTMP stream key.
                        Never scraped or stored beyond this call.

        Returns:
            A :class:`StreamStatus` snapshot immediately after stream start.

        Raises:
            ComplianceError: If disclosure banner is disabled (exit 3).
            StaleAckError: If ``tos_ack_at`` is older than 30 days (exit 4).
            ExternalDependencyError: If OBS is unreachable.
        """
        ...

    async def stop_stream(self) -> StreamStatus:
        """Stop the active RTMP stream.

        Returns:
            A final :class:`StreamStatus` snapshot.

        Example::

            final = await orch.stop_stream()
        """
        ...

    async def get_status(self) -> StreamStatus:
        """Poll the current streaming status from OBS.

        Returns:
            A :class:`StreamStatus` snapshot.

        Example::

            status = await orch.get_status()
        """
        ...

    async def disconnect(self) -> None:
        """Close the WebSocket connection to OBS Studio.

        Example::

            await orch.disconnect()
        """
        ...
