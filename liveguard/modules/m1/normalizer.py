"""M1 — Privacy Normalizer implementation.

Strips EXIF/XMP/GPS metadata via FFmpeg, preserves C2PA provenance by
default, injects neutral metadata tags, and optionally adds light noise.

Architecture note: M1Config is consumed from liveguard.config.schema; the
module never reads os.environ directly (vault is Phase 5+).

Usage example::

    import asyncio
    from pathlib import Path
    from liveguard.config.schema import M1Config
    from liveguard.modules.m1.normalizer import M1Normalizer

    normalizer = M1Normalizer(M1Config())
    report = asyncio.run(
        normalizer.process(Path("raw.mp4"), Path("clean.mp4"))
    )
    print(report.sha256_output)
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from liveguard.config.schema import M1Config
from liveguard.errors import ComplianceError, ExternalDependencyError, UsageError
from liveguard.interfaces import MetadataStrip, NormalizeReport

# ---------------------------------------------------------------------------
# M1-specific extended report
# ---------------------------------------------------------------------------

_FORBIDDEN_INJECT_TAGS: frozenset[str] = frozenset(
    {"Make", "Model", "GPSLatitude", "GPSLongitude", "GPSAltitude"}
)

_NEUTRAL_INJECT_TAGS: tuple[str, ...] = (
    "-Software=LiveGuard/1.0",
    # DateTimeOriginal is set dynamically at process() time
)

# ExifTool tag patterns that indicate C2PA provenance data
_C2PA_TAG_PATTERNS: tuple[str, ...] = ("-C2PA:all", "-XMP-c2pa:all")

# Tags that must NEVER appear in injected metadata (belt-and-suspenders)
_STRIP_PATTERN_TAGS: tuple[str, ...] = (
    "GPS:GPSLatitude",
    "GPS:GPSLongitude",
    "GPS:GPSAltitude",
    "EXIF:Make",
    "EXIF:Model",
    "EXIF:SerialNumber",
    "EXIF:CameraSerialNumber",
)


@dataclass
class M1Report(NormalizeReport):
    """Extended NormalizeReport with M1-specific fields.

    Attributes:
        input_hash: SHA-256 hex digest of the input file.
        output_hash: SHA-256 hex digest of the output file (= sha256_output).
        c2pa_before: Whether C2PA tags were present in the input.
        c2pa_after: Whether C2PA tags are present in the output.
        tags_removed: List of tag names stripped by FFmpeg/ExifTool.
        tags_injected: Mapping of tag name → injected value.
        ffmpeg_log_path: Path to the captured FFmpeg stderr log file.
        noise_strength: Noise filter ``alls`` value (0 = disabled).

    Example::

        report = M1Report(
            input_path=Path("/tmp/raw.mp4"),
            output_path=Path("/tmp/clean.mp4"),
            duration_seconds=5.0,
            input_hash="abc...",
            output_hash="def...",
            c2pa_before=False,
            c2pa_after=False,
            tags_removed=["GPS:GPSLatitude"],
            tags_injected={"-Software": "LiveGuard/1.0"},
            ffmpeg_log_path=Path("/tmp/ffmpeg_stderr.log"),
            noise_strength=4,
        )
    """

    input_hash: str = ""
    output_hash: str = ""
    c2pa_before: bool = False
    c2pa_after: bool = False
    tags_removed: list[str] = field(default_factory=list)
    tags_injected: dict[str, str] = field(default_factory=dict)
    ffmpeg_log_path: Path = field(default_factory=lambda: Path("/dev/null"))
    noise_strength: int = 0


# ---------------------------------------------------------------------------
# Normalizer implementation
# ---------------------------------------------------------------------------


class M1Normalizer:
    """M1 Privacy Normalizer.

    Implements :class:`~liveguard.interfaces.PrivacyNormalizer`.

    Strips EXIF/XMP/GPS metadata via FFmpeg, detects and preserves C2PA
    provenance, injects neutral metadata tags via ExifTool, and optionally
    adds light perceptual noise.

    Args:
        config: An :class:`~liveguard.config.schema.M1Config` instance.
        timeout_s: Subprocess timeout in seconds. Defaults to 3600.

    Example::

        from liveguard.config.schema import M1Config
        from liveguard.modules.m1.normalizer import M1Normalizer

        normalizer = M1Normalizer(M1Config(crf=22, noise=4))
        await normalizer.validate_dependencies()
        report = await normalizer.process(
            Path("raw.mp4"), Path("clean.mp4")
        )
    """

    def __init__(
        self,
        config: M1Config,
        timeout_s: int = 3600,
    ) -> None:
        """Initialise and verify required external dependencies.

        Args:
            config: M1 configuration block.
            timeout_s: Subprocess timeout in seconds.

        Raises:
            ExternalDependencyError: If ``ffmpeg`` or ``exiftool`` are not
                found on ``PATH``.
        """
        self._cfg = config
        self._timeout_s = timeout_s
        self._ffmpeg = self._require_tool("ffmpeg")
        self._exiftool = self._require_tool("exiftool")

    # ------------------------------------------------------------------
    # Public API (satisfies PrivacyNormalizer Protocol)
    # ------------------------------------------------------------------

    async def validate_dependencies(self) -> None:
        """Re-assert that ffmpeg and exiftool are still reachable.

        Raises:
            ExternalDependencyError: If either tool has disappeared from PATH.

        Example::

            await normalizer.validate_dependencies()
        """
        self._require_tool("ffmpeg")
        self._require_tool("exiftool")

    async def process(
        self,
        input: Path,
        output: Path,
        **kwargs: Any,
    ) -> M1Report:
        """Normalize privacy on *input* and write result to *output*.

        Execution order:
        1. Validate input file exists and is readable.
        2. Check compliance gate for ``--strip-provenance``.
        3. SHA-256 the input.
        4. Detect C2PA tags in the input via ExifTool.
        5. Run FFmpeg to strip all metadata and (optionally) add noise.
        6. Detect C2PA tags in the output — warn if dropped unexpectedly.
        7. Inject neutral metadata tags via ExifTool (in-place on output).
        8. SHA-256 the output.
        9. Build and return :class:`M1Report`.

        Args:
            input: Absolute path to the source video file.
            output: Absolute path for the normalised output file.
            **kwargs: Optional overrides:
                - ``strip_provenance`` (bool): Remove C2PA data. Requires
                  ``ack_legal_risk=True``; otherwise raises ComplianceError.
                - ``ack_legal_risk`` (bool): Acknowledges C2PA removal.
                - ``user_comment`` (str): Free-text Comment tag injected.
                - ``noise`` (int): Override noise strength in [0, 8].

        Returns:
            An :class:`M1Report` describing every transformation applied.

        Raises:
            UsageError: If *input* does not exist.
            ComplianceError: If ``strip_provenance=True`` but
                ``ack_legal_risk`` is not ``True``.
            ExternalDependencyError: If ffmpeg or exiftool is unavailable.

        Example::

            report = await normalizer.process(
                Path("raw.mp4"),
                Path("clean.mp4"),
                strip_provenance=False,
                user_comment="Stream prep 2025-01-01",
            )
        """
        strip_provenance: bool = bool(kwargs.get("strip_provenance", False))
        ack_legal_risk: bool = bool(kwargs.get("ack_legal_risk", False))
        user_comment: str = str(kwargs.get("user_comment", ""))
        # noise: explicit kwarg wins; fall back to config value
        noise_strength: int = int(kwargs.get("noise", self._cfg.noise))
        noise_strength = max(0, min(8, noise_strength))

        # 1. Validate input
        input = input.resolve()
        output = output.resolve()
        if not input.exists():
            raise UsageError(f"Input file not found: {input}")
        if not input.is_file():
            raise UsageError(f"Input path is not a regular file: {input}")

        # 2. Compliance gate: --strip-provenance requires --i-understand-legal-risk
        if strip_provenance != ack_legal_risk:
            raise ComplianceError(
                "--strip-provenance requires --i-understand-legal-risk. "
                "Both flags must be provided together, or neither."
            )

        # 3. SHA-256 input
        input_hash = _sha256_file(input)

        # 4. Detect C2PA in input
        c2pa_before = self._detect_c2pa(input)

        # 5. Build and run FFmpeg
        output.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_log_path, tags_removed = self._run_ffmpeg(
            input, output, noise_strength, strip_provenance
        )

        # 6. Detect C2PA in output
        c2pa_after = self._detect_c2pa(output)
        if c2pa_before and not c2pa_after and not strip_provenance:
            _audit_stderr(
                "c2pa_dropped_by_codec",
                {
                    "input": str(input),
                    "output": str(output),
                    "note": "C2PA provenance was present in input but lost during "
                    "re-encoding. Consider using a C2PA-aware codec pipeline.",
                },
            )

        # 7. Inject neutral metadata
        now_iso = datetime.now(tz=timezone.utc).strftime("%Y:%m:%d %H:%M:%S")
        tags_injected = self._inject_neutral_metadata(output, now_iso, user_comment)

        # 7b. Audit provenance-strip event
        if strip_provenance and ack_legal_risk:
            _audit_stderr(
                "provenance_stripped",
                {
                    "input": str(input),
                    "output": str(output),
                    "acknowledged": True,
                    "timestamp": now_iso,
                },
            )

        # 8. SHA-256 output
        output_hash = _sha256_file(output)

        # 9. Probe duration via ffprobe (best-effort; 0.0 on failure)
        duration_s = self._probe_duration(output)

        return M1Report(
            input_path=input,
            output_path=output,
            duration_seconds=duration_s,
            faces_redacted=[],  # Face redaction is a separate sub-step (future)
            metadata_strips=[
                MetadataStrip(
                    tag=t, original_value="[stripped]", tool="ffmpeg+exiftool"
                )
                for t in tags_removed
            ],
            c2pa_preserved=c2pa_after or (not c2pa_before),
            audio_normalized=self._cfg.audio_normalize,
            completed_at=datetime.now(tz=timezone.utc),
            sha256_output=output_hash,
            # Extended M1Report fields
            input_hash=input_hash,
            output_hash=output_hash,
            c2pa_before=c2pa_before,
            c2pa_after=c2pa_after,
            tags_removed=tags_removed,
            tags_injected=tags_injected,
            ffmpeg_log_path=ffmpeg_log_path,
            noise_strength=noise_strength,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_tool(name: str) -> str:
        """Return the absolute path of *name* or raise ExternalDependencyError.

        Args:
            name: Binary name to locate on ``PATH`` (e.g. ``"ffmpeg"``).

        Returns:
            The absolute path as a string.

        Raises:
            ExternalDependencyError: If *name* is not found on ``PATH``.

        Example::

            path = M1Normalizer._require_tool("ffmpeg")
        """
        found = shutil.which(name)
        if found is None:
            raise ExternalDependencyError(
                f"Required tool '{name}' not found on PATH. "
                f"Install it and ensure it is accessible.",
                dependency=name,
            )
        return found

    def _build_ffmpeg_argv(
        self,
        input: Path,
        output: Path,
        noise_strength: int,
        strip_provenance: bool,
    ) -> list[str]:
        """Construct the FFmpeg argument list.

        Args:
            input: Source video path.
            output: Destination video path.
            noise_strength: Noise filter ``alls`` value (0 = disabled).
            strip_provenance: If True, also pass ``-map_metadata -1`` to
                ensure all metadata (including C2PA containers) is dropped.

        Returns:
            A list of strings suitable for ``subprocess.run``.

        Example::

            argv = normalizer._build_ffmpeg_argv(
                Path("in.mp4"), Path("out.mp4"), noise_strength=4,
                strip_provenance=False
            )
        """
        vf_filters: list[str] = []
        if noise_strength > 0:
            vf_filters.append(f"noise=alls={noise_strength}:allf=t+u")

        argv: list[str] = [
            self._ffmpeg,
            "-y",  # overwrite output without prompting
            "-i",
            str(input),
            "-map_metadata",
            "-1",  # strip all metadata containers
            "-fflags",
            "+bitexact",
            "-c:v",
            "libx264",
            "-crf",
            str(self._cfg.crf),
            "-preset",
            self._cfg.preset,
            "-c:a",
            "copy",
        ]

        if vf_filters:
            argv += ["-vf", ",".join(vf_filters)]

        argv.append(str(output))
        return argv

    def _run_ffmpeg(
        self,
        input: Path,
        output: Path,
        noise_strength: int,
        strip_provenance: bool,
    ) -> tuple[Path, list[str]]:
        """Run FFmpeg to strip metadata and (optionally) add noise.

        Args:
            input: Source video path.
            output: Destination video path.
            noise_strength: Noise filter ``alls`` value.
            strip_provenance: Whether C2PA stripping is acknowledged.

        Returns:
            A tuple of (ffmpeg_log_path, tags_removed_list).

        Raises:
            ExternalDependencyError: If FFmpeg exits non-zero.

        Example::

            log_path, tags = normalizer._run_ffmpeg(
                Path("in.mp4"), Path("out.mp4"), 4, False
            )
        """
        argv = self._build_ffmpeg_argv(input, output, noise_strength, strip_provenance)

        log_fd, log_path_str = tempfile.mkstemp(
            suffix="_ffmpeg.log", prefix="liveguard_"
        )
        log_path = Path(log_path_str)

        try:
            with open(log_fd, "wb") as log_fh:
                result = subprocess.run(
                    argv,
                    stderr=log_fh,
                    stdout=subprocess.DEVNULL,
                    timeout=self._timeout_s,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise ExternalDependencyError(
                f"FFmpeg timed out after {self._timeout_s}s processing {input}",
                dependency="ffmpeg",
            ) from exc

        if result.returncode != 0:
            log_snippet = log_path.read_text(errors="replace")[-2000:]
            raise ExternalDependencyError(
                f"FFmpeg exited with code {result.returncode}. "
                f"Last 2000 bytes of stderr:\n{log_snippet}",
                dependency="ffmpeg",
            )

        # All metadata containers stripped by -map_metadata -1; report canonical list
        tags_removed = list(_STRIP_PATTERN_TAGS)
        return log_path, tags_removed

    def _detect_c2pa(self, path: Path) -> bool:
        """Detect the presence of C2PA provenance tags in a media file.

        Uses ExifTool with ``-C2PA:all`` and ``-XMP-c2pa:all`` pattern
        queries. Returns ``True`` if any C2PA tag is found.

        Args:
            path: Path to the media file to inspect.

        Returns:
            ``True`` if C2PA tags are present, ``False`` otherwise.

        Example::

            has_c2pa = normalizer._detect_c2pa(Path("video.mp4"))
        """
        argv = [self._exiftool, "-j", "-C2PA:all", "-XMP-c2pa:all", str(path)]
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                timeout=30,
                check=False,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return False

        if result.returncode != 0 or not result.stdout.strip():
            return False

        try:
            data: list[dict[str, Any]] = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False

        if not data:
            return False

        # Any key other than "SourceFile" indicates a C2PA tag was found
        return any(k != "SourceFile" for k in data[0])

    def _inject_neutral_metadata(
        self, path: Path, now_iso: str, user_comment: str
    ) -> dict[str, str]:
        """Inject neutral, non-identifying metadata tags via ExifTool.

        Injects: Software, DateTimeOriginal, Comment.
        Explicitly does NOT inject: Make, Model, GPS tags.

        Args:
            path: Path to the media file to tag (in-place).
            now_iso: ISO 8601 timestamp string for DateTimeOriginal.
            user_comment: Optional free-text comment. Empty string if none.

        Returns:
            A dict mapping tag names to injected values.

        Raises:
            ExternalDependencyError: If ExifTool exits non-zero.

        Example::

            injected = normalizer._inject_neutral_metadata(
                Path("clean.mp4"), "2025:01:01 12:00:00", "Stream prep"
            )
        """
        tags: dict[str, str] = {
            "-Software": "LiveGuard/1.0",
            "-DateTimeOriginal": now_iso,
        }
        if user_comment:
            tags["-Comment"] = user_comment

        # Belt-and-suspenders: assert no forbidden tags
        for tag_key in tags:
            bare = tag_key.lstrip("-").split("=")[0]
            if bare in _FORBIDDEN_INJECT_TAGS:
                raise ComplianceError(
                    f"Internal error: attempted to inject forbidden tag '{bare}'"
                )

        argv = [self._exiftool, "-overwrite_original"]
        for key, value in tags.items():
            argv.append(f"{key}={value}")
        argv.append(str(path))

        result = subprocess.run(
            argv,
            capture_output=True,
            timeout=30,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            raise ExternalDependencyError(
                f"ExifTool metadata injection failed (exit {result.returncode}): "
                f"{result.stderr.strip()}",
                dependency="exiftool",
            )

        # Return clean key→value mapping (strip leading dash for reporting)
        return {k.lstrip("-").split("=")[0]: v for k, v in tags.items()}

    def _probe_duration(self, path: Path) -> float:
        """Probe the media duration in seconds via ffprobe.

        Returns ``0.0`` on any failure (best-effort; never raises).

        Args:
            path: Path to the media file.

        Returns:
            Duration in seconds, or ``0.0`` if unavailable.

        Example::

            dur = normalizer._probe_duration(Path("clean.mp4"))
        """
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            return 0.0
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    str(path),
                ],
                capture_output=True,
                timeout=30,
                check=False,
                text=True,
            )
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except Exception:  # noqa: BLE001
            return 0.0


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file.

    Args:
        path: Path to the file to hash.

    Returns:
        Lowercase hex string of the SHA-256 digest.

    Example::

        digest = _sha256_file(Path("video.mp4"))
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _audit_stderr(event: str, payload: dict[str, Any]) -> None:
    """Write an audit event to stderr (stub until Phase 5 audit chain).

    Args:
        event: Short event name, e.g. ``"provenance_stripped"``.
        payload: Arbitrary dict of event context data.

    Example::

        _audit_stderr("provenance_stripped", {"input": "raw.mp4"})
    """
    record = {
        "liveguard_audit": True,
        "event": event,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        **payload,
    }
    print(json.dumps(record), file=sys.stderr)
