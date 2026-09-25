"""Network helpers shared by all LLM providers.

SSL: prefer certifi's CA bundle. Python's default context reads the Windows
certificate store, which on freshly-imaged or long-offline machines can be
missing root CAs — producing CERTIFICATE_VERIFY_FAILED on perfectly healthy
networks. certifi ships the CA bundle inside the app, so cloud providers
work on any machine.
"""

from __future__ import annotations

import ssl
import urllib.error

_context: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """Shared SSL context: certifi bundle when available, system roots else."""
    global _context
    if _context is None:
        _context = _build_context()
    return _context


def _build_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def ssl_error_hint(e: Exception) -> str | None:
    """Human-readable hint when `e` is a TLS certificate verification failure.

    Returns None for anything else, so callers can keep their normal message.
    """
    reason = e
    if isinstance(e, urllib.error.URLError) and e.reason:
        reason = e.reason
    text = str(reason).lower()
    if isinstance(reason, ssl.SSLCertVerificationError) or "certificate verify failed" in text:
        return (
            "the secure connection to the server could not be verified. "
            "Make sure the system date/time is correct, or update Windows "
            "root certificates — the app bundles its own CA list, so a "
            "reboot or an update usually fixes it."
        )
    return None

