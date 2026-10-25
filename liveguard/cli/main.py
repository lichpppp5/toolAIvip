r"""LiveGuard CLI entry point.

Built with Typer. Each module is a subcommand group.

Usage example::

    $ liveguard harden raw.mp4 --output clean.mp4 --report report.json
    $ liveguard harden raw.mp4 --output clean.mp4 \\
          --strip-provenance --i-understand-legal-risk
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated

import typer

from liveguard.audit.chain import AuditChain
from liveguard.audit.verify import verify_active_audit_log
from liveguard.config.loader import load_config
from liveguard.config.schema import M1Config, M2Config
from liveguard.errors import (
    ComplianceError,
    ExternalDependencyError,
    LiveGuardError,
    StaleAckError,
    UsageError,
)
from liveguard.exit_codes import ExitCode
from liveguard.health import start_health_server
from liveguard.metrics import start_metrics_server
from liveguard.modules.m1.normalizer import M1Normalizer
from liveguard.modules.m2.tester import M2RobustnessTester
from liveguard.modules.m3.session import M3GeoSessionManager
from liveguard.modules.m4.orchestrator import M4OBSOrchestrator
from liveguard.orchestrator.runner import run_pipeline

app = typer.Typer(
    name="liveguard",
    help="Local-only creator toolchain for compliant livestream preparation.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _get_git_sha() -> str:
    """Retrieve git short commit SHA or 'unknown'."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _print_version_info() -> None:
    """Print LiveGuard version, commit SHA, and core dependencies."""
    git_sha = _get_git_sha()
    typer.echo(f"LiveGuard v0.1.0 (commit {git_sha})")
    deps = ["pydantic", "typer", "pyrage", "torch", "obsws-python", "playwright"]
    typer.echo("Dependencies:")
    for dep in deps:
        try:
            ver = importlib.metadata.version(dep)
        except Exception:
            ver = "not installed"
        typer.echo(f"  {dep}: {ver}")


