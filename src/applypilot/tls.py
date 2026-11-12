"""Trusted certificates for HTTPS requests made with Python's own urllib.

The desktop app ships its own Python, which on macOS has no list of trusted certificates, so every
urllib HTTPS request failed with "CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate"
(Workday employers, 0.18.0). certifi's list (installed with httpx) fixes that.
"""

from __future__ import annotations

import functools
import os
import ssl


def ca_file() -> str | None:
    try:
        import certifi
        path = certifi.where()
        return path if os.path.exists(path) else None
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def context() -> ssl.SSLContext:
    """The system's trusted certificates plus certifi's, so a company's own certificate (a work
    proxy installed on the computer) still works where the system has one."""
    ctx = ssl.create_default_context()
    path = ca_file()
    if path:
        ctx.load_verify_locations(cafile=path)
    return ctx


def use_bundled_certificates() -> None:
    """For the packaged app: point Python's default HTTPS checks at certifi's list, unless the user
    has set their own."""
    path = ca_file()
    if path:
        os.environ.setdefault("SSL_CERT_FILE", path)
