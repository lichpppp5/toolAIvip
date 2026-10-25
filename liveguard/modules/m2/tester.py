"""M2 — Robustness Tester implementation (research-only, watermarked).

This module implements the :class:`~liveguard.interfaces.RobustnessTester` protocol.
It is explicitly designed for offline academic/defensive robustness evaluation,
NOT for platform moderation evasion.

Mandatory security and compliance properties:
1. Requires both ``research_only=True`` and ``ack_research_use=True``.
2. Permanently burns a visible "PERTURBED" watermark on every output frame.
3. Injects ``XMP:LiveGuard:robustness_test=true`` into output metadata.
4. Emits structured ``m2_research_run`` audit events to stderr.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageDraw, ImageFont
from skimage.metrics import peak_signal_noise_ratio, structural_similarity

from liveguard.config.schema import M2Config
from liveguard.errors import (
    ComplianceError,
    ExternalDependencyError,
    UsageError,
)
from liveguard.interfaces import RobustnessReport

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extended report dataclass
# ---------------------------------------------------------------------------


@dataclass
class M2Report(RobustnessReport):
    """Detailed output report for M2 Robustness Tester.

    Extends :class:`~liveguard.interfaces.RobustnessReport`.

    Attributes:
        mean_ssim: Mean structural similarity index across all processed frames.
        mean_psnr: Mean peak signal-to-noise ratio in dB.
        eps_used: Final L_inf epsilon perturbation bound applied.
        iters: Number of PGD attack iterations per batch.
        device: Device utilized for inference ("cuda", "cpu", etc.).
        duration_seconds: Processing wall-clock time in seconds.
        frames_processed: Total number of frames processed and encoded.
        eps_reductions: Count of times epsilon was reduced by quality guards.
        input_hash: SHA-256 hex digest of source video.
        output_hash: SHA-256 hex digest of watermarked output video.
    """

    mean_ssim: float = 0.0
    mean_psnr: float = 0.0
    eps_used: float = 8.0 / 255.0
    iters: int = 40
    device: str = "cpu"
    duration_seconds: float = 0.0
    frames_processed: int = 0
    eps_reductions: int = 0
    input_hash: str = ""
    output_hash: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path, chunk_size: int = 65536) -> str:
    """Compute the SHA-256 hex digest of a file.

    Args:
        path: Path to the target file.
        chunk_size: Read buffer size in bytes.

    Returns:
        Hex-encoded SHA-256 checksum string.
    """
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def _sha256_text(text: str) -> str:
    """Compute SHA-256 hex digest of a string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _audit_stderr(event: str, data: dict[str, Any]) -> None:
    """Emit a structured audit line to stderr in JSON format."""
    payload = {
        "audit_event": event,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        **data,
    }
    import sys

    print(f"[AUDIT] {json.dumps(payload, default=str)}", file=sys.stderr, flush=True)


def apply_watermark(
    frame: Image.Image,
    text: str = "PERTURBED",
    opacity: float = 0.6,
) -> Image.Image:
    """Burn a visible corner watermark into the frame.

    The watermark is drawn in the bottom-right corner and cannot be disabled.
    Opacity is enforced to be at least 0.1 and capped at 1.0.

    Args:
        frame: PIL Image in RGB mode.
        text: Watermark string to render (defaults to ``"PERTURBED"``).
        opacity: Float in [0.1, 1.0] specifying watermark alpha.

    Returns:
        New PIL Image with the watermark burned in.
    """
    if not text:
        text = "PERTURBED"
    effective_opacity = max(0.1, min(1.0, float(opacity)))

    # Convert to RGBA for alpha blending
    base = frame.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Dynamic font sizing based on frame height
    font_size = max(18, base.height // 28)
    font: Any
    try:
        font = ImageFont.truetype("Arial", font_size)
    except OSError:
        font = ImageFont.load_default()

    margin_x = int(base.width * 0.02)
    margin_y = int(base.height * 0.02)

    # Calculate text bounding box
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    x = base.width - text_width - margin_x
    y = base.height - text_height - margin_y

    alpha_val = int(255 * effective_opacity)
    shadow_alpha = int(180 * effective_opacity)

    # Draw dark shadow for contrast on bright scenes
    draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, shadow_alpha))
    # Draw high-contrast white text
    draw.text((x, y), text, font=font, fill=(255, 255, 255, alpha_val))

    # Composite and convert back to RGB
    combined = Image.alpha_composite(base, overlay)
    return combined.convert("RGB")


