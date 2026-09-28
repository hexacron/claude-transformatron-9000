"""MCP tools for running and inspecting a local Maltego v3 transform server.

Every tool here is a thin shell over :mod:`transformatron.operations`, which the
CLI in ``scripts/transformatron_cli.py`` also calls. Keep behaviour in that module
so both front ends stay identical.

Authoring transforms is covered by ``docs/transform-authoring.md`` and the SDK
guidance in ``server/.agents/skills/``. These tools cover what those do not: the
server lifecycle and the transform execution cycle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Concatenate

from mcp.server.mcpserver import MCPServer

from transformatron import operations
from transformatron.config import ConfigError, TransformatronConfig, load_config

server = MCPServer(
    name="transformatron",
    instructions=(
        "Control a local Maltego v3 transform server. Use these tools to start, "
        "stop, and inspect the server and to execute transforms. To write or "
        "modify transform code, read docs/transform-authoring.md first."
    ),
)


# Config is loaded on every call, not once at import, for the same reason the CLI loads it
# per command: an edit to transformatron.toml must apply to the next tool call without
# restarting the MCP client, and a typo in the file must come back to the agent as a
# message it can act on rather than stopping this server from starting at all.


async def _blocking[**P](
    operation: Callable[Concatenate[TransformatronConfig, P], str],
    *args: P.args,
    **kwargs: P.kwargs,
) -> str:
    """Run a synchronous operation off the event loop with freshly loaded config.

    Starting and stopping the server wait for seconds, and the others touch disk or the
    network. Run on the loop, any of them would stall every other request this server
    is handling.
    """
    try:
        config = load_config()
    except ConfigError as exc:
        return f"Failed: {exc}"
    # str() drops the operations.Failure subtype: MCP tools return plain text.
    return str(await asyncio.to_thread(operation, config, *args, **kwargs))


async def _async[**P](
    operation: Callable[Concatenate[TransformatronConfig, P], Awaitable[str]],
    *args: P.args,
    **kwargs: P.kwargs,
) -> str:
    """Await an async operation with freshly loaded config."""
    try:
        config = load_config()
    except ConfigError as exc:
        return f"Failed: {exc}"
    return str(await operation(config, *args, **kwargs))


@server.tool()
async def server_start(ssl: bool = False) -> str:
    """Start the local transform server and wait until it answers.

    Args:
        ssl: Serve over HTTPS. Requires generate_certs first. Both the Maltego
            desktop client and the Graph Browser require it.
    """
    return await _blocking(operations.start, ssl=ssl)


@server.tool()
async def server_stop() -> str:
    """Stop the running transform server."""
    return await _blocking(operations.stop)


@server.tool()
async def server_restart(ssl: bool | None = None) -> str:
    """Restart the server to pick up new or edited transform modules.

    Args:
        ssl: Serve over HTTPS after restarting. Left unset, the scheme the
            server is already running under is preserved, so reloading an
            HTTPS server keeps the Maltego desktop client working.
    """
    return await _blocking(operations.restart, ssl=ssl)


@server.tool()
async def server_status() -> str:
    """Report whether the server is running, healthy, and how many transforms it serves."""
    return await _async(operations.status)


@server.tool()
async def server_logs(lines: int = 50) -> str:
    """Return recent server log output.

    Args:
        lines: Number of trailing log lines to return.
    """
    return await _blocking(operations.logs, lines)


@server.tool()
async def list_transforms() -> str:
    """List every transform the running server advertises, with input and output types.

    An output type of NONE means the transform function is missing a return
    annotation, which stops the Maltego client from routing to it.
    """
    return await _async(operations.list_transforms)


@server.tool()
async def get_transform(transform_id: str) -> str:
    """Return the full detail document for one transform.

    Args:
        transform_id: Fully qualified transform name from list_transforms.
    """
    return await _async(operations.get_transform, transform_id)


@server.tool()
async def list_entities() -> str:
    """List the entity types the running server advertises."""
    return await _async(operations.list_entities)


@server.tool()
async def run_transform(
    transform_id: str,
    entity_type: str,
    entity_value: str,
    settings: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> str:
    """Run a transform against one input entity and return its results.

    Check the reported entity count: a transform can report success while
    producing nothing. See docs/transform-authoring.md.

    Args:
        transform_id: Fully qualified transform name from list_transforms.
        entity_type: Maltego type of the input entity, e.g. maltego.Domain.
        entity_value: Value carried by the input entity.
        settings: Optional transform settings, keyed by setting name.
        timeout: Seconds to wait before cancelling the run.
    """
    return await _async(
        operations.run_transform, transform_id, entity_type, entity_value, settings, timeout
    )


@server.tool()
async def get_seed_url() -> str:
    """Return the seed URL and the steps to register this server with Maltego."""
    return await _blocking(operations.seed_url)


@server.tool()
async def generate_certs(force: bool = False) -> str:
    """Generate a self-signed certificate for serving over HTTPS.

    Args:
        force: Overwrite an existing certificate pair.
    """
    return await _blocking(operations.generate_certs, force=force)


@server.tool()
async def scaffold_transform(
    service: str | None = None,
    curl: str | None = None,
    openapi: str | None = None,
    sample_response: str | None = None,
    operation_ids: list[str] | None = None,
    force: bool = False,
) -> str:
    """Scaffold a new Maltego transform module from a cURL command or OpenAPI specification.

    Args:
        service: Optional service name slug (e.g. 'greynoise', 'threatfox').
        curl: A full cURL command string demonstrating an API request.
        openapi: An OpenAPI/Swagger JSON spec: a URL, a file path, or the document itself.
        sample_response: Sample JSON response for a cURL command, to infer output fields.
        operation_ids: OpenAPI operation ids to scaffold. Without it every GET operation is
            scaffolded; POST operations are only scaffolded when named here.
        force: Overwrite an existing service directory.
    """
    return await _blocking(
        operations.scaffold,
        service=service,
        curl=curl,
        openapi=openapi,
        sample_response=sample_response,
        operations=operation_ids,
        force=force,
    )


def main() -> None:
    """Run the MCP server over stdio."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
