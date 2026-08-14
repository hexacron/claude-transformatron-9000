"""Server operations shared by every front end.

The MCP tools and the CLI are both thin shells over this module, so an agent using
MCP and a human using the terminal get identical behaviour and identical output.
Add an operation here, not in a front end.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from transformatron import certs, lifecycle
from transformatron.client import TransformClient, TransformServerError
from transformatron.config import TransformatronConfig
from transformatron.scaffold import (
    parse_curl_command,
    parse_openapi_spec,
    write_scaffold,
)


def format_transform(transform: dict[str, Any]) -> str:
    """Render one transform as id, display name, and input/output types."""
    name = transform.get("name", "<unnamed>")
    display = transform.get("displayName", "")
    input_types = ", ".join(transform.get("input", {}).get("typeIds", [])) or "?"
    output_types = ", ".join(transform.get("output", {}).get("typeIds", [])) or "NONE"
    return f"{name}\n    {display}\n    {input_types} -> {output_types}"


def start(config: TransformatronConfig, ssl: bool = False) -> str:
    """Start the server and report the result."""
    try:
        return lifecycle.start(config, ssl=ssl).detail
    except lifecycle.ServerLifecycleError as exc:
        return f"Failed to start: {exc}"


def stop(config: TransformatronConfig) -> str:
    """Stop the server and report the result."""
    try:
        return lifecycle.stop(config)
    except OSError as exc:
        return f"Failed to stop: {exc}"


def restart(config: TransformatronConfig, ssl: bool | None = None) -> str:
    """Restart the server, picking up edited transform modules.

    With ``ssl=None`` the running server's scheme is preserved.
    """
    try:
        return lifecycle.restart(config, ssl=ssl).detail
    except lifecycle.ServerLifecycleError as exc:
        return f"Failed to restart: {exc}"


async def status(config: TransformatronConfig) -> str:
    """Report whether the server is running, healthy, and how many transforms it serves."""
    state = lifecycle.status(config)
    lines = [state.detail]
    if state.healthy:
        resolved = lifecycle.resolve_config(config)
        try:
            transforms = await TransformClient(resolved).list_transforms()
            lines.append(f"Serving {len(transforms)} transforms.")
            lines.append(f"Seed URL: {resolved.seed_url}")
        except TransformServerError as exc:
            lines.append(str(exc))
    return "\n".join(lines)


async def list_transforms(config: TransformatronConfig) -> str:
    """List every transform the server advertises."""
    try:
        transforms = await TransformClient(lifecycle.resolve_config(config)).list_transforms()
    except TransformServerError as exc:
        return str(exc)
    if not transforms:
        return "The server advertises no transforms."
    body = "\n".join(format_transform(t) for t in transforms)
    return f"{len(transforms)} transforms:\n{body}"


async def get_transform(config: TransformatronConfig, transform_id: str) -> str:
    """Return the full detail document for one transform."""
    try:
        detail = await TransformClient(lifecycle.resolve_config(config)).get_transform(transform_id)
    except TransformServerError as exc:
        return str(exc)
    return json.dumps(detail, indent=2)


async def list_entities(config: TransformatronConfig) -> str:
    """List the entity types the server advertises."""
    try:
        entities = await TransformClient(lifecycle.resolve_config(config)).list_entities()
    except TransformServerError as exc:
        return str(exc)
    names = sorted(str(e.get("name") or e.get("id") or e) for e in entities)
    return f"{len(names)} entities:\n" + "\n".join(names)


async def run_transform(
    config: TransformatronConfig,
    transform_id: str,
    entity_type: str,
    entity_value: str,
    settings: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> str:
    """Run one transform against one input entity and render the result.

    The rendered output always states the entity count, because a transform can
    report success while producing nothing — see docs/transform-authoring.md.
    """
    try:
        result = await TransformClient(lifecycle.resolve_config(config)).run_transform(
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


def seed_url(config: TransformatronConfig) -> str:
    """Return the seed URL and the steps to register this server with Maltego."""
    return (
        f"Seed URL: {lifecycle.resolve_config(config).seed_url}\n\n"
        "To register it in the Maltego client:\n"
        "1. Start the server and confirm it is healthy.\n"
        "2. In Maltego, open the Transforms tab and choose Transform Hub.\n"
        "3. Add a local Transform Hub item and paste the seed URL above.\n"
        "4. Install the item, then run a transform from a matching entity.\n\n"
        "The desktop client and the Graph Browser both require HTTPS: generate "
        "certificates and restart with SSL enabled."
    )


def generate_certs(config: TransformatronConfig, force: bool = False) -> str:
    """Generate a self-signed certificate for serving over HTTPS."""
    try:
        pair = certs.generate(config, force=force)
    except certs.CertificateError as exc:
        return f"Failed to generate certificates: {exc}"
    return (
        f"Certificate: {pair.cert_file}\n"
        f"Private key: {pair.key_file}\n\n"
        "Restart with SSL enabled to serve over HTTPS.\n\n"
        "To trust this certificate, run (changes system trust, so run it yourself):\n"
        f"  {pair.trust_command}"
    )


def logs(config: TransformatronConfig, lines: int = 50) -> str:
    """Return recent server log output."""
    return lifecycle.tail_log(config, lines)


def scaffold(
    config: TransformatronConfig,
    service: str | None = None,
    curl: str | None = None,
    openapi: str | None = None,
    sample_response: dict[str, Any] | str | None = None,
) -> str:
    """Scaffold a new Maltego transform module package from a cURL command or OpenAPI spec."""
    if not curl and not openapi:
        return "Failed to scaffold: Provide either a --curl command or an --openapi spec."

    # Only the failures a caller can act on are caught: a malformed spec, a cURL command
    # with no URL, or a service that already exists. A bare `except Exception` here would
    # also swallow the FileExistsError guarding hand-written modules and report it as
    # ordinary prose, and would hide genuine bugs in the generator behind a string.
    try:
        if curl:
            scaffold_cfg = parse_curl_command(
                curl_cmd=curl,
                sample_response=sample_response,
                service_name=service,
            )
        else:
            openapi_path = Path(openapi or "")
            openapi_content = openapi_path.read_text() if openapi_path.is_file() else openapi or ""
            scaffold_cfg = parse_openapi_spec(
                spec=openapi_content,
                service_name=service,
            )

        created_files = write_scaffold(scaffold_cfg, config.project_dir)
    except ValueError as exc:
        # json.JSONDecodeError subclasses ValueError, so a malformed spec lands here too.
        return f"Failed to scaffold: {exc}"
    except FileExistsError as exc:
        return f"Failed to scaffold: {exc}"
    except OSError as exc:
        return f"Failed to scaffold: could not write to {config.project_dir}: {exc}"

    file_lines = "\n".join(f"  - {f}" for f in created_files)
    transforms_list = "\n".join(
        f"  - {t.display_name} ({t.input_entity} -> {', '.join(t.output_entity_types)})"
        for t in scaffold_cfg.transforms
    )

    return (
        f"Scaffolded '{scaffold_cfg.display_name}' ({scaffold_cfg.service_id}):\n\n"
        f"Created files:\n{file_lines}\n\n"
        f"Transforms:\n{transforms_list}\n\n"
        f"Import added to server/project.py.\n"
        f"Next steps:\n"
        f"1. Run 'transformatron_cli.py restart' to load the new transform.\n"
        f"2. Run 'transformatron_cli.py list' to confirm registration.\n"
        f"3. Run 'smoke_test_transforms.py' to verify entity output."
    )
