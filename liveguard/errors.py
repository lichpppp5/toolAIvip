"""LiveGuard exception hierarchy.

All public exceptions inherit from :class:`LiveGuardError` so callers can
catch the entire taxonomy with a single ``except LiveGuardError`` clause.

Usage example::

    from liveguard.errors import ComplianceError
    raise ComplianceError("Disclosure banner is disabled", exit_code=3)
"""

from __future__ import annotations

from liveguard.exit_codes import ExitCode


class LiveGuardError(Exception):
    """Base class for all LiveGuard runtime errors.

    Attributes:
        message: Human-readable description of the error.
        exit_code: Suggested process exit code from :mod:`liveguard.exit_codes`.

    Example::

        try:
            run_pipeline()
        except LiveGuardError as exc:
            sys.exit(exc.exit_code)
    """

    def __init__(self, message: str, exit_code: int = ExitCode.GENERIC) -> None:
        """Initialise the error with a message and exit code.

        Args:
            message: Human-readable description of what went wrong.
            exit_code: Process exit code; defaults to :attr:`ExitCode.GENERIC`.
        """
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code

    def __str__(self) -> str:  # noqa: D105
        return f"[exit {self.exit_code}] {self.message}"


class UsageError(LiveGuardError):
    """Raised when the user supplies invalid arguments or configuration.

    Exit code: :attr:`ExitCode.USAGE`.

    Example::

        raise UsageError("--crf must be in [0, 51], got 99")
    """

    def __init__(self, message: str) -> None:
        """Initialise with a usage-level exit code.

        Args:
            message: Description of the misuse, ideally with remediation hint.
        """
        super().__init__(message, exit_code=ExitCode.USAGE)


class ComplianceError(LiveGuardError):
    """Raised when a hard compliance gate is violated.

    Typical trigger: disclosure banner disabled before stream start.
    Exit code: :attr:`ExitCode.COMPLIANCE`.

    Example::

        raise ComplianceError("Disclosure banner must be enabled to stream.")
    """

    def __init__(self, message: str) -> None:
        """Initialise with a compliance exit code.

        Args:
            message: Description of the violated compliance rule.
        """
        super().__init__(message, exit_code=ExitCode.COMPLIANCE)


class StaleAckError(LiveGuardError):
    """Raised when the ToS acknowledgement is older than 30 days.

    Exit code: :attr:`ExitCode.STALE_ACK`.

    Example::

        raise StaleAckError("tos_ack_at is 45 days old; re-acknowledge required.")
    """

    def __init__(self, message: str) -> None:
        """Initialise with a stale-ack exit code.

        Args:
            message: Description of the staleness condition.
        """
        super().__init__(message, exit_code=ExitCode.STALE_ACK)


class ExternalDependencyError(LiveGuardError):
    """Raised when a required external tool is missing or unreachable.

    Typical triggers: ffmpeg not on PATH, OBS WebSocket refused connection,
    exiftool binary absent.
    Exit code: :attr:`ExitCode.EXTERNAL_DEP`.

    Example::

        raise ExternalDependencyError("ffmpeg not found on PATH")
    """

    def __init__(self, message: str, dependency: str = "") -> None:
        """Initialise with an external-dependency exit code.

        Args:
            message: Human-readable description of the missing dependency.
            dependency: Optional name of the missing dependency (e.g. ``"ffmpeg"``).
        """
        super().__init__(message, exit_code=ExitCode.EXTERNAL_DEP)
        self.dependency = dependency


class AuditWriteError(LiveGuardError):
    """Raised when the audit log cannot be written or the hash chain breaks.

    Exit code: :attr:`ExitCode.AUDIT_WRITE`.

    Example::

        raise AuditWriteError("Failed to append to audit.jsonl: permission denied")
    """

    def __init__(self, message: str) -> None:
        """Initialise with an audit-write exit code.

        Args:
            message: Description of the write failure.
        """
        super().__init__(message, exit_code=ExitCode.AUDIT_WRITE)
