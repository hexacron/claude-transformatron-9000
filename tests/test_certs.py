"""Tests for the generated development certificate.

These assert properties of the certificate that browsers enforce and curl does not, so a
regression here shows up as an unreachable Graph Browser rather than a failing request.
"""

from __future__ import annotations

import shutil
import ssl
import subprocess

import pytest

from transformatron.certs import generate
from transformatron.config import TransformatronConfig


@pytest.fixture
def config(tmp_path) -> TransformatronConfig:
    return TransformatronConfig(project_dir=tmp_path, state_dir=tmp_path / "state")


requires_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl is needed to generate a certificate"
)


def _cert_text(cert_file: str) -> str:
    """Return the human-readable dump of a PEM certificate."""
    return subprocess.run(
        ["openssl", "x509", "-in", cert_file, "-noout", "-text"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@requires_openssl
def test_certificate_allows_digital_signature(config: TransformatronConfig) -> None:
    """The key usage must permit signing, or TLS 1.3 clients cannot use the certificate.

    Every TLS 1.3 cipher suite is signature-based, so a server whose certificate offers only
    the encipherment bits cannot complete the handshake a browser negotiates. Chrome rejects
    it with ERR_SSL_KEY_USAGE_INCOMPATIBLE before any application logic runs, and trusting
    the certificate does not help. curl accepts it, so only an explicit check catches this.
    """
    pair = generate(config)

    key_usage = _cert_text(pair.cert_file).split("X509v3 Key Usage")[1].split("X509v3")[0]

    assert "Digital Signature" in key_usage


@requires_openssl
def test_certificate_covers_localhost_and_loopback(config: TransformatronConfig) -> None:
    """Clients reaching the server by IP reject a certificate that only names localhost."""
    pair = generate(config)

    san = _cert_text(pair.cert_file).split("Subject Alternative Name")[1].split("X509v3")[0]

    assert "DNS:localhost" in san
    assert "IP Address:127.0.0.1" in san


@requires_openssl
def test_certificate_loads_into_a_tls_server_context(config: TransformatronConfig) -> None:
    """The pair is usable by a TLS 1.3 server context, which is how it is actually served.

    Exercises the certificate and key together: a mismatched pair, an unreadable key, or a
    certificate a modern TLS stack refuses to load all surface here rather than at runtime.
    """
    pair = generate(config)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(pair.cert_file, pair.key_file)
