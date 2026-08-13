"""MCP tools for running and inspecting a local Maltego v3 transform server.

Every tool here is a thin shell over :mod:`transformatron.operations`, which the
CLI in ``scripts/transformatron_cli.py`` also calls. Keep behaviour in that module
so both front ends stay identical.

Authoring transforms is covered by ``docs/transform-authoring.md`` and the SDK
guidance in ``server/.agents/skills/``. These tools cover what those do not: the
server lifecycle and the transform execution cycle.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from transformatron import operations
from transformatron.config import load_config

server = MCPServer(
    name="transformatron",
    instructions=(
        "Control a local Maltego v3 transform server. Use these tools to start, "
        "stop, and inspect the server and to execute transforms. To write or "
        "modify transform code, read docs/transform-authoring.md first."
    ),
)

CONFIG = load_config()


@server.tool()
def server_start(ssl: bool = False) -> str:
    """Start the local transform server and wait until it answers.

    Args:
        ssl: Serve over HTTPS. Requires generate_certs first. Both the Maltego
            desktop client and the Graph Browser require it.
    """
    return operations.start(CONFIG, ssl=ssl)


@server.tool()
def server_stop() -> str:
    """Stop the running transform server."""
    return operations.stop(CONFIG)


@server.tool()
def server_restart(ssl: bool = False) -> str:
    """Restart the server to pick up new or edited transform modules.

    Args:
        ssl: Serve over HTTPS after restarting.
    """
    return operations.restart(CONFIG, ssl=ssl)


@server.tool()
async def server_status() -> str:
    """Report whether the server is running, healthy, and how many transforms it serves."""
    return await operations.status(CONFIG)


@server.tool()
def server_logs(lines: int = 50) -> str:
    """Return recent server log output.

    Args:
        lines: Number of trailing log lines to return.
    """
    return operations.logs(CONFIG, lines)


@server.tool()
async def list_transforms() -> str:
    """List every transform the running server advertises, with input and output types.

    An output type of NONE means the transform function is missing a return
    annotation, which stops the Maltego client from routing to it.
    """
    return await operations.list_transforms(CONFIG)


@server.tool()
async def get_transform(transform_id: str) -> str:
    """Return the full detail document for one transform.

    Args:
        transform_id: Fully qualified transform name from list_transforms.
    """
    return await operations.get_transform(CONFIG, transform_id)


@server.tool()
async def list_entities() -> str:
    """List the entity types the running server advertises."""
    return await operations.list_entities(CONFIG)


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
    return await operations.run_transform(
        CONFIG, transform_id, entity_type, entity_value, settings, timeout
    )


@server.tool()
def get_seed_url() -> str:
    """Return the seed URL and the steps to register this server with Maltego."""
    return operations.seed_url(CONFIG)


@server.tool()
def generate_certs(force: bool = False) -> str:
    """Generate a self-signed certificate for serving over HTTPS.

    Args:
        force: Overwrite an existing certificate pair.
    """
    return operations.generate_certs(CONFIG, force=force)


def main() -> None:
    """Run the MCP server over stdio."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
