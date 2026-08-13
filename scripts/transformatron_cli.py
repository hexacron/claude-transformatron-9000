"""Command-line front end for the local Maltego transform server.

Every operation the MCP tools expose is available here, calling the same
:mod:`transformatron.operations` functions, so behaviour and output are identical.
Use this from a terminal, a shell script, or any coding agent that can run commands
but does not speak MCP.

Usage:
    uv run python scripts/transformatron_cli.py status
    uv run python scripts/transformatron_cli.py start --ssl
    uv run python scripts/transformatron_cli.py restart --ssl
    uv run python scripts/transformatron_cli.py list
    uv run python scripts/transformatron_cli.py run <id> maltego.IPv4Address 8.8.8.8
    uv run python scripts/transformatron_cli.py logs --lines 100
    uv run python scripts/transformatron_cli.py certs
    uv run python scripts/transformatron_cli.py seed-url
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Coroutine
from typing import Any

from transformatron import operations
from transformatron.config import load_config


def _resolve(value: Coroutine[Any, Any, str]) -> str:
    """Run an async operation to completion and return its output."""
    return asyncio.run(value)


def _parse_settings(pairs: list[str] | None) -> dict[str, str] | None:
    """Parse repeated ``KEY=VALUE`` arguments into a settings mapping."""
    if not pairs:
        return None
    settings: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator:
            raise SystemExit(f"Invalid --setting {pair!r}: expected KEY=VALUE")
        settings[key] = value
    return settings


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for every supported command."""
    parser = argparse.ArgumentParser(
        prog="transformatron_cli",
        description="Control a local Maltego v3 transform server.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start the server")
    start.add_argument("--ssl", action="store_true", help="Serve over HTTPS")

    sub.add_parser("stop", help="Stop the server")

    restart = sub.add_parser("restart", help="Restart to pick up edited transforms")
    restart.add_argument("--ssl", action="store_true", help="Serve over HTTPS")

    sub.add_parser("status", help="Report running state, health, and transform count")
    sub.add_parser("list", help="List advertised transforms with input/output types")
    sub.add_parser("entities", help="List advertised entity types")
    sub.add_parser("seed-url", help="Print the seed URL and registration steps")

    show = sub.add_parser("show", help="Print the detail document for one transform")
    show.add_argument("transform_id")

    logs = sub.add_parser("logs", help="Print recent server log output")
    logs.add_argument("--lines", type=int, default=50)

    certs = sub.add_parser("certs", help="Generate a self-signed certificate for HTTPS")
    certs.add_argument("--force", action="store_true", help="Overwrite an existing pair")

    run = sub.add_parser("run", help="Run one transform against one input entity")
    run.add_argument("transform_id")
    run.add_argument("entity_type", help="e.g. maltego.IPv4Address")
    run.add_argument("entity_value")
    run.add_argument("--setting", action="append", metavar="KEY=VALUE")
    run.add_argument("--timeout", type=float, default=60.0)

    return parser


def dispatch(args: argparse.Namespace) -> str:
    """Route a parsed command to its operation and return the rendered output."""
    config = load_config()
    match args.command:
        case "start":
            return operations.start(config, ssl=args.ssl)
        case "stop":
            return operations.stop(config)
        case "restart":
            return operations.restart(config, ssl=args.ssl)
        case "status":
            return _resolve(operations.status(config))
        case "list":
            return _resolve(operations.list_transforms(config))
        case "entities":
            return _resolve(operations.list_entities(config))
        case "show":
            return _resolve(operations.get_transform(config, args.transform_id))
        case "logs":
            return operations.logs(config, args.lines)
        case "certs":
            return operations.generate_certs(config, force=args.force)
        case "seed-url":
            return operations.seed_url(config)
        case "run":
            return _resolve(
                operations.run_transform(
                    config,
                    args.transform_id,
                    args.entity_type,
                    args.entity_value,
                    _parse_settings(args.setting),
                    args.timeout,
                )
            )
    raise SystemExit(f"Unknown command: {args.command}")


def main() -> int:
    args = build_parser().parse_args()
    print(dispatch(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
