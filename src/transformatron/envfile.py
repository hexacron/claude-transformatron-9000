"""Read API keys from a local ``.env`` file.

Transforms read their credentials from the Maltego client's transform settings first and
fall back to the process environment, which is what makes headless testing possible. That
fallback only works if the key is exported in the shell that starts the server, which is
easy to get wrong: exports do not survive between shells, and a key set after the server
started never reaches it.

This module closes that gap. ``.env`` at the repository root is read at server start and
merged into the subprocess environment, so a key written once survives restarts.

The file is gitignored (see ``.gitignore``) and holds real credentials. ``.env.example``
is the committed template and must never hold a real value.

The format is deliberately minimal — ``KEY=value``, one per line, ``#`` comments, blank
lines ignored, optional surrounding quotes stripped. It is not a shell script and is not
executed: no interpolation, no ``export`` prefix handling, no multi-line values. Anything
more belongs in the Maltego client's transform settings, which is the intended home for
credentials in a real deployment.
"""

from __future__ import annotations

from pathlib import Path


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines into a mapping.

    Args:
        text: Raw contents of an env file.

    Returns:
        The parsed keys. Malformed lines are skipped rather than raising, so one bad
        line cannot stop a server from starting.
    """
    values: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        key, separator, value = line.partition("=")
        if not separator:
            continue

        key = key.strip()
        if not key:
            continue

        # Quotes are stripped so a value with spaces can be written either way. Only a
        # matched pair is removed, so a value that legitimately ends in a quote survives.
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        values[key] = value

    return values


def load_env_file(env_file: Path) -> dict[str, str]:
    """Return the keys defined in `env_file`, or an empty mapping when it is absent.

    A missing file is the normal case for a fresh clone and is not an error.
    """
    if not env_file.is_file():
        return {}
    return parse_env_file(env_file.read_text())
