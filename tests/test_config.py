"""Tests for reading server settings out of ``transformatron.toml``.

Identity is what the Maltego client displays and what every transform id is prefixed with,
so a file that is silently ignored publishes placeholder names under an author's own server.
The address matters for the same reason: a port that is read wrong is reported wrong in the
seed URL the user pastes into Maltego.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from transformatron import mcp
from transformatron.config import (
    CONFIG_FILE_NAME,
    DEFAULT_AUTHOR,
    DEFAULT_HOST,
    DEFAULT_NAMESPACE,
    DEFAULT_PORT,
    DEFAULT_SERVER_NAME,
    ConfigError,
    TransformatronConfig,
    _read_server_table,
    load_config,
)


def test_a_missing_file_leaves_the_defaults(tmp_path: Path) -> None:
    """A fresh clone has no config file and must still start."""
    assert _read_server_table(tmp_path / "transformatron.toml") == {}


def test_identity_is_read_from_the_server_table(tmp_path: Path) -> None:
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text(
        '[server]\nserver_name = "Acme Intel"\nnamespace = "acme.intel"\nauthor = "Acme"\n'
    )

    assert _read_server_table(config_file) == {
        "server_name": "Acme Intel",
        "namespace": "acme.intel",
        "author": "Acme",
    }


def test_a_partial_file_only_overrides_what_it_sets(tmp_path: Path) -> None:
    """Setting one key must not blank the others back to empty."""
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text('[server]\nnamespace = "acme.intel"\n')

    assert _read_server_table(config_file) == {"namespace": "acme.intel"}


def test_malformed_toml_is_reported_not_ignored(tmp_path: Path) -> None:
    """A typo must not silently fall back to publishing the placeholder identity."""
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text('[server]\nnamespace = "unclosed\n')

    with pytest.raises(ConfigError, match="not valid TOML"):
        _read_server_table(config_file)


@pytest.mark.parametrize("value", ['""', '"   "', "42"])
def test_an_unusable_identity_value_is_rejected(tmp_path: Path, value: str) -> None:
    """An empty or non-string namespace would produce transform ids nobody can route."""
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text(f"[server]\nnamespace = {value}\n")

    with pytest.raises(ConfigError, match="non-empty string"):
        _read_server_table(config_file)


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    """Forward compatibility: a key this version does not know is not an error."""
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text('[server]\nnamespace = "acme.intel"\nfuture_key = "whatever"\n')

    assert _read_server_table(config_file) == {"namespace": "acme.intel"}


def test_the_address_is_read_from_the_server_table(tmp_path: Path) -> None:
    """Port 3000 collides with anything already on it, including a second clone."""
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text('[server]\nhost = "0.0.0.0"\nport = 8080\n')

    assert _read_server_table(config_file) == {"host": "0.0.0.0", "port": 8080}


@pytest.mark.parametrize("value", ["0", "80", "65536", "99999", "-1"])
def test_an_unusable_port_is_rejected(tmp_path: Path, value: str) -> None:
    """Binding fails late and obscurely; the file is where the mistake is visible."""
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text(f"[server]\nport = {value}\n")

    with pytest.raises(ConfigError, match="between"):
        _read_server_table(config_file)


@pytest.mark.parametrize("value", ['"8080"', "true", "8080.5"])
def test_a_non_integer_port_is_rejected(tmp_path: Path, value: str) -> None:
    """``true`` is an int subclass in Python, so it would otherwise bind port 1."""
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text(f"[server]\nport = {value}\n")

    with pytest.raises(ConfigError, match="whole number"):
        _read_server_table(config_file)


def test_the_port_reaches_the_urls_the_user_pastes() -> None:
    """A port read but not propagated sends the user to the wrong seed URL."""
    config = TransformatronConfig(port=8080)

    assert config.base_url == "http://127.0.0.1:8080"
    assert config.seed_url == "http://127.0.0.1:8080/seed"
    assert config.api_url == "http://127.0.0.1:8080/api/v3"


def test_the_file_reaches_the_loaded_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading the file is only half the job — load_config has to apply what it read.

    Without this, dropping ``port=`` from the ``TransformatronConfig`` call leaves every
    test passing while the server keeps binding 3000.
    """
    config_file = tmp_path / CONFIG_FILE_NAME
    config_file.write_text('[server]\nhost = "0.0.0.0"\nport = 8080\nnamespace = "acme.intel"\n')
    monkeypatch.setattr("transformatron.config.REPO_ROOT", tmp_path)

    config = load_config()

    assert config.host == "0.0.0.0"
    assert config.port == 8080
    assert config.namespace == "acme.intel"
    assert config.seed_url == "http://0.0.0.0:8080/seed"


def test_a_missing_file_loads_the_documented_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh clone has no config file and must still bind the documented address."""
    monkeypatch.setattr("transformatron.config.REPO_ROOT", tmp_path)

    config = load_config()

    assert (config.host, config.port) == (DEFAULT_HOST, DEFAULT_PORT)
    assert config.namespace == DEFAULT_NAMESPACE


def test_defaults_are_the_documented_placeholders() -> None:
    """The README and project.py quote these, so a drift here makes the docs wrong."""
    assert DEFAULT_SERVER_NAME == "New Maltego Integration"
    assert DEFAULT_NAMESPACE == "acme.new_maltego_integration"
    assert DEFAULT_AUTHOR == "Acme Corp"


async def test_mcp_tools_apply_an_edited_file_on_the_next_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The MCP server is long-lived; a port changed in the file must not need a client restart."""
    monkeypatch.setattr("transformatron.config.REPO_ROOT", tmp_path)
    config_file = tmp_path / CONFIG_FILE_NAME

    config_file.write_text("[server]\nport = 8080\n")
    assert ":8080/seed" in await mcp.get_seed_url()

    config_file.write_text("[server]\nport = 9090\n")
    assert ":9090/seed" in await mcp.get_seed_url()


async def test_mcp_tools_report_a_bad_file_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo made while the MCP server runs must reach the agent as a message it can fix."""
    monkeypatch.setattr("transformatron.config.REPO_ROOT", tmp_path)
    (tmp_path / CONFIG_FILE_NAME).write_text("[server]\nport = 80\n")

    result = await mcp.server_status()

    assert result.startswith("Failed: ")
    assert "[server].port must be between" in result
