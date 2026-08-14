"""Generate a local development certificate for serving the transform server over HTTPS.

The Maltego Graph Browser client requires HTTPS. Plain local development over
HTTP does not need any of this.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from transformatron.config import TransformatronConfig

# Mirrors resources/cert.conf in the maltego-transforms repository: a localhost
# certificate needs both the DNS name and the loopback IP as subject alt names,
# or clients reached via 127.0.0.1 reject it.
#
# keyUsage must include digitalSignature. Every TLS 1.3 suite is signature-based —
# the server signs the handshake instead of receiving an encrypted premaster secret —
# so a certificate offering only the encipherment bits cannot be used for the exchange
# the Graph Browser negotiates. Chrome enforces this and fails the request with
# ERR_SSL_KEY_USAGE_INCOMPATIBLE before CORS or any application logic is reached;
# trusting the certificate does not help, because trust is not what is being rejected.
# curl is laxer and accepts the encipherment-only certificate, so it does not catch this.
CERT_CONFIG = """[req]
default_bits = 2048
prompt = no
distinguished_name = req_distinguished_name
x509_extensions = v3_req

[req_distinguished_name]
CN = localhost

[v3_req]
keyUsage = digitalSignature, keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = localhost
IP.1 = 127.0.0.1
"""

CERT_VALIDITY_DAYS = 825


class CertificateError(RuntimeError):
    """Raised when a certificate cannot be generated."""


@dataclass
class CertificatePair:
    """Paths to a generated certificate and its private key.

    Attributes:
        cert_file: PEM-encoded certificate.
        key_file: PEM-encoded private key.
        trust_command: Command the user can run to trust the certificate.
    """

    cert_file: str
    key_file: str
    trust_command: str


def generate(config: TransformatronConfig, force: bool = False) -> CertificatePair:
    """Create a self-signed certificate for ``localhost`` and ``127.0.0.1``.

    Args:
        config: Supplies the certificate output paths.
        force: Overwrite an existing certificate pair.

    Returns:
        The certificate paths and the command to trust them.

    Raises:
        CertificateError: If a certificate already exists and ``force`` is unset,
            or if ``openssl`` fails.
    """
    cert_file, key_file = config.cert_file, config.key_file
    if cert_file.exists() and not force:
        return CertificatePair(str(cert_file), str(key_file), _trust_command(config))

    cert_file.parent.mkdir(parents=True, exist_ok=True)
    config_file = cert_file.parent / "cert.conf"
    config_file.write_text(CERT_CONFIG)

    try:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-nodes",
                "-newkey",
                "rsa:2048",
                "-days",
                str(CERT_VALIDITY_DAYS),
                "-keyout",
                str(key_file),
                "-out",
                str(cert_file),
                "-config",
                str(config_file),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise CertificateError("openssl was not found on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise CertificateError(f"openssl failed to generate a certificate: {exc.stderr}") from exc

    key_file.chmod(0o600)
    return CertificatePair(str(cert_file), str(key_file), _trust_command(config))


def _trust_command(config: TransformatronConfig) -> str:
    """Return the macOS command that adds the certificate to the system trust store.

    Trusting a certificate changes system-wide trust, so this is surfaced for the
    user to run themselves rather than executed here.
    """
    return (
        "sudo security add-trusted-cert -d -r trustRoot "
        f"-k /Library/Keychains/System.keychain {config.cert_file}"
    )
