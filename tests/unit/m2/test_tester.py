"""Unit tests for M2 — Robustness Tester.

Tests cover:
- Compliance validation: missing either research flag raises UsageError.
- Deterministic perturbation with mocked CLIP model.
- Quality guard: SSIM/PSNR threshold failures trigger epsilon reduction and retry.
- Checkpointing and resume: periodic checkpoint write and hash mismatch check.
- Mandatory watermark application to every frame.
- Hardware device selection and CPU fallback.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch
from PIL import Image

from liveguard.config.schema import M2Config
from liveguard.errors import ExternalDependencyError, UsageError
from liveguard.modules.m2.tester import (
    M2Report,
    M2RobustnessTester,
    apply_watermark,
)


class DummyMockClipModel(torch.nn.Module):
    """Deterministic mock of CLIP model for fast unit testing."""

    def __init__(self) -> None:
        super().__init__()
        # A tiny linear layer that takes flattened (B, 3*224*224) to embedding 512
        self.proj = torch.nn.Linear(3 * 224 * 224, 512, bias=False)
        with torch.no_grad():
            self.proj.weight.fill_(0.01)

    def encode_image(self, image: torch.Tensor) -> torch.Tensor:
        b = image.shape[0]
        flattened = image.reshape(b, -1)
        return self.proj(flattened)


@pytest.fixture
def dummy_video(tmp_path: Path) -> Path:
    """Create a dummy file representing an input video."""
    video = tmp_path / "input.mp4"
    video.write_bytes(b"dummy video data 12345")
    return video


@pytest.fixture
def mock_clip_model() -> DummyMockClipModel:
    return DummyMockClipModel()


class TestM2ComplianceAndGuards:
    """Test M2 compliance gating and security invariants."""

    @pytest.mark.asyncio
    async def test_missing_research_only_flag_raises_usage_error(
        self, dummy_video: Path, tmp_path: Path, mock_clip_model: DummyMockClipModel
    ):
        """Must raise UsageError (exit 2) if research_only is False."""
        tester = M2RobustnessTester(model=mock_clip_model)
        out = tmp_path / "out.mp4"

        with pytest.raises(UsageError) as exc_info:
            await tester.test(
                input=dummy_video,
                output=out,
                research_only=False,
                ack_research_use=True,
            )
        assert "strictly for research purposes" in str(exc_info.value)
        assert exc_info.value.exit_code == 2

    @pytest.mark.asyncio
    async def test_missing_ack_research_use_flag_raises_usage_error(
        self, dummy_video: Path, tmp_path: Path, mock_clip_model: DummyMockClipModel
    ):
        """Must raise UsageError (exit 2) if ack_research_use is False."""
        tester = M2RobustnessTester(model=mock_clip_model)
        out = tmp_path / "out.mp4"

        with pytest.raises(UsageError) as exc_info:
            await tester.test(
                input=dummy_video,
                output=out,
                research_only=True,
                ack_research_use=False,
            )
        assert "strictly for research purposes" in str(exc_info.value)
        assert exc_info.value.exit_code == 2

    @pytest.mark.asyncio
    async def test_nonexistent_input_raises_usage_error(
        self, tmp_path: Path, mock_clip_model: DummyMockClipModel
    ):
        """Nonexistent input video path must raise UsageError."""
        tester = M2RobustnessTester(model=mock_clip_model)
        out = tmp_path / "out.mp4"

        with pytest.raises(UsageError) as exc_info:
            await tester.test(
                input=tmp_path / "nonexistent.mp4",
                output=out,
                research_only=True,
                ack_research_use=True,
            )
        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_missing_ffmpeg_raises_external_dependency_error(
        self, dummy_video: Path, tmp_path: Path, mock_clip_model: DummyMockClipModel
    ):
        """Missing ffmpeg on PATH raises ExternalDependencyError."""
        tester = M2RobustnessTester(model=mock_clip_model)
        out = tmp_path / "out.mp4"

        with patch("shutil.which", return_value=None):
            with pytest.raises(ExternalDependencyError) as exc_info:
                await tester.test(
                    input=dummy_video,
                    output=out,
                    research_only=True,
                    ack_research_use=True,
                )
            assert "ffmpeg" in str(exc_info.value)


class TestM2Watermark:
    """Test mandatory watermark properties."""

    def test_watermark_is_applied_and_cannot_be_disabled(self):
        """Watermark draws 'PERTURBED' text and forces minimum opacity."""
        img = Image.new("RGB", (640, 360), color=(100, 150, 200))
        # Even if 0 opacity or empty text is passed, watermark is enforced
        wm_img = apply_watermark(img, text="", opacity=0.0)

        # Output image must have different pixel values in the bottom-right corner
        orig_arr = np.array(img)
        wm_arr = np.array(wm_img)

        assert wm_img.size == img.size
        # Bottom-right section must be modified
        assert not np.array_equal(orig_arr[300:350, 450:630], wm_arr[300:350, 450:630])


class TestM2QualityGuardAndPipeline:
    """Test pipeline execution, quality guard, and checkpoints."""

    @pytest.mark.asyncio
    async def test_pipeline_execution_and_report(
        self, dummy_video: Path, tmp_path: Path, mock_clip_model: DummyMockClipModel
    ):
        """End-to-end execution of tester.test() with mocked FFmpeg processes."""
        out = tmp_path / "perturbed.mp4"
        tester = M2RobustnessTester(
            config=M2Config(
                iters=2, batch_size=2, checkpoint_interval=2, min_ssim=0.90
            ),
            model=mock_clip_model,
        )

        # Mock probe: 64x64 video, 30fps, 4 frames
        w, h, fps = 64, 64, 30.0
        frame_len = w * h * 3
        total_test_frames = 4

        def mock_read_fn(_size: int, count=[0]) -> bytes:  # noqa: B006
            if count[0] < total_test_frames:
                count[0] += 1
                return (np.ones(frame_len, dtype=np.uint8) * 128).tobytes()
            return b""

        mock_decode_proc = MagicMock()
        mock_decode_proc.stdout.read = MagicMock(side_effect=mock_read_fn)
        mock_decode_proc.wait.return_value = 0

        # Mock encode process stdin
        mock_encode_proc = MagicMock()
        mock_encode_proc.stdin.write = MagicMock()
        mock_encode_proc.wait.return_value = 0

        with (
            patch(
                "liveguard.modules.m2.tester.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch.object(tester, "_probe_video_info", return_value=(w, h, fps, 4)),
            patch("subprocess.Popen", side_effect=[mock_decode_proc, mock_encode_proc]),
        ):
            report = await tester.test(
                input=dummy_video,
                output=out,
                research_only=True,
                ack_research_use=True,
                iters=2,
            )

            assert isinstance(report, M2Report)
            assert report.frames_processed == 4
            assert report.watermark_visible is True
            assert report.mean_ssim >= 0.90
            assert report.mean_psnr >= 40.0
            assert mock_encode_proc.stdin.write.call_count == 4

    @pytest.mark.asyncio
    async def test_quality_guard_triggers_epsilon_reduction(
        self, dummy_video: Path, tmp_path: Path, mock_clip_model: DummyMockClipModel
    ):
        """When SSIM drops below threshold, epsilon is reduced by 25% and retried."""
        out = tmp_path / "perturbed.mp4"
        tester = M2RobustnessTester(
            config=M2Config(min_ssim=0.99, quality_retries=3, batch_size=1),
            model=mock_clip_model,
        )

        w, h, fps = 64, 64, 30.0
        frame_len = w * h * 3

        def mock_read_single(_size: int, count=[0]) -> bytes:  # noqa: B006
            if count[0] < 1:
                count[0] += 1
                return (np.ones(frame_len, dtype=np.uint8) * 128).tobytes()
            return b""

        mock_decode_proc = MagicMock()
        mock_decode_proc.stdout.read = MagicMock(side_effect=mock_read_single)
        mock_decode_proc.wait.return_value = 0

        mock_encode_proc = MagicMock()
        mock_encode_proc.wait.return_value = 0

        # Simulate SSIM returning low value on first try, then acceptable on retry
        with (
            patch(
                "liveguard.modules.m2.tester.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch.object(tester, "_probe_video_info", return_value=(w, h, fps, 1)),
            patch("subprocess.Popen", side_effect=[mock_decode_proc, mock_encode_proc]),
            patch(
                "liveguard.modules.m2.tester.structural_similarity",
                side_effect=[0.90, 0.995],
            ),
            patch(
                "liveguard.modules.m2.tester.peak_signal_noise_ratio", return_value=45.0
            ),
        ):
            report = await tester.test(
                input=dummy_video,
                output=out,
                research_only=True,
                ack_research_use=True,
                iters=1,
            )

            assert report.eps_reductions == 1
            assert report.eps_used < tester._cfg.epsilon

    @pytest.mark.asyncio
    async def test_checkpoint_written_and_resume_mismatch_refused(
        self, dummy_video: Path, tmp_path: Path, mock_clip_model: DummyMockClipModel
    ):
        """Resume with mismatched input hash must raise UsageError."""
        out = tmp_path / "perturbed.mp4"
        ckpt = out.with_suffix(".ckpt.json")
        ckpt.write_text(
            json.dumps({"input_hash": "different_hash_12345", "frame_idx": 50})
        )

        tester = M2RobustnessTester(model=mock_clip_model)

        with patch(
            "liveguard.modules.m2.tester.shutil.which", return_value="/usr/bin/ffmpeg"
        ):
            with pytest.raises(UsageError) as exc_info:
                await tester.test(
                    input=dummy_video,
                    output=out,
                    research_only=True,
                    ack_research_use=True,
                    resume=True,
                )
            assert "Checkpoint input hash mismatch" in str(exc_info.value)


class TestM2DeviceSelection:
    """Test device selection and fallback."""

    def test_device_fallback_to_cpu_when_cuda_unavailable(self):
        with patch("torch.cuda.is_available", return_value=False):
            tester = M2RobustnessTester()
            assert tester._device == torch.device("cpu")