def _version_callback(value: bool) -> None:
    """Callback triggered eagerly when --version is supplied."""
    if value:
        _print_version_info()
        raise typer.Exit(code=ExitCode.SUCCESS)


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Global path to YAML configuration file."),
    ] = Path("liveguard.yaml"),
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-v",
            help="Print version, git SHA, and dependencies.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """LiveGuard: compliant creator livestream toolchain."""
    ctx.ensure_object(dict)["config"] = config


# ---------------------------------------------------------------------------
# Shared error handler
# ---------------------------------------------------------------------------


def _handle_error(exc: LiveGuardError) -> None:
    """Print a structured error and exit with the appropriate exit code.

    Args:
        exc: A :class:`~liveguard.errors.LiveGuardError` instance.
    """
    typer.echo(f"\n[liveguard] ERROR (exit {exc.exit_code}): {exc.message}", err=True)
    raise typer.Exit(code=exc.exit_code)


# ---------------------------------------------------------------------------
# M1 -- harden subcommand
# ---------------------------------------------------------------------------


@app.command("harden")
def harden(
    # Positional argument: use old-style `= typer.Argument(...)` default
    # to avoid the Typer/Click 8.5 TyperArgument.make_metavar() arity bug.
    input: Path = typer.Argument(help="Source video file to normalise."),  # noqa: A002
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination for the normalised video."),
    ] = Path("./output/clean.mp4"),
    report: Annotated[
        Path | None,
        typer.Option(
            "--report",
            "-r",
            help="Write JSON report to this path. Omit to skip.",
        ),
    ] = None,
    strip_provenance: bool = typer.Option(  # noqa: FBT001
        False,
        "--strip-provenance",
        help=(
            "Remove C2PA provenance data from the output. "
            "Requires --i-understand-legal-risk."
        ),
    ),
    ack_legal_risk: bool = typer.Option(  # noqa: FBT001
        False,
        "--i-understand-legal-risk",
        help=(
            "Acknowledge that removing C2PA provenance may have legal implications. "
            "Required when --strip-provenance is set."
        ),
    ),
    crf: Annotated[
        int,
        typer.Option("--crf", help="FFmpeg CRF quality value [0-51]. Default: 23."),
    ] = 23,
    preset: Annotated[
        str,
        typer.Option(
            "--preset",
            help="FFmpeg encoding preset. Default: medium.",
        ),
    ] = "medium",
    noise: Annotated[
        int,
        typer.Option(
            "--noise",
            help="Light noise filter strength [0-8]. 0 = disabled. Default: 4.",
        ),
    ] = 4,
    user_comment: Annotated[
        str,
        typer.Option(
            "--comment",
            help="Free-text comment tag to inject. Optional.",
        ),
    ] = "",
    timeout: Annotated[
        int,
        typer.Option(
            "--timeout",
            help="FFmpeg subprocess timeout in seconds. Default: 3600.",
        ),
    ] = 3600,
) -> None:
    """Normalise privacy metadata in a video file (M1).

    Strips EXIF/GPS metadata, preserves C2PA provenance (unless
    --strip-provenance is set), injects neutral tags, and optionally
    adds a light noise filter.

    Example::

        liveguard harden raw.mp4 --output clean.mp4 --report report.json
    """
    # Validate CRF range early
    if not (0 <= crf <= 51):
        _handle_error(UsageError(f"--crf must be in [0, 51], got {crf}"))

    # Validate noise range early
    if not (0 <= noise <= 8):
        _handle_error(UsageError(f"--noise must be in [0, 8], got {noise}"))

    # Compliance gate: --strip-provenance requires --i-understand-legal-risk
    if strip_provenance != ack_legal_risk:
        _handle_error(
            ComplianceError(
                "--strip-provenance requires --i-understand-legal-risk. "
                "Both flags must be provided together, or neither."
            )
        )

    # Warn and pause if strip-provenance is set (allow Ctrl-C)
    if strip_provenance:
        typer.echo(
            "\n"
            "+-------------------------------------------------------------+\n"
            "|  WARNING: --strip-provenance is set                        |\n"
            "|                                                             |\n"
            "|  C2PA provenance data will be permanently removed from     |\n"
            "|  the output file. This may affect content authenticity     |\n"
            "|  verification and could have legal implications.           |\n"
            "|                                                             |\n"
            "|  Press Ctrl-C within 3 seconds to abort.                  |\n"
            "+-------------------------------------------------------------+\n",
            err=True,
        )
        try:
            time.sleep(3)
        except KeyboardInterrupt:
            typer.echo("\nAborted by user.", err=True)
            raise typer.Exit(code=ExitCode.SUCCESS) from None

    # Build config from CLI flags
    try:
        cfg = M1Config(crf=crf, preset=preset, noise=noise)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        _handle_error(UsageError(f"Invalid configuration: {exc}"))
        return  # unreachable; satisfies type checkers

    # Instantiate normalizer (validates deps at __init__)
    try:
        normalizer = M1Normalizer(cfg, timeout_s=timeout)
    except ExternalDependencyError as exc:
        _handle_error(exc)
        return

    # Run pipeline
    try:
        m1_report = asyncio.run(
            normalizer.process(
                input=input,
                output=output,
                strip_provenance=strip_provenance,
                ack_legal_risk=ack_legal_risk,
                user_comment=user_comment,
                noise=noise,
            )
        )
    except (
        ComplianceError,
        UsageError,
        ExternalDependencyError,
        LiveGuardError,
    ) as exc:
        _handle_error(exc)
        return

    # Emit JSON report
    report_dict = {
        "liveguard_version": "0.1.0",
        "module": "M1",
        "input_path": str(m1_report.input_path),
        "output_path": str(m1_report.output_path),
        "input_hash": m1_report.input_hash,
        "output_hash": m1_report.output_hash,
        "duration_seconds": m1_report.duration_seconds,
        "c2pa_before": m1_report.c2pa_before,
        "c2pa_after": m1_report.c2pa_after,
        "c2pa_preserved": m1_report.c2pa_preserved,
        "tags_removed": m1_report.tags_removed,
        "tags_injected": m1_report.tags_injected,
        "noise_strength": m1_report.noise_strength,
        "audio_normalized": m1_report.audio_normalized,
        "ffmpeg_log_path": str(m1_report.ffmpeg_log_path),
        "completed_at": m1_report.completed_at.isoformat(),
    }

    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(report_dict, indent=2))
        typer.echo(f"Report written to: {report}")

    typer.echo(json.dumps(report_dict, indent=2))
    typer.echo(f"\nNormalisation complete -> {output}")


# ---------------------------------------------------------------------------
# M2 -- protect subcommand (research-only)
# ---------------------------------------------------------------------------


@app.command("protect")
def protect(
    input: Path = typer.Argument(help="Source video file under test."),  # noqa: A002
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination path for watermarked video."),
    ] = Path("./output/perturbed.mp4"),
    research_only: bool = typer.Option(  # noqa: FBT001
        False,
        "--research-only",
        help="Mandatory: explicitly declare research-only intent.",
    ),
    ack_research_use: bool = typer.Option(  # noqa: FBT001
        False,
        "--i-understand-research-use",
        help="Mandatory: acknowledge this tool is NOT for platform evasion.",
    ),
    epsilon: Annotated[
        float,
        typer.Option(
            "--epsilon",
            help="L_inf perturbation budget numerator. Default: 8.0.",
        ),
    ] = 8.0,
    iters: Annotated[
        int,
        typer.Option("--iters", help="Number of PGD attack iterations. Default: 40."),
    ] = 40,
    batch: Annotated[
        int,
        typer.Option("--batch", help="Inference batch size. Default: 8."),
    ] = 8,
    report: Annotated[
        Path | None,
        typer.Option("--report", "-r", help="Path to write JSON evaluation report."),
    ] = None,
    resume: bool = typer.Option(  # noqa: FBT001
        False,
        "--resume",
        help="Resume interrupted run from checkpoint file.",
    ),
) -> None:
    """Evaluate adversarial robustness on video frames (M2).

    Applies PGD perturbation targeting CLIP self-similarity, bounded by
    SSIM/PSNR perceptual guards and a mandatory 'PERTURBED' watermark.

    CRITICAL: This is an academic research tool. It cannot be used to evade
    platform moderation. Both --research-only and --i-understand-research-use
    are strictly required.
    """
    # 1. Compliance verification
    if not research_only or not ack_research_use:
        _handle_error(
            UsageError(
                "M2 Robustness Tester is strictly for research purposes. Both "
                "--research-only AND --i-understand-research-use flags are required."
            )
        )
        return

    # 2. Warning box and pause before running
    typer.echo(
        "\n"
        "+-------------------------------------------------------------+\n"
        "|  WARNING: RESEARCH-ONLY ADVERSARIAL EVALUATION RUN          |\n"
        "|                                                             |\n"
        "|  This tool is for offline academic and defensive evaluation.|\n"
        "|  It is EXPLICITLY NOT for platform moderation evasion.      |\n"
        "|  Every frame will have a visible 'PERTURBED' watermark.     |\n"
        "|                                                             |\n"
        "|  Press Ctrl-C within 5 seconds to abort.                    |\n"
        "+-------------------------------------------------------------+\n",
        err=True,
    )
    try:
        time.sleep(5)
    except KeyboardInterrupt:
        typer.echo("\nAborted by user.", err=True)
        raise typer.Exit(code=ExitCode.SUCCESS) from None

    eps_val = epsilon / 255.0 if epsilon > 1.0 else epsilon
    cfg = M2Config(epsilon=eps_val, iters=iters, batch_size=batch)

    try:
        tester = M2RobustnessTester(cfg)
    except Exception as exc:  # noqa: BLE001
        _handle_error(UsageError(f"Initialization error: {exc}"))
        return

    try:
        m2_report = asyncio.run(
            tester.test(
                input=input,
                output=output,
                research_only=research_only,
                ack_research_use=ack_research_use,
                epsilon=eps_val,
                iters=iters,
                batch_size=batch,
                resume=resume,
            )
        )
    except (
        UsageError,
        ComplianceError,
        ExternalDependencyError,
        LiveGuardError,
    ) as exc:
        _handle_error(exc)
        return

    report_dict = {
        "liveguard_version": "0.1.0",
        "module": "M2",
        "input_path": str(m2_report.input_path),
        "watermarked_path": str(m2_report.watermarked_path),
        "input_hash": m2_report.input_hash,
        "output_hash": m2_report.output_hash,
        "perturbations_applied": m2_report.perturbations_applied,
        "mean_ssim": m2_report.mean_ssim,
        "mean_psnr": m2_report.mean_psnr,
        "eps_used": m2_report.eps_used,
        "iters": m2_report.iters,
        "device": m2_report.device,
        "duration_seconds": m2_report.duration_seconds,
        "frames_processed": m2_report.frames_processed,
        "eps_reductions": m2_report.eps_reductions,
        "watermark_visible": m2_report.watermark_visible,
        "completed_at": m2_report.completed_at.isoformat(),
    }

    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(report_dict, indent=2))
        typer.echo(f"Report written to: {report}")

    typer.echo(json.dumps(report_dict, indent=2))
    typer.echo(f"\nRobustness evaluation complete -> {output}")


# ---------------------------------------------------------------------------
# M4 -- obs subcommand group
# ---------------------------------------------------------------------------

obs_app = typer.Typer(
    name="obs",
    help="OBS Studio orchestration and stream management (M4).",
    no_args_is_help=True,
)
app.add_typer(obs_app, name="obs")


@obs_app.command("start")
def obs_start(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to YAML configuration file.",
        ),
    ] = Path("liveguard.yaml"),
    stream_key: Annotated[
        str | None,
        typer.Option(
            "--stream-key",
            "-k",
            help="TikTok RTMP stream key (prompted interactively if omitted).",
        ),
    ] = None,
) -> None:
    """Start OBS RTMP stream under compliance gating (M4).

    Validates that the AI disclosure banner is enabled and ToS acknowledgement
    is not stale. Automatically provisions required sources in OBS.
    """
    full_cfg = load_config(config)

    key = stream_key
    if not key:
        try:
            key = typer.prompt("Enter TikTok RTMP stream key", hide_input=True)
        except (KeyboardInterrupt, EOFError):
            typer.echo("\nAborted by user.", err=True)
            raise typer.Exit(code=ExitCode.SUCCESS) from None

    orch = M4OBSOrchestrator(
        config=full_cfg.modules.m4,
        compliance_config=full_cfg.compliance,
        video_source_path=full_cfg.pipeline.input_path,
    )

    try:
        status = asyncio.run(orch.start_stream(stream_key=key))
        typer.echo(
            f"OBS stream started successfully: state={status.state.name}, "
            f"scene='{status.scene_name}'"
        )
    except (
        ComplianceError,
        StaleAckError,
        ExternalDependencyError,
        LiveGuardError,
    ) as exc:
        _handle_error(exc)


@obs_app.command("monitor")
def obs_monitor(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to YAML configuration file.",
        ),
    ] = Path("liveguard.yaml"),
) -> None:
    """Monitor OBS stream health and reconnect if dropped (M4)."""
    full_cfg = load_config(config)

    orch = M4OBSOrchestrator(
        config=full_cfg.modules.m4,
        compliance_config=full_cfg.compliance,
        video_source_path=full_cfg.pipeline.input_path,
    )

    async def _run_monitor() -> None:
        await orch.connect()
        loop = asyncio.get_running_loop()
        orch.register_signal_handlers(loop)
        host = full_cfg.modules.m4.obs_host
        port = full_cfg.modules.m4.obs_port
        typer.echo(f"Monitoring OBS stream at {host}:{port}...")
        await orch.monitor()

    try:
        asyncio.run(_run_monitor())
    except (
        ComplianceError,
        StaleAckError,
        ExternalDependencyError,
        LiveGuardError,
    ) as exc:
        _handle_error(exc)


# ---------------------------------------------------------------------------
# M3 -- env subcommand group
# ---------------------------------------------------------------------------

env_app = typer.Typer(
    name="env",
    help="Environment geo-testing and session management (M3).",
    no_args_is_help=True,
)
app.add_typer(env_app, name="env")


@env_app.command("check")
def env_check(
    profile: Annotated[
        str,
        typer.Option(
            "--profile",
            "-p",
            help="Browser profile ID for geo-testing.",
        ),
    ] = "default",
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to YAML configuration file.",
        ),
    ] = Path("liveguard.yaml"),
) -> None:
    """Run geographic availability and session check for a profile (M3)."""
    full_cfg = load_config(config)
    mgr = M3GeoSessionManager(config=full_cfg.modules.m3)

    try:
        report = asyncio.run(mgr.check(profile_id=profile))
        typer.echo(
            f"Geo-test report for profile '{profile}':\n"
            f"  Region probed : {report.region_probed}\n"
            f"  Geo result    : {report.geo_result.name}\n"
            f"  Latency       : {report.latency_ms:.1f} ms\n"
            f"  Session valid : {report.session_valid}\n"
            f"  Probe endpoint: {report.probe_url}"
        )
    except (UsageError, ExternalDependencyError, LiveGuardError) as exc:
        _handle_error(exc)


@env_app.command("login")
def env_login(
    profile: Annotated[
        str,
        typer.Option(
            "--profile",
            "-p",
            help="Browser profile ID for manual login.",
        ),
    ] = "default",
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to YAML configuration file.",
        ),
    ] = Path("liveguard.yaml"),
) -> None:
    """Open visible browser for manual one-time user login (M3)."""
    full_cfg = load_config(config)
    mgr = M3GeoSessionManager(config=full_cfg.modules.m3)

    try:
        typer.echo(f"Opening browser for manual login (profile='{profile}')...")
        report = asyncio.run(mgr.login(profile_id=profile, headless=False))
        if report.session_valid:
            typer.echo(f"Login successful for profile '{profile}'. Session saved.")
        else:
            typer.echo(
                f"Login incomplete or timed out for profile '{profile}'.", err=True
            )
    except (UsageError, ExternalDependencyError, LiveGuardError) as exc:
        _handle_error(exc)


# ---------------------------------------------------------------------------
# Audit subcommand group
# ---------------------------------------------------------------------------

audit_app = typer.Typer(
    name="audit",
    help="Cryptographic HMAC-chained audit log inspection and verification.",
    no_args_is_help=True,
)
app.add_typer(audit_app, name="audit")


@audit_app.command("verify")
def audit_verify(
    log: Annotated[
        Path,
        typer.Option("--log", "-l", help="Path to audit events JSONL file."),
    ] = Path("audit/events.jsonl"),
    key: Annotated[
        Path,
        typer.Option(
            "--key", "-k", help="Path to age private key for HMAC derivation."
        ),
    ] = Path("secrets/age.key"),
) -> None:
    """Recompute and verify HMAC-SHA256 audit chain integrity."""
    is_valid, broken_idx, msg = verify_active_audit_log(
        audit_path=log, age_key_path=key
    )
    if is_valid:
        typer.echo(f"Audit log verified: {msg}")
        raise typer.Exit(code=ExitCode.SUCCESS)

    typer.echo(
        f"[liveguard] AUDIT INTEGRITY FAILURE at entry {broken_idx}: {msg}",
        err=True,
    )
    raise typer.Exit(code=ExitCode.AUDIT_WRITE)


@audit_app.command("tail")
def audit_tail(
    lines: Annotated[
        int,
        typer.Option(
            "-n", "--lines", help="Number of recent audit entries to display."
        ),
    ] = 20,
    log: Annotated[
        Path,
        typer.Option("--log", "-l", help="Path to audit events JSONL file."),
    ] = Path("audit/events.jsonl"),
) -> None:
    """Display the most recent entries from the HMAC audit log."""
    if not log.is_file():
        typer.echo(f"Audit log file '{log}' does not exist.", err=True)
        raise typer.Exit(code=ExitCode.SUCCESS)

    chain = AuditChain(log_path=log)
    entries = chain.tail(lines)
    for entry in entries:
        typer.echo(json.dumps(entry))


# ---------------------------------------------------------------------------
# Config subcommand group
# ---------------------------------------------------------------------------

config_app = typer.Typer(
    name="config",
    help="Configuration file validation and inspection.",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")


@config_app.command("validate")
def config_validate(
    config: Annotated[
        Path,
        typer.Argument(help="Path to YAML configuration file to validate."),
    ] = Path("liveguard.yaml"),
) -> None:
    """Validate a LiveGuard YAML configuration file against the schema."""
    try:
        cfg = load_config(config)
        typer.echo(f"Configuration '{config}' is valid (schema version {cfg.version}).")
    except LiveGuardError as exc:
        _handle_error(exc)


# ---------------------------------------------------------------------------
# Version command
# ---------------------------------------------------------------------------


@app.command("version")
def version_cmd() -> None:
    """Print LiveGuard version, git commit SHA, and core dependencies."""
    _print_version_info()


# ---------------------------------------------------------------------------
# Dashboard command
# ---------------------------------------------------------------------------


@app.command("dashboard")
def dashboard_cmd(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to YAML configuration file."),
    ] = Path("liveguard.yaml"),
    host: Annotated[
        str,
        typer.Option("--host", "-h", help="Streamlit dashboard bind host."),
    ] = "0.0.0.0",
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Streamlit dashboard bind port."),
    ] = 8501,
) -> None:
    """Launch the LiveGuard Streamlit operations dashboard."""
    app_path = Path(__file__).parent.parent / "dashboard" / "app.py"
    typer.echo(f"Starting LiveGuard dashboard on http://{host}:{port} ...")
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        host,
        "--server.port",
        str(port),
        "--server.headless",
        "true",
    ]
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        typer.echo("\nLiveGuard dashboard stopped.")


# ---------------------------------------------------------------------------
# Serve Metrics & Health command
# ---------------------------------------------------------------------------


@app.command("serve-metrics")
def serve_metrics_cmd(
    metrics_port: Annotated[
        int,
        typer.Option(
            "--metrics-port",
            help="Port for Prometheus metrics exposition (:9090/metrics).",
        ),
    ] = 9090,
    health_port: Annotated[
        int,
        typer.Option(
            "--health-port",
            help="Port for HTTP health endpoint (:8080/healthz).",
        ),
    ] = 8080,
    host: Annotated[
        str,
        typer.Option("--host", "-h", help="Bind host address."),
    ] = "0.0.0.0",
) -> None:
    """Run headless Prometheus metrics and health endpoint servers."""
    typer.echo(f"Starting Prometheus metrics on http://{host}:{metrics_port}/metrics")
    start_metrics_server(port=metrics_port, host=host)

    typer.echo(f"Starting Health endpoint on http://{host}:{health_port}/healthz")
    server = start_health_server(port=health_port, host=host)

    typer.echo("Servers running in headless mode. Press Ctrl+C to terminate.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        typer.echo("\nStopping servers...")
        server.stop()
        typer.echo("Servers stopped.")


# ---------------------------------------------------------------------------
# Pipeline run command
# ---------------------------------------------------------------------------


@app.command("run")
def run_cmd(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Path to YAML configuration file."),
    ] = Path("liveguard.yaml"),
    research_only: bool = typer.Option(
        False,
        "--research-only",
        help="Enable M2 robustness testing in research mode.",
    ),
    ack_research_use: bool = typer.Option(
        False,
        "--i-understand-research-use",
        help="Acknowledge M2 research use.",
    ),
    strip_provenance: bool = typer.Option(
        False,
        "--strip-provenance",
        help="Strip C2PA provenance in M1.",
    ),
    ack_legal_risk: bool = typer.Option(
        False,
        "--i-understand-legal-risk",
        help="Acknowledge legal risk for C2PA stripping.",
    ),
) -> None:
    """Execute the configured LiveGuard pipeline end-to-end."""
    try:
        cfg = load_config(config)
        state = run_pipeline(
            cfg=cfg,
            research_only=research_only,
            ack_research_use=ack_research_use,
            strip_provenance=strip_provenance,
            ack_legal_risk=ack_legal_risk,
            interactive_eula=True,
        )
        typer.echo(f"Pipeline finished with status: {state.active_phase}")
    except LiveGuardError as exc:
        _handle_error(exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    app()
