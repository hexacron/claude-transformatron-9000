"""Configuration for the local Maltego v3 transform server."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when ``transformatron.toml`` is present but cannot be used."""


REPO_ROOT = Path(__file__).resolve().parents[2]

# The SDK's ServerHTTPSettings defaults to 127.0.0.1:3000, and server/project.py asks for
# protocol="https" — though the CLI and MCP server override the scheme per start through
# MALTEGO_SERVER_* variables. The skill scripts shipped with the SDK default to port 8080
# instead, so the port is always stated explicitly here rather than relying on any single
# default.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3000
API_PREFIX = "api/v3"

# Identity published to the Maltego client in the seed and on every transform id.
# These are the placeholders a fresh clone starts with; `transformatron.toml` at the
# repository root overrides them, so adopting this repository does not mean editing
# server/project.py. The namespace prefixes every transform id, so two servers that
# share one need distinguishing before both are registered in the same client.
DEFAULT_SERVER_NAME = "New Maltego Integration"
DEFAULT_NAMESPACE = "acme.new_maltego_integration"
DEFAULT_AUTHOR = "Acme Corp"

CONFIG_FILE_NAME = "transformatron.toml"


@dataclass(frozen=True)
class TransformatronConfig:
    """Locations and connection details for the managed transform server.

    Attributes:
        host: Interface the transform server binds to.
        port: TCP port the transform server listens on.
        scheme: ``http`` for local development, ``https`` for Graph Browser.
        server_name: Display name the Maltego client shows for this server.
        namespace: Prefix on every transform id, e.g. ``acme.my_integration``.
        author: Author string published in the seed.
        project_dir: Directory holding ``project.py`` and ``transforms/``.
        state_dir: Directory for the PID file, log file, and generated certs.
        env_file: Local file holding API keys for headless testing. Gitignored.
    """

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    scheme: str = "http"
    server_name: str = DEFAULT_SERVER_NAME
    namespace: str = DEFAULT_NAMESPACE
    author: str = DEFAULT_AUTHOR
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


STRING_KEYS = ("server_name", "namespace", "author", "host")

# Ports below 1024 need root on Unix, and 0 would hand out an ephemeral port the client
# could not be told about in advance.
MIN_PORT = 1024
MAX_PORT = 65535


def _read_server_table(config_file: Path) -> dict[str, str | int]:
    """Read the ``[server]`` keys from ``transformatron.toml``.

    Args:
        config_file: Path to the TOML file. A missing file is not an error — the
            defaults are what a fresh clone runs with.

    Returns:
        The fields that were set, ready to pass to ``TransformatronConfig``.

    Raises:
        ConfigError: If the file is present but unparseable, or a known key holds an
            unusable value. Staying silent here would publish placeholder identity under
            a name the author believed they had changed, or bind a port they did not ask
            for and then report the wrong one in the seed URL.
    """
    if not config_file.is_file():
        return {}

    try:
        parsed = tomllib.loads(config_file.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{config_file} is not valid TOML: {exc}") from exc

    server = parsed.get("server", {})
    if not isinstance(server, dict):
        raise ConfigError(f"{config_file}: [server] must be a table, got {type(server).__name__}.")

    settings: dict[str, str | int] = {}
    for key in STRING_KEYS:
        if key not in server:
            continue
        value = server[key]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{config_file}: [server].{key} must be a non-empty string.")
        settings[key] = value

    if "port" in server:
        settings["port"] = _validate_port(server["port"], config_file)
    return settings


def _validate_port(value: object, config_file: Path) -> int:
    """Return ``value`` as a usable TCP port, or raise ``ConfigError``.

    ``bool`` is rejected explicitly because it is a subclass of ``int``, so ``port = true``
    would otherwise bind port 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{config_file}: [server].port must be a whole number, got {value!r}.")
    if not MIN_PORT <= value <= MAX_PORT:
        raise ConfigError(
            f"{config_file}: [server].port must be between {MIN_PORT} and {MAX_PORT}, got {value}."
        )
    return value


def load_config() -> TransformatronConfig:
    """Build the config for the server managed by this repository.

    Identity and address come from ``transformatron.toml`` at the repository root when
    present, so adopting this repository does not require editing ``server/project.py``.
    """
    # Applied field by field rather than splatted: a **kwargs spread would let a stray key
    # in the file reach project_dir or state_dir, which are not meant to be configurable.
    settings = _read_server_table(REPO_ROOT / CONFIG_FILE_NAME)
    return TransformatronConfig(
        host=str(settings.get("host", DEFAULT_HOST)),
        port=int(settings.get("port", DEFAULT_PORT)),
        server_name=str(settings.get("server_name", DEFAULT_SERVER_NAME)),
        namespace=str(settings.get("namespace", DEFAULT_NAMESPACE)),
        author=str(settings.get("author", DEFAULT_AUTHOR)),
    )
