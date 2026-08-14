"""Tests for reading API keys from a local .env file."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from transformatron import lifecycle
from transformatron.config import TransformatronConfig
from transformatron.envfile import load_env_file, parse_env_file


def test_parses_keys_comments_and_blank_lines() -> None:
    text = """
# a comment
GREYNOISE_API_KEY=abc123

URLSCAN_API_KEY=def456
    # indented comment
"""

    assert parse_env_file(text) == {
        "GREYNOISE_API_KEY": "abc123",
        "URLSCAN_API_KEY": "def456",
    }


def test_strips_matched_surrounding_quotes() -> None:
    """Quoted values are common in env files copied from shell exports."""
    parsed = parse_env_file("A='single'\nB=\"double\"\nC=bare\nD='unmatched\n")

    assert parsed == {"A": "single", "B": "double", "C": "bare", "D": "'unmatched"}


def test_keeps_values_containing_equals_signs() -> None:
    """Base64 and JWT-style keys carry `=` padding, which must survive the split."""
    assert parse_env_file("KEY=abc==\n") == {"KEY": "abc=="}


def test_skips_malformed_lines_without_raising() -> None:
    """One bad line must not stop a server from starting."""
    assert parse_env_file("GOOD=1\nthis line has no equals sign\n=novalue\n") == {"GOOD": "1"}


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """A fresh clone has no .env, which is the normal case."""
    assert load_env_file(tmp_path / "absent.env") == {}


def test_server_env_includes_env_file_keys(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GREYNOISE_API_KEY=from-file\n")
    config = dataclasses.replace(TransformatronConfig(), env_file=env_file)

    env = lifecycle.build_server_env(config)

    assert env["GREYNOISE_API_KEY"] == "from-file"


def test_exported_variable_beats_env_file(tmp_path: Path, monkeypatch) -> None:
    """A one-off export on the command line has to override what is written down."""
    env_file = tmp_path / ".env"
    env_file.write_text("GREYNOISE_API_KEY=from-file\n")
    monkeypatch.setenv("GREYNOISE_API_KEY", "from-shell")
    config = dataclasses.replace(TransformatronConfig(), env_file=env_file)

    env = lifecycle.build_server_env(config)

    assert env["GREYNOISE_API_KEY"] == "from-shell"


def test_env_file_does_not_override_server_pinning(tmp_path: Path) -> None:
    """The port and address are ours to set; a stray .env entry must not move them."""
    env_file = tmp_path / ".env"
    env_file.write_text("MALTEGO_SERVER_HTTP_PORT=9999\n")
    config = dataclasses.replace(TransformatronConfig(), env_file=env_file)

    env = lifecycle.build_server_env(config)

    assert env["MALTEGO_SERVER_HTTP_PORT"] == str(config.port)
