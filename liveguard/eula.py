r"""EULA gate and per-feature acknowledgements for LiveGuard.

Ensures compliance with legal and ethical terms before executing
any LiveGuard operations. First-run requires explicit user agreement.
"""

from __future__ import annotations

import getpass
import hashlib
import platform
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from liveguard.audit.chain import get_audit_chain
from liveguard.errors import ComplianceError, UsageError

DEFAULT_MARKER_PATH = Path.home() / ".liveguard" / "eula_accepted"

SEPARATOR = "=" * 78
EULA_TEXT = f"""{SEPARATOR}
                        LIVEGUARD END USER LICENSE AGREEMENT
{SEPARATOR}
LiveGuard is a local-only creator livestream preparation and protection toolchain.
By using this software, you agree to the following mandatory ethical and legal terms:

1. COMPLIANCE & INTEGRITY
   - LiveGuard must NEVER be used to evade platform bans, community guidelines,
     or terms of service.
   - Fake-device metadata spoofing and residential proxy rotation for evasion
     are strictly prohibited and not supported.

2. PROVENANCE & TRANSPARENCY
   - C2PA Content Credentials are preserved by default.
   - Provenance stripping requires explicit legal risk acknowledgement.
   - Disclosure banners in livestreaming are required for synthetic content.

3. RESEARCH TOOLS
   - Module M2 (Robustness Testing) is strictly for research use with mandatory
     watermarking ("PERTURBED") and cannot be used for evasive purposes.

4. CREDENTIAL SAFETY
   - LiveGuard never scrapes stream keys or account credentials from web UIs.
   - All secrets must be securely managed via the encrypted local vault.

To accept these terms and proceed, you must type 'I AGREE' exactly.
{SEPARATOR}
"""


def compute_machine_hash() -> str:
    """Compute an anonymized SHA-256 machine hash.

    Combines platform node name, local username, and hardware MAC address.

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    mac = uuid.getnode()
    node = platform.node()
    user = getpass.getuser()
    raw = f"{node}:{user}:{mac}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_eula_accepted(marker_path: Path | None = None) -> bool:
    """Check whether the EULA marker file exists and is non-empty.

    Args:
        marker_path: Path to marker file. Defaults to DEFAULT_MARKER_PATH.

    Returns:
        True if accepted, False otherwise.
    """
    path = marker_path if marker_path is not None else DEFAULT_MARKER_PATH
    return path.is_file() and path.stat().st_size > 0


def enforce_eula(
    marker_path: Path | None = None,
    prompt_fn: Callable[[str], str] | None = None,
    interactive: bool = True,
) -> None:
    """Enforce EULA agreement.

    If not previously accepted, presents the EULA text and prompts the user
    for the exact phrase 'I AGREE'. If refused or invalid input is given,
    exits with ExitCode.USAGE (2) via UsageError.

    Args:
        marker_path: Path to marker file. Defaults to DEFAULT_MARKER_PATH.
        prompt_fn: Custom prompt function for mocking stdin.
        interactive: If False and not accepted, immediately refuses.

    Raises:
        UsageError: If the user refuses or enters anything other than 'I AGREE'.
    """
    path = marker_path if marker_path is not None else DEFAULT_MARKER_PATH
    if is_eula_accepted(path):
        return

    sys.stdout.write(EULA_TEXT + "\n")
    sys.stdout.flush()

    if not interactive and prompt_fn is None:
        raise UsageError("EULA has not been accepted. Interactive agreement required.")

    ask = prompt_fn if prompt_fn is not None else input
    try:
        response = ask("Type 'I AGREE' to accept the terms: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise UsageError("EULA agreement cancelled by user.") from exc

    if response != "I AGREE":
        raise UsageError(
            f"EULA refused (expected 'I AGREE', got '{response}'). Exiting."
        )

    # Record acceptance
    path.parent.mkdir(parents=True, exist_ok=True)
    machine_hash = compute_machine_hash()
    accepted_ts = time.time()
    marker_content = f"eula_accepted_at={accepted_ts}\nmachine_hash={machine_hash}\n"
    path.write_text(marker_content, encoding="utf-8")
    path.chmod(0o600)

    # Append audit log
    try:
        chain = get_audit_chain()
        chain.append(
            event="eula_accepted",
            module="eula",
            payload={
                "machine_hash": machine_hash,
                "accepted_at": accepted_ts,
            },
            actor_hash=machine_hash,
        )
    except Exception:
        # If audit chain cannot write, don't crash acceptance if not yet initialized
        pass


def require_ack(
    feature: str,
    enabled: bool,
    acknowledged: bool,
    ack_flag_name: str,
    is_compliance: bool = False,
) -> None:
    """Verify that a sensitive feature has its mandatory acknowledgement.

    Args:
        feature: Description or name of the feature (e.g. 'strip_provenance').
        enabled: Whether the feature was requested/enabled.
        acknowledged: Whether the user provided the corresponding ack flag.
        ack_flag_name: The flag required (e.g. '--i-understand-legal-risk').
        is_compliance: If True, raises ComplianceError instead of UsageError.

    Raises:
        ComplianceError: If is_compliance is True and ack is missing or orphaned.
        UsageError: If is_compliance is False and ack is missing or orphaned.
    """
    if enabled and not acknowledged:
        msg = f"Feature '{feature}' requires acknowledgement flag '{ack_flag_name}'."
        if is_compliance:
            raise ComplianceError(msg)
        raise UsageError(msg)

    if acknowledged and not enabled:
        msg = (
            f"Acknowledgement flag '{ack_flag_name}' provided without enabling "
            f"'{feature}'."
        )
        if is_compliance:
            raise ComplianceError(msg)
        raise UsageError(msg)
