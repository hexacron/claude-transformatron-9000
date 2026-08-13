"""Async client for the Maltego v3 transform protocol.

Route and payload shapes are taken from the SDK's own protocol definitions
(``maltego/server/v3/__init__.py``) and the reference documented in the shipped
``maltego-transform-test`` skill.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import httpx

from transformatron.config import TransformatronConfig

# From maltego/protocol/v3/execution/transform_run.py. The SDK defines both
# COMPLETED and FINISHED as distinct successful end states; treating only one as
# terminal would poll forever against a server that reports the other.
SUCCESS_STATES = frozenset({"COMPLETED", "FINISHED"})
FAILURE_STATES = frozenset({"FAILED", "CANCELED", "TIMED_OUT"})
TERMINAL_STATES = SUCCESS_STATES | FAILURE_STATES

DEFAULT_RUN_TIMEOUT = 60.0
POLL_INTERVAL = 0.5


class TransformServerError(RuntimeError):
    """Raised when the transform server is unreachable or returns an error."""


@dataclass
class RunResult:
    """Outcome of a single transform run.

    Attributes:
        run_id: Server-assigned identifier for the run.
        state: Terminal run state reported by the server.
        entities: Entities produced by the transform.
        links: Links produced by the transform.
        messages: Status messages emitted during the run.
    """

    run_id: str
    state: str
    entities: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.state in SUCCESS_STATES


def build_run_request(
    entity_type: str,
    entity_value: str,
    settings: dict[str, str] | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    """Build the POST body for a transform run from a single input entity.

    The server derives the input entity from ``valueRef``, which must name one of
    the entity's properties.
    """
    value_ref = "value"
    return {
        "input": {
            "metadata": {
                "entitiesTypesStat": {entity_type: 1},
                "entitiesTotalCount": 1,
                "linksTotalCount": 0,
                "rootEntitiesCount": 1,
            },
            "graph": {
                "entities": [
                    {
                        "id": "0",
                        "valueRef": value_ref,
                        "type": entity_type,
                        "properties": [
                            {"name": value_ref, "type": "STRING", "value": entity_value}
                        ],
                        "displayInformation": [],
                    }
                ],
                "links": [],
            },
        },
        "transformSettings": [
            {"name": name, "value": value} for name, value in (settings or {}).items()
        ],
        "limit": limit,
    }


def collect_events(events: list[dict[str, Any]], result: RunResult) -> None:
    """Sort protocol events into entities, links, and status messages.

    Events arrive as ``{"timestamp": ..., "data": {"inputType": ..., ...}}``.
    Only ADD/UPDATE events contribute output; DELETE events retract earlier ones.
    """
    for event in events:
        data = event.get("data", {})
        input_type = data.get("inputType")
        if data.get("eventType") == "DELETE":
            continue
        if input_type == "ENTITY" and "entity" in data:
            result.entities.append(data["entity"])
        elif input_type == "LINK" and "link" in data:
            result.links.append(data["link"])
        elif input_type == "STATUS_MESSAGE":
            text = data.get("statusMessage", {}).get("text") or data.get("text")
            if text:
                result.messages.append(str(text))


class TransformClient:
    """Talks to a running transform server over the v3 protocol."""

    def __init__(self, config: TransformatronConfig, timeout: float = 15.0) -> None:
        self._config = config
        self._timeout = timeout

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._config.api_url}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
                response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise TransformServerError(
                f"Could not reach the transform server at {url}: {exc}. "
                "Start it with the server_start tool."
            ) from exc
        if response.status_code >= 400:
            raise TransformServerError(
                f"{method} {url} failed with HTTP {response.status_code}: {response.text[:400]}"
            )
        if not response.content:
            return None
        return response.json()

    async def status(self) -> Any:
        """Return the server status document."""
        return await self._request("GET", "status")

    async def list_transforms(self) -> list[dict[str, Any]]:
        """Return every transform the server advertises."""
        payload = await self._request("GET", "transforms")
        return _unwrap_list(payload, "transforms")

    async def get_transform(self, transform_id: str) -> Any:
        """Return the detail document for a single transform."""
        return await self._request("GET", f"transforms/{transform_id}")

    async def list_entities(self) -> list[dict[str, Any]]:
        """Return every entity schema the server advertises."""
        payload = await self._request("GET", "assets/entities")
        return _unwrap_list(payload, "entities")

    async def cancel_run(self, transform_id: str, run_id: str) -> None:
        """Ask the server to cancel an in-flight run."""
        await self._request("POST", f"transforms/{transform_id}/run/{run_id}/cancel")

    async def run_transform(
        self,
        transform_id: str,
        entity_type: str,
        entity_value: str,
        settings: dict[str, str] | None = None,
        timeout: float = DEFAULT_RUN_TIMEOUT,
    ) -> RunResult:
        """Run a transform and poll until it reaches a terminal state.

        Args:
            transform_id: Identifier from ``list_transforms``.
            entity_type: Maltego entity type of the input, e.g. ``maltego.Domain``.
            entity_value: Value carried by the input entity.
            settings: Optional transform settings, by setting name.
            timeout: Seconds to wait before cancelling the run.

        Returns:
            The collected entities, links, and messages for the run.

        Raises:
            TransformServerError: If the server is unreachable or returns no run id.
        """
        body = build_run_request(entity_type, entity_value, settings)
        started = await self._request("POST", f"transforms/{transform_id}/run", json=body)
        run_id = (started or {}).get("result", {}).get("runId")
        if not run_id:
            raise TransformServerError(
                f"Server accepted the run for '{transform_id}' but returned no runId: {started!r}"
            )
        return await self._poll_run(transform_id, run_id, timeout)

    async def _poll_run(self, transform_id: str, run_id: str, timeout: float) -> RunResult:
        """Poll a run's results endpoint until terminal, cancelling on timeout."""
        result = RunResult(run_id=run_id, state="RUNNING")
        seen = 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while True:
            payload = await self._request("GET", f"transforms/{transform_id}/run/{run_id}/results")
            run = (payload or {}).get("result", {})
            result.state = run.get("state", result.state)

            events = run.get("events", []) or []
            # The server replays the full event list on every poll; only take the
            # events that arrived since the previous one.
            collect_events(events[seen:], result)
            seen = len(events)

            if result.state in TERMINAL_STATES:
                return result

            if loop.time() >= deadline:
                # Cancel server-side so the run is not left orphaned. A failure
                # here is not worth surfacing: the run is already being abandoned.
                with suppress(TransformServerError):
                    await self.cancel_run(transform_id, run_id)
                result.state = "TIMED_OUT"
                result.messages.append(
                    f"Run exceeded the {timeout:g}s client timeout and was cancelled."
                )
                return result

            await asyncio.sleep(POLL_INTERVAL)


def _unwrap_list(payload: Any, key: str) -> list[dict[str, Any]]:
    """Return a list from either a bare array or a keyed/``result``-wrapped object."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for candidate in (payload.get(key), payload.get("result")):
            if isinstance(candidate, list):
                return candidate
            if isinstance(candidate, dict) and isinstance(candidate.get(key), list):
                return candidate[key]
    return []
