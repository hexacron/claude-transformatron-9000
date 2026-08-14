"""Configuration for the local Maltego v3 transform server."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The SDK's ServerHTTPSettings defaults to 127.0.0.1:3000, and the generated
# project.py sets protocol="http". The skill scripts shipped with the SDK
# default to port 8080 instead, so the port is always stated explicitly here
# rather than relying on any single default.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3000
API_PREFIX = "api/v3"


@dataclass(frozen=True)
class TransformatronConfig:
    """Locations and connection details for the managed transform server.

    Attributes:
        host: Interface the transform server binds to.
        port: TCP port the transform server listens on.
        scheme: ``http`` for local development, ``https`` for Graph Browser.
        project_dir: Directory holding ``project.py`` and ``transforms/``.
        state_dir: Directory for the PID file, log file, and generated certs.
        env_file: Local file holding API keys for headless testing. Gitignored.
    """

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    scheme: str = "http"
    project_dir: Path = field(default_factory=lambda: REPO_ROOT / "server")
    state_dir: Path = field(default_factory=lambda: REPO_ROOT / ".transformatron")
    env_file: Path = field(default_factory=lambda: REPO_ROOT / ".env")

    @property
    def base_url(self) -> str:
        """Root URL of the transform server."""
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def api_url(self) -> str:
        """Base URL of the v3 protocol API."""
        return f"{self.base_url}/{API_PREFIX}"

    @property
    def seed_url(self) -> str:
        """Seed URL to register with the Maltego client."""
        return f"{self.base_url}/seed"

    @property
    def entrypoint(self) -> Path:
        """Path to the server's ``project.py``."""
        return self.project_dir / "project.py"

    def with_scheme(self, scheme: str) -> TransformatronConfig:
        """Return a copy of this config that addresses the server over ``scheme``."""
        return replace(self, scheme=scheme)

    @property
    def pid_file(self) -> Path:
        return self.state_dir / "server.pid"

    @property
    def scheme_file(self) -> Path:
        """Records the scheme the running server was started with."""
        return self.state_dir / "server.scheme"

    @property
    def log_file(self) -> Path:
        return self.state_dir / "server.log"

    @property
    def cert_file(self) -> Path:
        return self.state_dir / "certs" / "cert.pem"

    @property
    def key_file(self) -> Path:
        return self.state_dir / "certs" / "key.pem"


def load_config() -> TransformatronConfig:
    """Build the config for the server managed by this repository."""
    return TransformatronConfig()