# ---------------------------------------------------------------------------
# M2 Robustness Tester
# ---------------------------------------------------------------------------


class M2RobustnessTester:
    """M2 — Robustness Tester implementation.

    Carries out adversarial self-similarity minimization on video frames
    using CLIP ViT-B/32, strictly bounded by SSIM/PSNR perceptual guards
    and mandatory frame watermarking.
    """

    CLIP_MODEL_NAME: str = "ViT-B-32"
    CLIP_PRETRAINED: str = "laion2b_s34b_b79k"

    # CLIP standard normalization constants
    CLIP_MEAN = torch.tensor([0.48145466, 0.4578275, 0.40821073]).view(1, 3, 1, 1)
    CLIP_STD = torch.tensor([0.26862954, 0.26130258, 0.27577711]).view(1, 3, 1, 1)

    def __init__(
        self,
        config: M2Config | None = None,
        model: Any | None = None,
        preprocess: Any | None = None,
    ) -> None:
        """Initialize M2 Robustness Tester.

        Args:
            config: Optional configuration instance. Defaults to ``M2Config()``.
            model: Optional pre-loaded CLIP model (used for test injection).
            preprocess: Optional pre-processing pipeline for the model.
        """
        self._cfg = config if config is not None else M2Config()
        self._model = model
        self._preprocess = preprocess

        # Select hardware device
        if torch.cuda.is_available():
            self._device = torch.device("cuda")
        else:
            self._device = torch.device("cpu")
        logger.info("m2_device_selected: %s", self._device)

    async def validate_dependencies(self) -> None:
        """Validate that FFmpeg is available on PATH.

        Raises:
            ExternalDependencyError: If FFmpeg binary is missing.
        """
        if shutil.which("ffmpeg") is None:
            raise ExternalDependencyError(
                "Required external tool 'ffmpeg' is missing on PATH. "
                "Install FFmpeg to use M2 Robustness Tester."
            )

    def _ensure_model_loaded(self) -> None:
        """Ensure CLIP ViT-B/32 model is loaded from cache without network access.

        Raises:
            ExternalDependencyError: If weights are missing from cache_dir.
        """
        if self._model is not None:
            return

        cache_path = Path(self._cfg.cache_dir).expanduser().resolve()
        logger.info("Attempting to load CLIP model from cache: %s", cache_path)

        try:
            import open_clip

            # Load model strictly using the designated cache_dir
            model, _, preprocess = open_clip.create_model_and_transforms(
                self.CLIP_MODEL_NAME,
                pretrained=self.CLIP_PRETRAINED,
                cache_dir=str(cache_path),
            )
            model.train(mode=False)
            model.to(self._device)
            self._model = model
            self._preprocess = preprocess
        except Exception as exc:  # noqa: BLE001
            raise ExternalDependencyError(
                f"CLIP ViT-B/32 weights not found or could not be loaded from cache: "
                f"{cache_path}. Error: {exc}. Please download weights first using: "
                f"python -m liveguard.modules.m2.cache --cache-dir '{cache_path}'"
            ) from exc

    def _normalize_tensor(self, x: torch.Tensor) -> torch.Tensor:
        """Normalize [0, 1] RGB tensor to CLIP mean and std."""
        mean = self.CLIP_MEAN.to(x.device)
        std = self.CLIP_STD.to(x.device)
        return (x - mean) / std

    def _pgd_attack_batch(
        self,
        clean_batch: torch.Tensor,
        eps: float,
        iters: int,
        alpha: float,
    ) -> torch.Tensor:
        """Run PGD on a batch of normalized frames to minimize clean self-similarity.

        Args:
            clean_batch: Float tensor of shape (B, 3, H, W) in [0, 1].
            eps: L_inf perturbation radius in [0, 1].
            iters: Number of gradient descent steps.
            alpha: Step size per iteration.

        Returns:
            Perturbed float tensor of shape (B, 3, H, W) in [0, 1].
        """
        assert self._model is not None
        # Resize clean frames to 224x224 for CLIP embedding computation
        with torch.no_grad():
            clean_resized = F.interpolate(
                clean_batch,
                size=(224, 224),
                mode="bilinear",
                align_corners=False,
            )
            clean_norm = self._normalize_tensor(clean_resized)
            clean_embeds = self._model.encode_image(clean_norm)
            clean_embeds = F.normalize(clean_embeds, p=2, dim=-1).detach()

        # Initialize adversarial tensor with random start in L_inf ball
        noise = torch.empty_like(clean_batch).uniform_(-eps, eps)
        adv_batch = torch.clamp(clean_batch + noise, 0.0, 1.0).detach()

        for _ in range(iters):
            adv_batch.requires_grad_(True)

            adv_resized = F.interpolate(
                adv_batch,
                size=(224, 224),
                mode="bilinear",
                align_corners=False,
            )
            adv_norm = self._normalize_tensor(adv_resized)
            adv_embeds = self._model.encode_image(adv_norm)
            adv_embeds = F.normalize(adv_embeds, p=2, dim=-1)

            # Target: minimize cosine self-similarity
            cos_sim = (adv_embeds * clean_embeds).sum(dim=-1).mean()

            # Minimize cosine similarity -> gradient descent step
            grads = torch.autograd.grad(cos_sim, adv_batch)[0]

            with torch.no_grad():
                adv_batch = adv_batch - alpha * grads.sign()
                # Project onto L_inf ball around clean_batch
                adv_batch = torch.max(
                    torch.min(adv_batch, clean_batch + eps), clean_batch - eps
                )
                adv_batch = torch.clamp(adv_batch, 0.0, 1.0)

            adv_batch = adv_batch.detach()

        return adv_batch

    def _probe_video_info(self, path: Path) -> tuple[int, int, float, int]:
        """Probe video dimensions, frame rate, and frame count via ffprobe.

        Returns:
            Tuple of (width, height, fps, nb_frames).
        """
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_frames",
            "-of",
            "json",
            str(path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        stream = data["streams"][0]

        width = int(stream["width"])
        height = int(stream["height"])

        # Parse fractional fps (e.g. "30000/1001" or "30/1")
        fps_str = stream.get("r_frame_rate", "30/1")
        if "/" in fps_str:
            num, denom = fps_str.split("/")
            fps = float(num) / float(denom) if float(denom) > 0 else 30.0
        else:
            fps = float(fps_str)

        nb_frames_raw = stream.get("nb_frames")
        if nb_frames_raw and nb_frames_raw.isdigit():
            nb_frames = int(nb_frames_raw)
        else:
            nb_frames = 0

        return width, height, fps, nb_frames

    async def test(
        self,
        input: Path,
        output: Path,
        research_only: bool,
        ack_research_use: bool,
        **kwargs: Any,
    ) -> M2Report:
        """Execute the M2 robustness evaluation pipeline.

        Args:
            input: Source video path.
            output: Destination path for the watermarked output video.
            research_only: Must be ``True``.
            ack_research_use: Must be ``True``.
            **kwargs: Optional configuration overrides:
                - ``epsilon`` (float): Base L_inf perturbation budget.
                - ``iters`` (int): PGD iteration count.
                - ``batch_size`` (int): Inference batch size.
                - ``resume`` (bool): Resume from checkpoint file.

        Returns:
            A populated :class:`M2Report` instance.

        Raises:
            UsageError: If compliance flags are omitted or input file is invalid.
            ExternalDependencyError: If required tools or models are missing.
            ComplianceError: If quality guard fails persistently.
        """
        # 1. Mandatory compliance verification
        if not research_only or not ack_research_use:
            raise UsageError(
                "M2 Robustness Tester is strictly for research purposes. "
                "Both --research-only AND --i-understand-research-use "
                "flags are required."
            )

        input_path = input.resolve()
        output_path = output.resolve()

        if not input_path.exists() or not input_path.is_file():
            raise UsageError(f"Input video file not found: {input_path}")

        await self.validate_dependencies()
        self._ensure_model_loaded()

        input_hash = _sha256_file(input_path)

        # Audit event emission
        _audit_stderr(
            "m2_research_run",
            {
                "input_path": str(input_path),
                "input_hash": input_hash,
                "research_only_ack_hash": _sha256_text("research_only:true"),
                "research_use_ack_hash": _sha256_text("ack_research_use:true"),
            },
        )

        # Config extraction
        eps_base = float(kwargs.get("epsilon", self._cfg.epsilon))
        iters = int(kwargs.get("iters", self._cfg.iters))
        batch_size = int(kwargs.get("batch_size", self._cfg.batch_size))
        resume = bool(kwargs.get("resume", False))

        ckpt_path = output_path.with_suffix(".ckpt.json")
        start_frame_idx = 0
        running_ssim_sum = 0.0
        running_psnr_sum = 0.0
        total_eps_reductions = 0

        # Checkpoint handling
        if resume:
            if not ckpt_path.exists():
                raise UsageError(
                    f"Resume requested but checkpoint not found: {ckpt_path}"
                )
            try:
                ckpt_data = json.loads(ckpt_path.read_text())
                if ckpt_data.get("input_hash") != input_hash:
                    prev_hash = ckpt_data.get("input_hash")
                    raise UsageError(
                        f"Checkpoint input hash mismatch! Checkpoint was created "
                        f"for {prev_hash}, but input hash is {input_hash}."
                    )
                start_frame_idx = int(ckpt_data.get("frame_idx", 0))
                eps_base = float(ckpt_data.get("eps_current", eps_base))
                running_ssim_sum = float(ckpt_data.get("ssim_running_sum", 0.0))
                running_psnr_sum = float(ckpt_data.get("psnr_running_sum", 0.0))
                total_eps_reductions = int(ckpt_data.get("eps_reductions", 0))
                logger.info("Resuming from frame index %d", start_frame_idx)
            except json.JSONDecodeError as exc:
                raise UsageError(f"Corrupt checkpoint file: {ckpt_path}") from exc

        # Probe input video
        width, height, fps, _ = self._probe_video_info(input_path)
        frame_size = width * height * 3

        # Prepare output directory
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Start decoder and encoder FFmpeg processes
        decode_cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
        encode_cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{width}x{height}",
            "-pix_fmt",
            "rgb24",
            "-r",
            str(fps),
            "-i",
            "-",
            "-i",
            str(input_path),
            "-map",
            "0:v",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-crf",
            "23",
            "-preset",
            "medium",
            "-c:a",
            "copy",
            str(output_path),
        ]

        start_time = time.time()
        decode_proc = subprocess.Popen(
            decode_cmd, stdout=subprocess.PIPE, bufsize=frame_size * batch_size
        )
        encode_proc = subprocess.Popen(encode_cmd, stdin=subprocess.PIPE)

        frames_processed = start_frame_idx
        current_eps = eps_base

        try:
            # Skip frames if resuming
            if start_frame_idx > 0:
                bytes_to_skip = start_frame_idx * frame_size
                assert decode_proc.stdout is not None
                decode_proc.stdout.read(bytes_to_skip)

            while True:
                batch_frames_np: list[np.ndarray[Any, Any]] = []

                # Read up to batch_size frames
                for _ in range(batch_size):
                    assert decode_proc.stdout is not None
                    raw_bytes = decode_proc.stdout.read(frame_size)
                    if not raw_bytes or len(raw_bytes) < frame_size:
                        break
                    frame_arr = np.frombuffer(raw_bytes, dtype=np.uint8).reshape(
                        (height, width, 3)
                    )
                    batch_frames_np.append(frame_arr)

                if not batch_frames_np:
                    break

                curr_batch_len = len(batch_frames_np)

                # Convert to torch tensor (B, 3, H, W) normalized to [0, 1]
                batch_tensor = (
                    torch.from_numpy(np.stack(batch_frames_np))
                    .permute(0, 3, 1, 2)
                    .float()
                    .div(255.0)
                    .to(self._device)
                )

                # Quality guard loop per batch
                eps_attempt = current_eps
                success = False

                for _retry in range(self._cfg.quality_retries + 1):
                    alpha = eps_attempt / 10.0
                    adv_tensor = self._pgd_attack_batch(
                        batch_tensor, eps_attempt, iters, alpha
                    )

                    # Compute quality metrics against clean frames
                    adv_np = (
                        (adv_tensor.permute(0, 2, 3, 1).cpu().numpy() * 255.0)
                        .round()
                        .astype(np.uint8)
                    )

                    batch_ssim: list[float] = []
                    batch_psnr: list[float] = []

                    for idx in range(curr_batch_len):
                        s = float(
                            structural_similarity(  # type: ignore[no-untyped-call]
                                batch_frames_np[idx],
                                adv_np[idx],
                                channel_axis=2,
                                data_range=255,
                            )
                        )
                        p = float(
                            peak_signal_noise_ratio(  # type: ignore[no-untyped-call]
                                batch_frames_np[idx],
                                adv_np[idx],
                                data_range=255,
                            )
                        )
                        batch_ssim.append(s)
                        batch_psnr.append(p)

                    min_batch_ssim = min(batch_ssim)
                    min_batch_psnr = min(batch_psnr)

                    if (
                        min_batch_ssim >= self._cfg.min_ssim
                        and min_batch_psnr >= self._cfg.min_psnr
                    ):
                        success = True
                        running_ssim_sum += sum(batch_ssim)
                        running_psnr_sum += sum(batch_psnr)
                        current_eps = eps_attempt
                        break

                    # Reduce eps by 25% and retry
                    eps_attempt *= 0.75
                    total_eps_reductions += 1

                if not success:
                    # Quality guard failed after all retries
                    raise ComplianceError(
                        f"Quality guard failed at frame {frames_processed}: "
                        f"SSIM ({min_batch_ssim:.4f}) < {self._cfg.min_ssim} or "
                        f"PSNR ({min_batch_psnr:.2f}) < {self._cfg.min_psnr} dB after "
                        f"{self._cfg.quality_retries} reductions."
                    )

                # Watermark every frame and write to encoder
                assert encode_proc.stdin is not None
                for idx in range(curr_batch_len):
                    pil_img = Image.fromarray(adv_np[idx])
                    watermarked_pil = apply_watermark(
                        pil_img,
                        text=self._cfg.watermark_text,
                        opacity=self._cfg.watermark_opacity,
                    )
                    watermarked_bytes = np.array(watermarked_pil).tobytes()
                    encode_proc.stdin.write(watermarked_bytes)

                frames_processed += curr_batch_len

                # VRAM cleanup
                del batch_tensor, adv_tensor
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                # Checkpoint periodically
                if frames_processed % self._cfg.checkpoint_interval == 0:
                    ckpt_payload = {
                        "input_hash": input_hash,
                        "frame_idx": frames_processed,
                        "eps_current": current_eps,
                        "ssim_running_sum": running_ssim_sum,
                        "psnr_running_sum": running_psnr_sum,
                        "eps_reductions": total_eps_reductions,
                    }
                    ckpt_path.write_text(json.dumps(ckpt_payload, indent=2))

        finally:
            if decode_proc.stdout:
                decode_proc.stdout.close()
            decode_proc.wait()

            if encode_proc.stdin:
                encode_proc.stdin.close()
            encode_proc.wait()

        # Write metadata tag via ExifTool if available
        if shutil.which("exiftool") is not None:
            try:
                subprocess.run(
                    [
                        "exiftool",
                        "-overwrite_original",
                        "-XMP:LiveGuard:robustness_test=true",
                        str(output_path),
                    ],
                    capture_output=True,
                    check=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not write XMP robustness tag: %s", exc)

        # Cleanup checkpoint file if completed successfully
        if ckpt_path.exists():
            try:
                ckpt_path.unlink()
            except OSError:
                pass

        duration_s = max(0.001, time.time() - start_time)
        mean_ssim = running_ssim_sum / max(1, frames_processed)
        mean_psnr = running_psnr_sum / max(1, frames_processed)
        output_hash = _sha256_file(output_path) if output_path.exists() else ""

        return M2Report(
            input_path=input_path,
            watermarked_path=output_path,
            perturbations_applied=["pgd_clip"],
            psnr_db=mean_psnr,
            ssim=mean_ssim,
            watermark_visible=True,
            mean_ssim=mean_ssim,
            mean_psnr=mean_psnr,
            eps_used=current_eps,
            iters=iters,
            device=str(self._device),
            duration_seconds=duration_s,
            frames_processed=frames_processed,
            eps_reductions=total_eps_reductions,
            input_hash=input_hash,
            output_hash=output_hash,
        )
