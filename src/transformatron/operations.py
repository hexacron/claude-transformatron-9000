"""Server operations shared by every front end.

The MCP tools and the CLI are both thin shells over this module, so an agent using
MCP and a human using the terminal get identical behaviour and identical output.
Add an operation here, not in a front end.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx

from transformatron import certs, lifecycle
from transformatron.client import TransformClient, TransformServerError
from transformatron.config import TransformatronConfig
from transformatron.scaffold import (
    parse_curl_command,
    parse_openapi_spec,
    write_scaffold,
)


class Failure(str):
    """Output of an operation that did not do what it was asked to.

    It is still a ``str``, so the MCP tools hand it to the agent as ordinary text and
    anything that only renders output need not care. The CLI checks
    ``isinstance(output, Failure)`` to choose its exit code. Failure is carried in the type
    rather than read back out of the wording because the wording is not reliable: a
    transform's own status message can say "Failed" on a run that succeeded.
    """

    __slots__ = ()


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
    except (lifecycle.ServerLifecycleError, OSError) as exc:
        return Failure(f"Failed to start: {exc}")


def stop(config: TransformatronConfig) -> str:
    """Stop the server and report the result."""
    try:
        return lifecycle.stop(config)
    except (lifecycle.ServerLifecycleError, OSError) as exc:
        return Failure(f"Failed to stop: {exc}")


def restart(config: TransformatronConfig, ssl: bool | None = None) -> str:
    """Restart the server, picking up edited transform modules.

    With ``ssl=None`` the running server's scheme is preserved.
    """
    try:
        return lifecycle.restart(config, ssl=ssl).detail
    except (lifecycle.ServerLifecycleError, OSError) as exc:
        return Failure(f"Failed to restart: {exc}")


async def status(config: TransformatronConfig) -> str:
    """Report whether the server is running, healthy, and how many transforms it serves.

    Reported as a failure unless the server answers and lists its transforms, so a script
    can gate on ``status`` before running anything against the server.
    """
    # The health probe is a blocking HTTP call; running it on the event loop would stall
    # every other MCP request for as long as the probe waits.
    state = await asyncio.to_thread(lifecycle.status, config)
    lines = [state.detail]
    if not state.healthy:
        return Failure("\n".join(lines))
    resolved = lifecycle.resolve_config(config)
    try:
        transforms = await TransformClient(resolved).list_transforms()
    except TransformServerError as exc:
        lines.append(str(exc))
        return Failure("\n".join(lines))
    lines.append(f"Serving {len(transforms)} transforms.")
    lines.append(f"Seed URL: {resolved.seed_url}")
    return "\n".join(lines)


async def list_transforms(config: TransformatronConfig) -> str:
    """List every transform the server advertises."""
    try:
        transforms = await TransformClient(lifecycle.resolve_config(config)).list_transforms()
    except TransformServerError as exc:
        return Failure(str(exc))
    if not transforms:
        return "The server advertises no transforms."
    body = "\n".join(format_transform(t) for t in transforms)
    return f"{len(transforms)} transforms:\n{body}"


async def get_transform(config: TransformatronConfig, transform_id: str) -> str:
    """Return the full detail document for one transform."""
    try:
        detail = await TransformClient(lifecycle.resolve_config(config)).get_transform(transform_id)
    except TransformServerError as exc:
        return Failure(str(exc))
    return json.dumps(detail, indent=2)


async def list_entities(config: TransformatronConfig) -> str:
    """List the entity types the server advertises."""
    try:
        entities = await TransformClient(lifecycle.resolve_config(config)).list_entities()
    except TransformServerError as exc:
        return Failure(str(exc))
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

    A run is reported as a failure when the server is unreachable or the run ends in a
    state other than a success state. A successful run that returned zero entities is not
    a failure: finding nothing is a legitimate answer for many inputs, so the count in the
    output is the signal to read, not the exit code.
    """
    try:
        result = await TransformClient(lifecycle.resolve_config(config)).run_transform(
            transform_id, entity_type, entity_value, settings, timeout
        )
    except TransformServerError as exc:
        return Failure(str(exc))

    lines = [f"State: {result.state} ({'success' if result.succeeded else 'failure'})"]
    if result.messages:
        lines.append("Messages:")
        lines += [f"  - {m}" for m in result.messages]
    lines.append(f"Entities ({len(result.entities)}):")
    lines += [f"  {json.dumps(e)}" for e in result.entities]
    if result.links:
        lines.append(f"Links ({len(result.links)}):")
        lines += [f"  {json.dumps(link)}" for link in result.links]
    rendered = "\n".join(lines)
    return rendered if result.succeeded else Failure(rendered)


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
        return Failure(f"Failed to generate certificates: {exc}")
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


