"""LiveGuard — local-only creator toolchain for compliant livestream preparation.

Architecture::

    Input → M1 (Privacy Normalizer) → M2 (Robustness Tester, opt-in)
          → M4 (OBS Orchestrator) → TikTok RTMP
    M3 (Geo-Test & Session) runs BEFORE M4; never scrapes credentials.
"""

__version__ = "0.1.0"
