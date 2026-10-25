"""Unit tests for M1 — Privacy Normalizer.

All subprocess calls are mocked; no real FFmpeg or ExifTool required.
Coverage target: ≥ 75% of liveguard/modules/m1/.

Run with::

    pytest tests/unit/m1/test_normalizer.py -v --cov=liveguard/modules/m1
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from liveguard.config.schema import M1Config
from liveguard.errors import (
    ComplianceError,
    ExternalDependencyError,
    UsageError,
)
from liveguard.modules.m1.normalizer import (
    M1Normalizer,
    _sha256_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tmp_video(tmp_path: Path) -> Path:
    """Write a minimal 4-byte stub file as a fake video."""
    p = tmp_path / "input.mp4"
    p.write_bytes(b"\x00\x00\x00\x18")
    return p


def _write_tmp_output(tmp_path: Path) -> Path:
    """Write a minimal output stub file."""
    p = tmp_path / "output.mp4"
    p.write_bytes(b"\x00\x00\x00\x01")
    return p


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def default_cfg() -> M1Config:
    """Return a default M1Config instance."""
    return M1Config()


@pytest.fixture()
def normalizer_with_mocked_tools(
    default_cfg: M1Config,
) -> M1Normalizer:
    """Return an M1Normalizer with ffmpeg/exiftool mocked to exist."""
    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        return M1Normalizer(default_cfg)


# ---------------------------------------------------------------------------
# Test: missing tools at __init__
# ---------------------------------------------------------------------------


class TestDependencyValidation:
    def test_missing_ffmpeg_raises(self, default_cfg: M1Config) -> None:
        """ExternalDependencyError is raised when ffmpeg is absent from PATH."""

        def _which(name: str) -> str | None:
            return None if name == "ffmpeg" else "/usr/bin/exiftool"

        with patch("shutil.which", side_effect=_which):
            with pytest.raises(ExternalDependencyError) as exc_info:
                M1Normalizer(default_cfg)
        assert exc_info.value.exit_code == 5
        assert "ffmpeg" in exc_info.value.message.lower()

    def test_missing_exiftool_raises(self, default_cfg: M1Config) -> None:
        """ExternalDependencyError is raised when exiftool is absent from PATH."""

        def _which(name: str) -> str | None:
            return "/usr/bin/ffmpeg" if name == "ffmpeg" else None

        with patch("shutil.which", side_effect=_which):
            with pytest.raises(ExternalDependencyError) as exc_info:
                M1Normalizer(default_cfg)
        assert exc_info.value.exit_code == 5
        assert "exiftool" in exc_info.value.message.lower()

    def test_both_tools_present_succeeds(self, default_cfg: M1Config) -> None:
        """No exception when both tools are on PATH."""
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            n = M1Normalizer(default_cfg)
        assert n is not None


# ---------------------------------------------------------------------------
# Test: FFmpeg command construction
# ---------------------------------------------------------------------------


class TestFFmpegArgvConstruction:
    @pytest.mark.parametrize(
        ("crf", "preset", "noise"),
        [
            (23, "medium", 4),
            (18, "fast", 0),
            (28, "slow", 8),
            (0, "ultrafast", 1),
        ],
    )
    def test_ffmpeg_argv_structure(
        self,
        crf: int,
        preset: str,
        noise: int,
        tmp_path: Path,
    ) -> None:
        """FFmpeg argv contains correct -crf, -preset, and -vf values."""
        cfg = M1Config(crf=crf, preset=preset, noise=noise)  # type: ignore[call-arg]
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            n = M1Normalizer(cfg)

        inp = tmp_path / "in.mp4"
        out = tmp_path / "out.mp4"
        argv = n._build_ffmpeg_argv(inp, out, noise, strip_provenance=False)

        assert "-map_metadata" in argv
        assert "-1" in argv
        assert "-fflags" in argv
        assert "+bitexact" in argv
        assert "-crf" in argv
        assert str(crf) in argv
        assert "-preset" in argv
        assert preset in argv
        assert str(out) in argv
        assert str(inp) in argv

    def test_noise_filter_added_when_nonzero(self, tmp_path: Path) -> None:
        """``-vf noise=alls=N`` appears in argv when noise > 0."""
        cfg = M1Config(noise=6)  # type: ignore[call-arg]
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            n = M1Normalizer(cfg)

        inp, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
        argv = n._build_ffmpeg_argv(inp, out, noise_strength=6, strip_provenance=False)

        assert "-vf" in argv
        vf_idx = argv.index("-vf")
        vf_val = argv[vf_idx + 1]
        assert "noise=alls=6" in vf_val
        assert "allf=t+u" in vf_val

    def test_noise_filter_absent_when_zero(self, tmp_path: Path) -> None:
        """``-vf`` does NOT appear in argv when noise == 0."""
        cfg = M1Config(noise=0)  # type: ignore[call-arg]
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            n = M1Normalizer(cfg)

        inp, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
        argv = n._build_ffmpeg_argv(inp, out, noise_strength=0, strip_provenance=False)

        assert "-vf" not in argv

    def test_libx264_codec_always_present(self, tmp_path: Path) -> None:
        """libx264 is always used as the video codec."""
        cfg = M1Config()
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            n = M1Normalizer(cfg)

        inp, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
        argv = n._build_ffmpeg_argv(inp, out, noise_strength=0, strip_provenance=False)

        assert "-c:v" in argv
        assert "libx264" in argv


# ---------------------------------------------------------------------------
# Test: compliance gate
# ---------------------------------------------------------------------------


class TestComplianceGate:
    def test_strip_provenance_without_ack_raises_compliance_error(
        self, tmp_path: Path, default_cfg: M1Config
    ) -> None:
        """ComplianceError (exit 3) raised when --strip-provenance but no ack."""
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            n = M1Normalizer(default_cfg)

        inp = _make_tmp_video(tmp_path)

        with pytest.raises(ComplianceError) as exc_info:
            asyncio.run(
                n.process(
                    inp,
                    tmp_path / "out.mp4",
                    strip_provenance=True,
                    ack_legal_risk=False,
                )
            )
        assert exc_info.value.exit_code == 3
        assert (
            "strip-provenance" in exc_info.value.message.lower()
            or "ack" in exc_info.value.message.lower()
            or "legal" in exc_info.value.message.lower()
        )

    def test_strip_provenance_with_ack_does_not_raise_compliance(
        self, tmp_path: Path, default_cfg: M1Config
    ) -> None:
        """No ComplianceError when provenance and legal risk flags are set."""
        inp = _make_tmp_video(tmp_path)
        out = tmp_path / "out.mp4"
        out.write_bytes(b"\x00\x01")  # pre-create so SHA-256 can run

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            n = M1Normalizer(default_cfg)

        # Mock subprocess.run for both ffmpeg and exiftool calls
        mock_ffmpeg_result = MagicMock(returncode=0)
        mock_exiftool_c2pa_result = MagicMock(returncode=0, stdout="[]", stderr="")
        mock_exiftool_inject_result = MagicMock(returncode=0, stdout="", stderr="")
        mock_ffprobe_result = MagicMock(
            returncode=0, stdout=json.dumps({"format": {"duration": "5.0"}})
        )

        call_results = [
            mock_ffmpeg_result,  # _run_ffmpeg
            mock_exiftool_c2pa_result,  # _detect_c2pa(input)
            mock_exiftool_c2pa_result,  # _detect_c2pa(output)
            mock_exiftool_inject_result,  # _inject_neutral_metadata
            mock_ffprobe_result,  # _probe_duration
        ]

        def _side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            if call_results:
                return call_results.pop(0)
            return MagicMock(returncode=0, stdout="[]", stderr="")

        with (
            patch("subprocess.run", side_effect=_side_effect),
            patch("tempfile.mkstemp", return_value=(1, str(tmp_path / "ff.log"))),
            patch("os.open", return_value=1),
            patch(
                "builtins.open",
                MagicMock(
                    return_value=MagicMock(
                        __enter__=MagicMock(return_value=MagicMock()),
                        __exit__=MagicMock(return_value=False),
                    )
                ),
            ),
        ):
            # Should NOT raise ComplianceError
            try:
                asyncio.run(
                    n.process(
                        inp,
                        out,
                        strip_provenance=True,
                        ack_legal_risk=True,
                    )
                )
            except ComplianceError:
                pytest.fail("ComplianceError raised unexpectedly with ack=True")
            except Exception:  # noqa: BLE001
                # Subprocess mocking is partial; we only care about no ComplianceError
                pass

    def test_missing_input_raises_usage_error(
        self, tmp_path: Path, default_cfg: M1Config
    ) -> None:
        """UsageError (exit 2) raised when input file does not exist."""
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            n = M1Normalizer(default_cfg)

        with pytest.raises(UsageError) as exc_info:
            asyncio.run(n.process(tmp_path / "nonexistent.mp4", tmp_path / "out.mp4"))
        assert exc_info.value.exit_code == 2


# ---------------------------------------------------------------------------
# Test: neutral metadata injection
# ---------------------------------------------------------------------------


class TestNeutralMetadataInjection:
    def test_inject_contains_software_tag(
        self, tmp_path: Path, default_cfg: M1Config
    ) -> None:
        """ExifTool argv contains -Software=LiveGuard/1.0."""
        target = tmp_path / "video.mp4"
        target.write_bytes(b"\x00")

        with patch("shutil.which", return_value="/usr/bin/exiftool"):
            n = M1Normalizer(default_cfg)

        captured_argv: list[list[str]] = []

        def _run(*args: Any, **kwargs: Any) -> MagicMock:
            if args:
                captured_argv.append(list(args[0]))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=_run):
            n._inject_neutral_metadata(target, "2025:01:01 00:00:00", "")

        assert any("-Software=LiveGuard/1.0" in argv for argv in captured_argv)

    def test_inject_contains_datetime_tag(
        self, tmp_path: Path, default_cfg: M1Config
    ) -> None:
        """ExifTool argv contains -DateTimeOriginal=<timestamp>."""
        target = tmp_path / "video.mp4"
        target.write_bytes(b"\x00")

        with patch("shutil.which", return_value="/usr/bin/exiftool"):
            n = M1Normalizer(default_cfg)

        captured_argv: list[list[str]] = []

        def _run(*args: Any, **kwargs: Any) -> MagicMock:
            if args:
                captured_argv.append(list(args[0]))
            return MagicMock(returncode=0, stdout="", stderr="")

        now_str = "2025:01:01 12:00:00"
        with patch("subprocess.run", side_effect=_run):
            n._inject_neutral_metadata(target, now_str, "")

        assert any(f"-DateTimeOriginal={now_str}" in argv for argv in captured_argv)

    @pytest.mark.parametrize(
        "forbidden_tag", ["Make", "Model", "GPSLatitude", "GPSLongitude"]
    )
    def test_inject_does_not_contain_forbidden_tags(
        self, tmp_path: Path, default_cfg: M1Config, forbidden_tag: str
    ) -> None:
        """Forbidden device/GPS tags must NEVER appear in ExifTool inject argv."""
        target = tmp_path / "video.mp4"
        target.write_bytes(b"\x00")

        with patch("shutil.which", return_value="/usr/bin/exiftool"):
            n = M1Normalizer(default_cfg)

        captured_argv: list[list[str]] = []

        def _run(*args: Any, **kwargs: Any) -> MagicMock:
            if args:
                captured_argv.append(list(args[0]))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=_run):
            n._inject_neutral_metadata(target, "2025:01:01 00:00:00", "")

        for argv in captured_argv:
            for arg in argv:
                assert (
                    forbidden_tag not in arg
                ), f"Forbidden tag '{forbidden_tag}' found in exiftool argv: {argv}"

    def test_inject_includes_comment_when_provided(
        self, tmp_path: Path, default_cfg: M1Config
    ) -> None:
        """When user_comment is set, -Comment=<value> appears in ExifTool argv."""
        target = tmp_path / "video.mp4"
        target.write_bytes(b"\x00")

        with patch("shutil.which", return_value="/usr/bin/exiftool"):
            n = M1Normalizer(default_cfg)

        captured_argv: list[list[str]] = []

        def _run(*args: Any, **kwargs: Any) -> MagicMock:
            if args:
                captured_argv.append(list(args[0]))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=_run):
            n._inject_neutral_metadata(target, "2025:01:01 00:00:00", "Stream prep")

        assert any("-Comment=Stream prep" in argv for argv in captured_argv)


# ---------------------------------------------------------------------------
# Test: C2PA detection
# ---------------------------------------------------------------------------


class TestC2PADetection:
    def test_c2pa_detected_when_tags_present(
        self, tmp_path: Path, default_cfg: M1Config
    ) -> None:
        """_detect_c2pa returns True when ExifTool reports C2PA tags."""
        target = tmp_path / "video.mp4"
        target.write_bytes(b"\x00")

        with patch("shutil.which", return_value="/usr/bin/exiftool"):
            n = M1Normalizer(default_cfg)

        c2pa_json = json.dumps([{"SourceFile": str(target), "C2PA:Claim": "abc123"}])
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout=c2pa_json, stderr=""),
        ):
            result = n._detect_c2pa(target)

        assert result is True

    def test_c2pa_not_detected_when_tags_absent(
        self, tmp_path: Path, default_cfg: M1Config
    ) -> None:
        """_detect_c2pa returns False when ExifTool returns only SourceFile."""
        target = tmp_path / "video.mp4"
        target.write_bytes(b"\x00")

        with patch("shutil.which", return_value="/usr/bin/exiftool"):
            n = M1Normalizer(default_cfg)

        c2pa_json = json.dumps([{"SourceFile": str(target)}])
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout=c2pa_json, stderr=""),
        ):
            result = n._detect_c2pa(target)

        assert result is False

    def test_c2pa_not_detected_on_empty_json(
        self, tmp_path: Path, default_cfg: M1Config
    ) -> None:
        """_detect_c2pa returns False on empty exiftool output."""
        target = tmp_path / "video.mp4"
        target.write_bytes(b"\x00")

        with patch("shutil.which", return_value="/usr/bin/exiftool"):
            n = M1Normalizer(default_cfg)

        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="[]", stderr=""),
        ):
            result = n._detect_c2pa(target)

        assert result is False


# ---------------------------------------------------------------------------
# Test: SHA-256 helper
# ---------------------------------------------------------------------------


class TestSHA256Helper:
    def test_sha256_known_content(self, tmp_path: Path) -> None:
        """_sha256_file returns the correct digest for known content."""
        f = tmp_path / "data.bin"
        content = b"liveguard test content"
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert _sha256_file(f) == expected

    def test_sha256_empty_file(self, tmp_path: Path) -> None:
        """_sha256_file handles empty files correctly."""
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert _sha256_file(f) == expected


# ---------------------------------------------------------------------------
# Test: FFmpeg failure handling
# ---------------------------------------------------------------------------


class TestFFmpegFailure:
    def test_nonzero_ffmpeg_exit_raises_external_dep_error(
        self, tmp_path: Path, default_cfg: M1Config
    ) -> None:
        """ExternalDependencyError raised when FFmpeg exits non-zero."""
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            n = M1Normalizer(default_cfg)

        inp = _make_tmp_video(tmp_path)
        log_file = tmp_path / "ff.log"
        log_file.write_text("error: codec not found")

        def _run(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=1)

        with (
            patch("subprocess.run", side_effect=_run),
            patch("tempfile.mkstemp", return_value=(99, str(log_file))),
            patch("os.open", return_value=99),
            patch(
                "builtins.open",
                MagicMock(
                    return_value=MagicMock(
                        __enter__=MagicMock(return_value=MagicMock()),
                        __exit__=MagicMock(return_value=False),
                    )
                ),
            ),
        ):
            with pytest.raises(ExternalDependencyError) as exc_info:
                asyncio.run(n.process(inp, tmp_path / "out.mp4"))
        assert exc_info.value.exit_code == 5

    def test_ffmpeg_timeout_raises_external_dep_error(
        self, tmp_path: Path, default_cfg: M1Config
    ) -> None:
        """ExternalDependencyError raised when FFmpeg subprocess times out."""
        import subprocess

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            n = M1Normalizer(default_cfg, timeout_s=1)

        inp = _make_tmp_video(tmp_path)
        log_file = tmp_path / "ff.log"
        log_file.write_bytes(b"")

        def _run(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

        with (
            patch("subprocess.run", side_effect=_run),
            patch("tempfile.mkstemp", return_value=(99, str(log_file))),
            patch(
                "builtins.open",
                MagicMock(
                    return_value=MagicMock(
                        __enter__=MagicMock(return_value=MagicMock()),
                        __exit__=MagicMock(return_value=False),
                    )
                ),
            ),
        ):
            with pytest.raises(ExternalDependencyError) as exc_info:
                asyncio.run(n.process(inp, tmp_path / "out.mp4"))
        assert exc_info.value.exit_code == 5
        assert "timed out" in exc_info.value.message.lower()
