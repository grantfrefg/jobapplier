"""The Mac app's bundled Python has no trusted certificates, so Workday's urllib requests failed with
CERTIFICATE_VERIFY_FAILED. They now use certifi's list."""

import ssl
import urllib.request

from applypilot import tls
from applypilot.discovery import workday


def test_context_trusts_certifi():
    assert tls.ca_file()
    ctx = tls.context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.cert_store_stats()["x509_ca"] > 50  # certifi alone has well over 100


def test_workday_requests_use_the_context(monkeypatch):
    seen = {}
    monkeypatch.setattr(workday, "_opener", None)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout, context: seen.setdefault("ctx", context))
    workday._urlopen("https://example.test/")
    assert seen["ctx"] is tls.context()

    for proxy in (None, "host:8080"):
        workday.setup_proxy(proxy)
        https = [h for h in workday._opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
        assert https and https[0]._context is tls.context()
    monkeypatch.setattr(workday, "_opener", None)


def test_bundled_certificates_dont_override_the_users_own(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/my/corporate.pem")
    tls.use_bundled_certificates()
    import os
    assert os.environ["SSL_CERT_FILE"] == "/my/corporate.pem"
    monkeypatch.delenv("SSL_CERT_FILE")
    tls.use_bundled_certificates()
    assert os.environ["SSL_CERT_FILE"] == tls.ca_file()
    monkeypatch.delenv("SSL_CERT_FILE")
