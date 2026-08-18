# Ensure transforms are discovered: add an import per module under transforms/.
# The server only registers what this file imports.

import importlib
import pkgutil
from pathlib import Path

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


def _register_local_transforms() -> list[str]:
    """Import every module under ``transforms/local/``, if that directory exists.

    ``transforms/local/`` is gitignored: it is where integrations that should not be
    published live. It is discovered rather than imported by name because a fresh clone
    does not have it, and a missing static import would stop the server booting.

    Returns:
        The dotted names of the modules imported, for the startup log.
    """
    local_dir = Path(__file__).resolve().parent / "transforms" / "local"
    if not local_dir.is_dir():
        return []

    imported: list[str] = []
    for package in sorted(p for p in local_dir.iterdir() if (p / "__init__.py").is_file()):
        for module in pkgutil.iter_modules([str(package)]):
            # api.py holds shared client helpers and registers no transforms; importing
            # it directly is harmless but pointless, and it is imported by its siblings.
            if module.name in ("api", "__init__"):
                continue
            dotted = f"transforms.local.{package.name}.{module.name}"
            importlib.import_module(dotted)
            imported.append(dotted)
    return imported


_LOCAL_MODULES = _register_local_transforms()

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

    run_server(
        settings=settings,
        # SDK-native extension points. They are no-ops until their adapters in
        # middleware.py are swapped for real policy/audit clients.
        transform_middlewares=[
            AuthorizationMiddleware(PolicyChecker()),
            AuditMiddleware(AuditWriter()),
        ],
    )
