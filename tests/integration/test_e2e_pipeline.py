r"""End-to-end pipeline integration tests for LiveGuard (M1 and M2)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import torch

from liveguard.config.schema import M1Config, M2Config
from liveguard.modules.m1.normalizer import M1Normalizer
from liveguard.modules.m2.tester import M2RobustnessTester

FIXTURE_PATH = Path("tests/fixtures/sample_5s.mp4")


def _probe_streams(path: Path) -> list[dict[str, Any]]:
    """Probe audio and video streams using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    return data.get("streams", [])  # type: ignore[no-any-return]


def _sha256(path: Path) -> str:
    """Calculate sha256 checksum of file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class _DummyClipModel(torch.nn.Module):
    """Deterministic dummy CLIP model for mock testing."""

    def encode_image(self, x: torch.Tensor) -> torch.Tensor:
        feats = x.mean(dim=(2, 3))
        feats512 = feats.repeat(1, 171)[:, :512]
        return feats512 / (feats512.norm(dim=-1, keepdim=True) + 1e-6)


@pytest.mark.asyncio
async def test_e2e_m1_normalizer_pipeline(tmp_path: Path) -> None:
    """Test M1 privacy normalization on sample_5s.mp4 end-to-end."""
    assert FIXTURE_PATH.is_file(), f"Fixture missing: {FIXTURE_PATH}"

    output_path = tmp_path / "m1_clean.mp4"
    config = M1Config(
        crf=22,
        preset="medium",
        strip_provenance=False,
    )
    normalizer = M1Normalizer(config)
    await normalizer.validate_dependencies()

    report = await normalizer.process(
        input=FIXTURE_PATH,
        output=output_path,
        strip_provenance=False,
    )

    # 1. Output exists and is non-empty
    assert output_path.is_file()
    assert output_path.stat().st_size > 0

    # 2. ffprobe reports 2 streams (video + audio)
    in_streams = _probe_streams(FIXTURE_PATH)
    out_streams = _probe_streams(output_path)
    assert len(out_streams) == 2

    # 3. Audio codec matches input stream copy
    in_audio = next(s for s in in_streams if s.get("codec_type") == "audio")
    out_audio = next(s for s in out_streams if s.get("codec_type") == "audio")
    assert in_audio.get("codec_name") == out_audio.get("codec_name")

    # 4. Input hash matches fixture SHA-256
    expected_hash = _sha256(FIXTURE_PATH)
    assert report.input_hash == expected_hash


@pytest.mark.asyncio
async def test_e2e_m2_robustness_pipeline(tmp_path: Path) -> None:
    """Test M2 robustness testing with research acknowledgement and watermark."""
    assert FIXTURE_PATH.is_file(), f"Fixture missing: {FIXTURE_PATH}"

    mock_model = os.environ.get("LIVEGUARD_TEST_MOCK_MODEL") == "1"
    skip_gpu = os.environ.get("CI_SKIP_GPU") == "1"
    has_cuda = torch.cuda.is_available()

    if not mock_model:
        if skip_gpu or not has_cuda:
            pytest.skip(
                "GPU required for full CLIP inference (or set LIVEGUARD_TEST_MOCK_MODEL=1)"
            )

    output_path = tmp_path / "m2_watermarked.mp4"
    config = M2Config(
        cache_dir=str(tmp_path / "cache"),
        device="cuda" if has_cuda and not mock_model else "cpu",
        eps=1.0 / 255.0,
        iters=5,
        batch_size=4,
    )

    tester = M2RobustnessTester(config)

    if mock_model:
        # Mock CLIP model creation with deterministic dummy
        def _dummy_factory(*args: Any, **kwargs: Any) -> tuple[Any, Any, Any]:
            dummy = _DummyClipModel()
            return dummy, None, lambda img: img

        with patch("open_clip.create_model_and_transforms", side_effect=_dummy_factory):
            report = await tester.test(
                input=FIXTURE_PATH,
                output=output_path,
                research_only=True,
                ack_research_use=True,
                epsilon=1.0 / 255.0,
                iters=5,
            )
    else:
        report = await tester.test(
            input=FIXTURE_PATH,
            output=output_path,
            research_only=True,
            ack_research_use=True,
            epsilon=1.0 / 255.0,
            iters=5,
        )

    # 1. Output exists with 2 streams
    assert output_path.is_file()
    out_streams = _probe_streams(output_path)
    assert len(out_streams) == 2

    # 2. Quality and watermark metrics
    assert report.mean_ssim > 0.95
    assert report.watermark_visible is True
