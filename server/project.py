# Ensure transforms are discovered: add an import per module under transforms/.
# The server only registers what this file imports.

import logging
from pathlib import Path

from discovery import discover_local_transforms
from maltego.config import get_logging_config
from maltego.server import MaltegoServerSettings, ServerHTTPSettings, run_server
from middleware import AuditMiddleware, AuditWriter, AuthorizationMiddleware, PolicyChecker

# Reference integrations, committed to this repository as worked examples. Each one is
# imported by name, which is the registration model documented in AGENTS.md.
from transforms.crowdsec.smoke import *  # noqa: F401,F403
from transforms.examples.ffraud import *  # noqa: F401,F403
from transforms.greynoise.community import *  # noqa: F401,F403
from transforms.ipinfo.lookup import *  # noqa: F401,F403
from transforms.ransomwarelive.groups import *  # noqa: F401,F403
from transforms.ransomwarelive.intel import *  # noqa: F401,F403
from transforms.ransomwarelive.victims import *  # noqa: F401,F403
from transforms.rdap.domain import *  # noqa: F401,F403

# Integrations under the gitignored transforms/local/ are discovered, not imported by name:
# packages and single-file modules both load. See discovery.py for the rules.
_LOCAL_MODULES = discover_local_transforms(
    Path(__file__).resolve().parent / "transforms" / "local", "transforms.local"
)

log = logging.getLogger(__name__)

# Written by `transformatron_cli.py certs`. Kept in step with
# TransformatronConfig.cert_file / .key_file, which resolve to the same paths.
_CERTS_DIR = Path(__file__).resolve().parent.parent / ".transformatron" / "certs"
CERT_FILE = _CERTS_DIR / "cert.pem"
KEY_FILE = _CERTS_DIR / "key.pem"

if __name__ == "__main__":
    settings = MaltegoServerSettings(
        # Fallbacks for running this file directly. The supported way to change them is
        # transformatron.toml at the repository root, which the CLI and MCP server pass
        # in as MALTEGO_SERVER_* variables — those outrank whatever is written here.
        server_name="New Maltego Integration",
        ns="acme.new_maltego_integration",
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

    # run_server configures logging itself, but only once called, and it then blocks. The
    # same config is applied here first so the discovery line reaches server.log; a local
    # transform that never appears in the client is otherwise hard to tell from one that
    # was never loaded.
    get_logging_config(settings.log_level.upper())
    log.info("Local transforms loaded: %s", ", ".join(_LOCAL_MODULES) or "none")

    run_server(
        settings=settings,
        # SDK-native extension points. They are no-ops until their adapters in
        # middleware.py are swapped for real policy/audit clients.
        transform_middlewares=[
            AuthorizationMiddleware(PolicyChecker()),
            AuditMiddleware(AuditWriter()),
        ],
    )