_SPEC_FETCH_TIMEOUT = 30.0


def _load_openapi(openapi: str) -> tuple[str, str | None]:
    """Return the spec text and, when it was fetched, the URL it came from.

    Accepts a URL, a file path, or the document itself.
    """
    if re.match(r"^https?://", openapi.strip()):
        url = openapi.strip()
        try:
            response = httpx.get(url, follow_redirects=True, timeout=_SPEC_FETCH_TIMEOUT)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Raised as ValueError so the caller reports it like any other bad spec.
            raise ValueError(f"could not fetch the OpenAPI spec from {url}: {exc}") from exc
        return response.text, str(response.url)
    path = Path(openapi)
    return (path.read_text() if path.is_file() else openapi), None


def scaffold(
    config: TransformatronConfig,
    service: str | None = None,
    curl: str | None = None,
    openapi: str | None = None,
    sample_response: dict[str, Any] | str | None = None,
    operations: list[str] | None = None,
    force: bool = False,
) -> str:
    """Scaffold a new Maltego transform module package from a cURL command or OpenAPI spec."""
    if not curl and not openapi:
        return Failure("Failed to scaffold: Provide either a --curl command or an --openapi spec.")
    if curl and operations:
        return Failure(
            "Failed to scaffold: --operation selects from an OpenAPI spec, not a cURL command."
        )

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
            spec_text, spec_url = _load_openapi(openapi or "")
            scaffold_cfg = parse_openapi_spec(
                spec=spec_text,
                service_name=service,
                operations=operations,
                spec_url=spec_url,
            )

        created_files = write_scaffold(scaffold_cfg, config.project_dir, force=force)
    except ValueError as exc:
        # json.JSONDecodeError subclasses ValueError, so a malformed spec lands here too.
        return Failure(f"Failed to scaffold: {exc}")
    except FileExistsError as exc:
        return Failure(f"Failed to scaffold: {exc}")
    except OSError as exc:
        return Failure(f"Failed to scaffold: could not write to {config.project_dir}: {exc}")

    file_lines = "\n".join(f"  - {f}" for f in created_files)
    transforms_list = "\n".join(
        f"  - {t.display_name} ({t.input_entity} -> {', '.join(t.output_entity_types)}, "
        f"{t.http_method} {t.endpoint_path})"
        for t in scaffold_cfg.transforms
    )
    if scaffold_cfg.auth_key_name:
        auth_line = (
            f"Requires an API key: set {scaffold_cfg.auth_key_name} in .env, or pass "
            f"--setting {scaffold_cfg.auth_key_name}=... to run."
        )
    else:
        auth_line = "No API key: the request showed no authentication."
    if (config.project_dir / "project.py").exists():
        import_line = "Imports added to server/project.py."
    else:
        import_line = (
            "No server/project.py found, so nothing was imported; add the imports by hand."
        )
    notes = "".join(f"  - {note}\n" for note in scaffold_cfg.notes)
    notes_block = f"Notes:\n{notes}\n" if notes else ""

    return (
        f"Scaffolded '{scaffold_cfg.display_name}' ({scaffold_cfg.service_id}):\n\n"
        f"Created files:\n{file_lines}\n\n"
        f"Transforms:\n{transforms_list}\n\n"
        f"{auth_line}\n{import_line}\n\n"
        f"{notes_block}"
        f"Next steps:\n"
        f"1. Run 'transformatron_cli.py restart' to load the new transform.\n"
        f"2. Run it against a real input and check the entity count.\n"
        f"3. Every response field is mapped; delete the ones an investigator does not need.\n"
        f"4. Run 'smoke_test_transforms.py' to gate the whole set."
    )
