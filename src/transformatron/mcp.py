"""MCP tools for running and inspecting a local Maltego v3 transform server.

Authoring transforms is covered by the official agent skills the SDK installs
into ``server/.agents/skills/``. These tools cover what those skills do not: the
server lifecycle and the transform execution cycle.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer

from transformatron import certs, lifecycle
from transformatron.client import TransformClient, TransformServerError
from transformatron.config import load_config

server = MCPServer(
    name="transformatron",
    instructions=(
        "Control a local Maltego v3 transform server. Use these tools to start, "
        "stop, and inspect the server and to execute transforms. To write or "
        "modify transform code, load the official SDK skills in "
        "server/.agents/skills/ instead."
    ),
)

CONFIG = load_config()


def _format_transform(transform: dict[str, Any]) -> str:
    """Render one transform as a single line of id, input type, and output types."""
    name = transform.get("name", "<unnamed>")
    display = transform.get("displayName", "")
    input_types = ", ".join(transform.get("input", {}).get("typeIds", [])) or "?"
    output_types = ", ".join(transform.get("output", {}).get("typeIds", [])) or "NONE"
    return f"{name}\n    {display}\n    {input_types} -> {output_types}"


@server.tool()
def server_start(ssl: bool = False) -> str:
    """Start the local transform server and wait until it answers.

    Args:
        ssl: Serve over HTTPS. Requires generate_certs first. Needed by the
            Maltego Graph Browser; plain local development does not need it.
    """
    try:
        status = lifecycle.start(CONFIG, ssl=ssl)
    except lifecycle.ServerLifecycleError as exc:
        return f"Failed to start: {exc}"
    return f"{status.detail}\nSeed URL: {CONFIG.seed_url}"


@server.tool()
def server_stop() -> str:
    """Stop the local transform server."""
    return lifecycle.stop(CONFIG)


@server.tool()
def server_restart(ssl: bool = False) -> str:
    """Restart the server to pick up new or edited transform modules.

    Args:
        ssl: Serve over HTTPS after restarting.
    """
    try:
        status = lifecycle.restart(CONFIG, ssl=ssl)
    except lifecycle.ServerLifecycleError as exc:
        return f"Failed to restart: {exc}"
    return f"{status.detail}\nSeed URL: {CONFIG.seed_url}"


@server.tool()
async def server_status() -> str:
    """Report whether the server is running, healthy, and how many transforms it serves."""
    status = lifecycle.status(CONFIG)
    lines = [status.detail]
    if status.healthy:
        try:
            transforms = await TransformClient(CONFIG).list_transforms()
            lines.append(f"Serving {len(transforms)} transforms.")
            lines.append(f"Seed URL: {CONFIG.seed_url}")
        except TransformServerError as exc:
            lines.append(str(exc))
    return "\n".join(lines)


@server.tool()
def server_logs(lines: int = 50) -> str:
    """Return recent server log output.

    Args:
        lines: Number of trailing log lines to return.
    """
    return lifecycle.tail_log(CONFIG, lines)


@server.tool()
async def list_transforms() -> str:
    """List every transform the running server advertises, with input and output types.

    An output type of NONE means the transform function is missing a return
    annotation, which stops the Maltego client from routing to it.
    """
    try:
        transforms = await TransformClient(CONFIG).list_transforms()
    except TransformServerError as exc:
        return str(exc)
    if not transforms:
        return "The server advertises no transforms."
    body = "\n".join(_format_transform(t) for t in transforms)
    return f"{len(transforms)} transforms:\n{body}"


@server.tool()
async def get_transform(transform_id: str) -> str:
    """Return the full detail document for one transform.

    Args:
        transform_id: Fully qualified transform name from list_transforms.
    """
    try:
        detail = await TransformClient(CONFIG).get_transform(transform_id)
    except TransformServerError as exc:
        return str(exc)
    return json.dumps(detail, indent=2)


@server.tool()
async def list_entities() -> str:
    """List the entity types the running server advertises."""
    try:
        entities = await TransformClient(CONFIG).list_entities()
    except TransformServerError as exc:
        return str(exc)
    names = sorted(str(e.get("name") or e.get("id") or e) for e in entities)
    return f"{len(names)} entities:\n" + "\n".join(names)


@server.tool()
async def run_transform(
    transform_id: str,
    entity_type: str,
    entity_value: str,
    settings: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> str:
    """Run a transform against one input entity and return its results.

    Args:
        transform_id: Fully qualified transform name from list_transforms.
        entity_type: Maltego type of the input entity, e.g. maltego.Domain.
        entity_value: Value carried by the input entity.
        settings: Optional transform settings, keyed by setting name.
        timeout: Seconds to wait before cancelling the run.
    """
    try:
        result = await TransformClient(CONFIG).run_transform(
            transform_id, entity_type, entity_value, settings, timeout
        )
    except TransformServerError as exc:
        return str(exc)

    lines = [f"State: {result.state} ({'success' if result.succeeded else 'failure'})"]
    if result.messages:
        lines.append("Messages:")
        lines += [f"  - {m}" for m in result.messages]
    lines.append(f"Entities ({len(result.entities)}):")
    lines += [f"  {json.dumps(e)}" for e in result.entities]
    if result.links:
        lines.append(f"Links ({len(result.links)}):")
        lines += [f"  {json.dumps(link)}" for link in result.links]
    return "\n".join(lines)


@server.tool()
def get_seed_url() -> str:
    """Return the seed URL and the steps to register this server with Maltego."""
    return (
        f"Seed URL: {CONFIG.seed_url}\n\n"
        "To register it in the Maltego client:\n"
        "1. Start the server (server_start) and confirm it is healthy.\n"
        "2. In Maltego, open the Transforms tab and choose Transform Hub.\n"
        "3. Add a local Transform Hub item and paste the seed URL above.\n"
        "4. Install the item, then run a transform from a matching entity.\n\n"
        "The Graph Browser client requires HTTPS: run generate_certs and restart "
        "with ssl=true."
    )


@server.tool()
def generate_certs(force: bool = False) -> str:
    """Generate a self-signed certificate for serving over HTTPS.

    Args:
        force: Overwrite an existing certificate pair.
    """
    try:
        pair = certs.generate(CONFIG, force=force)
    except certs.CertificateError as exc:
        return f"Failed to generate certificates: {exc}"
    return (
        f"Certificate: {pair.cert_file}\n"
        f"Private key: {pair.key_file}\n\n"
        "Restart with ssl=true to serve over HTTPS.\n\n"
        "To trust this certificate, run (changes system trust, so run it yourself):\n"
        f"  {pair.trust_command}"
    )


def main() -> None:
    """Run the MCP server over stdio."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
