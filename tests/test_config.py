"""Tests for reading server identity out of ``transformatron.toml``.

Identity is what the Maltego client displays and what every transform id is prefixed with,
so a file that is silently ignored publishes placeholder names under an author's own server.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from transformatron.config import (
    DEFAULT_AUTHOR,
    DEFAULT_NAMESPACE,
    DEFAULT_SERVER_NAME,
    ConfigError,
    _read_identity,
)


def test_a_missing_file_leaves_the_defaults(tmp_path: Path) -> None:
    """A fresh clone has no config file and must still start."""
    assert _read_identity(tmp_path / "transformatron.toml") == {}


def test_identity_is_read_from_the_server_table(tmp_path: Path) -> None:
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text(
        '[server]\nserver_name = "Acme Intel"\nnamespace = "acme.intel"\nauthor = "Acme"\n'
    )

    assert _read_identity(config_file) == {
        "server_name": "Acme Intel",
        "namespace": "acme.intel",
        "author": "Acme",
    }


def test_a_partial_file_only_overrides_what_it_sets(tmp_path: Path) -> None:
    """Setting one key must not blank the others back to empty."""
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text('[server]\nnamespace = "acme.intel"\n')

    assert _read_identity(config_file) == {"namespace": "acme.intel"}


def test_malformed_toml_is_reported_not_ignored(tmp_path: Path) -> None:
    """A typo must not silently fall back to publishing the placeholder identity."""
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text('[server]\nnamespace = "unclosed\n')

    with pytest.raises(ConfigError, match="not valid TOML"):
        _read_identity(config_file)


@pytest.mark.parametrize("value", ['""', '"   "', "42"])
def test_an_unusable_identity_value_is_rejected(tmp_path: Path, value: str) -> None:
    """An empty or non-string namespace would produce transform ids nobody can route."""
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text(f"[server]\nnamespace = {value}\n")

    with pytest.raises(ConfigError, match="non-empty string"):
        _read_identity(config_file)


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    """Forward compatibility: a key this version does not know is not an error."""
    config_file = tmp_path / "transformatron.toml"
    config_file.write_text('[server]\nnamespace = "acme.intel"\nfuture_key = "whatever"\n')

    assert _read_identity(config_file) == {"namespace": "acme.intel"}


def test_defaults_are_the_documented_placeholders() -> None:
    """The README and project.py quote these, so a drift here makes the docs wrong."""
    assert DEFAULT_SERVER_NAME == "New Maltego Integration"
    assert DEFAULT_NAMESPACE == "acme.new_maltego_integration"
    assert DEFAULT_AUTHOR == "Acme Corp"
