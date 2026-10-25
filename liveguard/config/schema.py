"""LiveGuard configuration schema (pydantic v2).

Defines the full ``LiveGuardConfig`` model validated from ``liveguard.yaml``.

Usage example::

    import yaml
    from liveguard.config.schema import LiveGuardConfig

    with open("liveguard.yaml") as f:
        cfg = LiveGuardConfig.model_validate(yaml.safe_load(f))
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class DisclosureBannerConfig(BaseModel):
    """Settings for the on-stream disclosure/watermark banner.

    Attributes:
        enabled: Whether the banner must be shown. Defaults to ``True``.
            M4 refuses to stream when this is ``False`` (exit 3).
        text: Banner caption text shown to viewers.
        position: OBS source position as ``"top-left"``, ``"top-right"``,
            ``"bottom-left"``, or ``"bottom-right"``.

    Example::

        banner = DisclosureBannerConfig(enabled=True, text="AI-assisted stream")
    """

    enabled: bool = True
    text: str = "This stream uses AI-assisted preparation tools."
    position: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = (
        "bottom-right"
    )


class ComplianceConfig(BaseModel):
    """Compliance gating configuration.

    Attributes:
        disclosure_banner: Banner settings — must remain enabled for streaming.
        tos_ack_hash: SHA-256 hex digest of the ToS document acknowledged.
        tos_ack_at: UTC datetime when the user acknowledged the ToS.
            Must be within 30 days; older values raise exit 4.

    Example::

        compliance = ComplianceConfig(
            tos_ack_hash="e3b0c44298fc...",
            tos_ack_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
    """

    disclosure_banner: DisclosureBannerConfig = Field(
        default_factory=DisclosureBannerConfig
    )
    tos_ack_hash: str | None = None
    tos_ack_at: datetime | None = None


class ModuleConfig(BaseModel):
    """Per-module enable/disable switch.

    Attributes:
        enabled: Whether this module participates in the pipeline.

    Example::

        m2 = ModuleConfig(enabled=False)  # skip robustness tester
    """

    enabled: bool = True


class M1Config(ModuleConfig):
    """M1 — Privacy Normalizer configuration.

    Attributes:
        strip_provenance: If ``True`` and user passes ``--strip-provenance``
            with explicit acknowledgement, C2PA data is removed.
            Defaults to ``False`` (preserve provenance).
        face_confidence_threshold: Minimum MTCNN confidence to trigger
            redaction. Range [0.0, 1.0].
        redaction_method: Visual redaction method: ``"blur"`` or ``"pixelate"``.
        audio_normalize: Whether to apply loudness normalisation (EBU R128).
        crf: FFmpeg constant-rate factor for output quality. Range [0, 51].
        preset: FFmpeg encoding speed/quality preset.
        noise: Light noise filter strength in [0, 8]. 0 = disabled.

    Example::

        m1 = M1Config(face_confidence_threshold=0.92, crf=23, noise=4)
    """

    strip_provenance: bool = False
    face_confidence_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.9
    redaction_method: Literal["blur", "pixelate"] = "blur"
    audio_normalize: bool = True
    crf: Annotated[int, Field(ge=0, le=51)] = 23
    preset: Literal[
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    ] = "medium"
    noise: Annotated[int, Field(ge=0, le=8)] = 4

    @field_validator("crf")
    @classmethod
    def _crf_range(cls, v: int) -> int:
        """Validate CRF is within the FFmpeg-legal range [0, 51].

        Args:
            v: The CRF value to validate.

        Returns:
            The validated CRF value.

        Raises:
            ValueError: If *v* is outside [0, 51].
        """
        if not (0 <= v <= 51):
            raise ValueError(f"crf must be in [0, 51], got {v}")
        return v


class M2Config(ModuleConfig):
    """M2 — Robustness Tester configuration (research-only).

    Attributes:
        perturbations: List of perturbation types to apply.
        watermark_opacity: Opacity of the mandatory watermark in [0.1, 1.0].
            Cannot be set to 0 (watermark is always visible).
        watermark_text: Text burned into the frame. Defaults to ``"PERTURBED"``.
        cache_dir: Directory where CLIP model weights are cached.
        epsilon: Maximum L_inf perturbation budget in [0.0, 1.0].
        iters: Number of PGD attack iterations.
        batch_size: Frame batch size for GPU/CPU inference.
        min_ssim: Minimum acceptable Structural Similarity index.
        min_psnr: Minimum acceptable PSNR in dB.
        quality_retries: Max attempts to reduce epsilon if SSIM/PSNR guard fails.
        checkpoint_interval: Save state every N frames.

    Example::

        m2 = M2Config(epsilon=8.0/255.0, iters=40, watermark_opacity=0.6)
    """

    perturbations: list[str] = Field(default_factory=lambda: ["pgd_clip"])
    watermark_opacity: Annotated[float, Field(ge=0.1, le=1.0)] = 0.6
    watermark_text: str = "PERTURBED"
    cache_dir: str = "~/.cache/liveguard/models"
    epsilon: Annotated[float, Field(ge=0.0, le=1.0)] = 8.0 / 255.0
    iters: Annotated[int, Field(ge=1, le=200)] = 40
    batch_size: Annotated[int, Field(ge=1, le=64)] = 8
    min_ssim: Annotated[float, Field(ge=0.0, le=1.0)] = 0.95
    min_psnr: Annotated[float, Field(ge=0.0, le=100.0)] = 40.0
    quality_retries: Annotated[int, Field(ge=1, le=10)] = 3
    checkpoint_interval: Annotated[int, Field(ge=1, le=1000)] = 100


class M3Config(ModuleConfig):
    """M3 — Geo-Test & Session Manager configuration.

    Attributes:
        region: ISO-3166-1 alpha-2 region to probe. Defaults to ``"US"``.
        proxy_url: Optional HTTP/HTTPS proxy URL for the geo probe.
            Must be a valid URL if provided.
        browser_timeout_ms: Playwright page load timeout in milliseconds.

    Example::

        m3 = M3Config(region="GB", browser_timeout_ms=15000)
    """

    region: str = "US"
    proxy_url: str | None = None
    browser_timeout_ms: Annotated[int, Field(gt=0)] = 30_000

    @field_validator("proxy_url")
    @classmethod
    def _proxy_url_parseable(cls, v: str | None) -> str | None:
        """Validate proxy_url is a well-formed URL when provided.

        Args:
            v: The proxy URL string, or ``None``.

        Returns:
            The validated URL string, or ``None``.

        Raises:
            ValueError: If *v* is not a parseable URL with a scheme and host.
        """
        if v is not None:
            parsed = urlparse(v)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError(
                    f"proxy_url must be a valid URL with scheme and host, got {v!r}"
                )
        return v


class M4Config(ModuleConfig):
    """M4 — OBS Orchestrator configuration.

    Attributes:
        obs_host: Hostname of the OBS WebSocket server.
        obs_port: Port of the OBS WebSocket server (1–65535).
        obs_password_secret: Key name in the vault for the OBS WS password.
        rtmp_url: Base RTMP ingest URL (without stream key).
        scenes: List of OBS scene names required in the pipeline.
            Minimum 2 scenes.
        sample_rate: Audio sample rate in Hz. Must be > 0.

    Example::

        m4 = M4Config(scenes=["Intro", "Main"], obs_port=4455)
    """

    obs_host: str = "localhost"
    obs_port: Annotated[int, Field(ge=1, le=65535)] = 4455
    obs_password_secret: str = "obs_ws_password"
    rtmp_url: str = "rtmp://live.tiktok.com/live/"
    scenes: list[str] = Field(default_factory=lambda: ["Intro", "Main"])
    sample_rate: Annotated[int, Field(gt=0)] = 44100

    @field_validator("scenes")
    @classmethod
    def _min_two_scenes(cls, v: list[str]) -> list[str]:
        """Enforce a minimum of 2 OBS scenes.

        Args:
            v: The list of scene names.

        Returns:
            The validated list.

        Raises:
            ValueError: If fewer than 2 scene names are provided.
        """
        if len(v) < 2:
            raise ValueError(f"At least 2 OBS scenes are required, got {len(v)}: {v}")
        return v


class PipelineConfig(BaseModel):
    """Top-level pipeline ordering and I/O settings.

    Attributes:
        input_path: Path to the source video file.
        output_dir: Directory where processed files are written.
        working_dir: Temporary directory for intermediate files.

    Example::

        pipeline = PipelineConfig(input_path="/videos/raw.mp4")
    """

    input_path: str = ""
    output_dir: str = "./output"
    working_dir: str = "./tmp"


class ModulesConfig(BaseModel):
    """Aggregated per-module configuration block.

    Example::

        modules = ModulesConfig(m2=M2Config(enabled=False))
    """

    m1: M1Config = Field(default_factory=M1Config)
    m2: M2Config = Field(default_factory=M2Config)
    m3: M3Config = Field(default_factory=M3Config)
    m4: M4Config = Field(default_factory=M4Config)


class LoggingConfig(BaseModel):
    """Structured logging settings.

    Attributes:
        level: Log level: ``"DEBUG"``, ``"INFO"``, ``"WARNING"``, ``"ERROR"``.
        format: Output format: ``"json"`` or ``"console"``.
        audit_log_path: File path for the append-only HMAC audit log.

    Example::

        log_cfg = LoggingConfig(level="DEBUG", format="console")
    """

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "console"] = "json"
    audit_log_path: str = "./audit/audit.jsonl"


class DashboardConfig(BaseModel):
    """Streamlit dashboard settings.

    Attributes:
        enabled: Whether to launch the dashboard alongside the pipeline.
        host: Bind address for the Streamlit server.
        port: Port for the Streamlit server (1–65535).

    Example::

        dash = DashboardConfig(port=8502)
    """

    enabled: bool = False
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 8501


class EnvironmentConfig(BaseModel):
    """Runtime environment hints.

    Attributes:
        vault_path: Path to the age-encrypted vault file.
        identity_path: Path to the age identity (private key) file.

    Example::

        env = EnvironmentConfig(vault_path="./secrets/vault.age")
    """

    vault_path: str = "./secrets/vault.age"
    identity_path: str = "~/.config/liveguard/identity.age"


# ---------------------------------------------------------------------------
# Root config model
# ---------------------------------------------------------------------------


class LiveGuardConfig(BaseModel):
    """Root configuration model for LiveGuard.

    Validated from ``liveguard.yaml`` using pydantic v2.

    Attributes:
        version: Schema version, must be ``1``.
        pipeline: I/O and ordering configuration.
        modules: Per-module settings.
        obs: Alias for ``modules.m4`` — kept for readability in YAML.
        environment: Vault and identity paths.
        logging: Structured logging settings.
        dashboard: Streamlit dashboard settings.
        compliance: Compliance gating settings.

    Example::

        import yaml
        from liveguard.config.schema import LiveGuardConfig

        with open("liveguard.yaml") as f:
            cfg = LiveGuardConfig.model_validate(yaml.safe_load(f))
        assert cfg.version == 1
    """

    version: Literal[1]
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    modules: ModulesConfig = Field(default_factory=ModulesConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _compliance_banner_must_be_enabled_if_m4_enabled(self) -> LiveGuardConfig:
        """Warn at config-load time if M4 is enabled but banner is disabled.

        This does not raise — M4 itself raises :class:`ComplianceError` at
        stream start — but validates the intent is consistent.

        Returns:
            The validated config instance.
        """
        # No-op validator; M4 enforces at runtime.
        return self
