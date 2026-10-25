"""Process exit codes for LiveGuard.

Every exit code is documented here so that shell scripts, CI pipelines,
and monitoring systems can react to specific failure conditions.

Usage example::

    import sys
    from liveguard.exit_codes import ExitCode

    sys.exit(ExitCode.COMPLIANCE)  # exits with code 3
"""

from __future__ import annotations


class ExitCode:
    """Namespace of process exit codes used across all LiveGuard modules.

    Codes:
        SUCCESS (0):      Pipeline completed without errors.
        GENERIC (1):      Unclassified runtime error.
        USAGE (2):        Invalid arguments, bad config, or EULA not accepted.
        COMPLIANCE (3):   Hard compliance gate violated (e.g. banner disabled).
        STALE_ACK (4):    ToS acknowledgement is older than 30 days.
        EXTERNAL_DEP (5): Required external tool (ffmpeg, OBS, etc.) missing.
        AUDIT_WRITE (6):  Audit log write or hash-chain integrity failure.

    Example::

        from liveguard.exit_codes import ExitCode
        if result.exit_code == ExitCode.STALE_ACK:
            print("Please re-acknowledge the ToS.")
    """

    SUCCESS: int = 0
    """Pipeline completed successfully."""

    GENERIC: int = 1
    """Unclassified runtime error — check logs for details."""

    USAGE: int = 2
    """Invalid CLI arguments, unreadable/invalid config, or EULA not accepted."""

    COMPLIANCE: int = 3
    """Hard compliance gate violated (e.g. disclosure banner disabled)."""

    STALE_ACK: int = 4
    """ToS acknowledgement (``tos_ack_at``) is older than 30 days."""

    EXTERNAL_DEP: int = 5
    """A required external dependency (ffmpeg, exiftool, OBS …) is missing."""

    AUDIT_WRITE: int = 6
    """Failed to append to the audit log or HMAC hash chain is broken."""

    # Mapping for human-readable display (used by CLI error handler)
    _DESCRIPTIONS: dict[int, str] = {
        0: "success",
        1: "generic error",
        2: "usage / eula error",
        3: "compliance violation",
        4: "stale ToS acknowledgement",
        5: "external dependency missing",
        6: "audit write failure",
    }

    @classmethod
    def describe(cls, code: int) -> str:
        """Return a short human-readable description for *code*.

        Args:
            code: A process exit code integer.

        Returns:
            A one-line description string, or ``"unknown exit code"`` if
            *code* is not in the known set.

        Example::

            ExitCode.describe(3)  # → "compliance violation"
        """
        return cls._DESCRIPTIONS.get(code, "unknown exit code")
