# Ensure transforms are discovered: add an import per module under transforms/.
# The server only registers what this file imports.

from pathlib import Path

from maltego.server import MaltegoServerSettings, ServerHTTPSettings, run_server

# Sample transforms. Delete this import (and transforms/examples/) once you have
# your own modules; the server only registers what this file imports.
from transforms.examples.ffraud import *  # noqa: F401,F403
from transforms.ipinfo.lookup import *  # noqa: F401,F403
from transforms.ransomwarelive.groups import *  # noqa: F401,F403
from transforms.ransomwarelive.intel import *  # noqa: F401,F403
from transforms.ransomwarelive.victims import *  # noqa: F401,F403

# Written by `transformatron_cli.py certs`. Kept in step with
# TransformatronConfig.cert_file / .key_file, which resolve to the same paths.
_CERTS_DIR = Path(__file__).resolve().parent.parent / ".transformatron" / "certs"
CERT_FILE = _CERTS_DIR / "cert.pem"
KEY_FILE = _CERTS_DIR / "key.pem"

if __name__ == "__main__":
    settings = MaltegoServerSettings(
        server_name="New Maltego Integration",
        ns="acme.new_maltego_integration",  # choose acme.* here
        author="Acme Corp",
        # The Maltego desktop client refuses plain-HTTP transform servers, and it
        # rejects them client-side, so the server log stays empty. Generate a
        # certificate pair first (`transformatron_cli.py certs`) and pass it below.
        # MALTEGO_SERVER_* environment variables override these values, which is how
        # the transformatron CLI and MCP server steer this file without editing it.
        http_settings=ServerHTTPSettings(
            protocol="https",
            cert_file=str(CERT_FILE),
            cert_key=str(KEY_FILE),
            cors_allowed_origins=["https://app.maltego.com"]
        ),
    )

    run_server(settings=settings)
