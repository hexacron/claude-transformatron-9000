"""Tests for the v3 protocol client, especially the run/poll state machine."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from transformatron.client import (
    RunResult,
    TransformClient,
    TransformServerError,
    build_run_request,
    collect_events,
)
from transformatron.config import TransformatronConfig


def entity_event(value: str, event_type: str = "ADD") -> dict[str, Any]:
    return {
        "data": {
            "inputType": "ENTITY",
            "eventType": event_type,
            "entity": {"type": "maltego.Phrase", "value": value},
        }
    }


def status_event(text: str) -> dict[str, Any]:
    return {
        "data": {
            "inputType": "STATUS_MESSAGE",
            "eventType": "ADD",
            "statusMessage": {"text": text},
        }
    }


class FakeServer:
    """Scripts a sequence of poll responses for one transform run."""

    def __init__(self, poll_responses: list[dict[str, Any]], run_id: str = "run-1") -> None:
        self._poll_responses = poll_responses
        self._run_id = run_id
        self.polls = 0
        self.cancelled = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/cancel"):
            self.cancelled = True
            return httpx.Response(200, json={})
        if request.method == "POST":
            return httpx.Response(201, json={"result": {"runId": self._run_id, "state": "RUNNING"}})
        index = min(self.polls, len(self._poll_responses) - 1)
        self.polls += 1
        return httpx.Response(201, json=self._poll_responses[index])


@pytest.fixture
def config(tmp_path) -> TransformatronConfig:
    return TransformatronConfig(project_dir=tmp_path, state_dir=tmp_path / "state")


def patch_handler(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Route the client's httpx calls to ``handler``.

    Binds the original ``AsyncClient`` before patching; referring to
    ``httpx.AsyncClient`` inside the replacement would recurse.
    """
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def build(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", build)


def patch_transport(monkeypatch: pytest.MonkeyPatch, fake: FakeServer) -> None:
    """Route the client's httpx calls to the fake server."""
    patch_handler(monkeypatch, fake.handler)


def test_build_run_request_carries_entity_and_settings() -> None:
    body = build_run_request("maltego.Domain", "example.com", {"api_key": "secret"})
    entity = body["input"]["graph"]["entities"][0]
    assert entity["type"] == "maltego.Domain"
    assert entity["properties"][0]["value"] == "example.com"
    # valueRef must name a property that actually exists on the entity.
    assert entity["valueRef"] == entity["properties"][0]["name"]
    assert body["transformSettings"] == [{"name": "api_key", "value": "secret"}]
    assert body["input"]["metadata"]["entitiesTypesStat"] == {"maltego.Domain": 1}


def test_collect_events_sorts_by_type_and_skips_deletes() -> None:
    result = RunResult(run_id="r", state="RUNNING")
    collect_events(
        [
            entity_event("keep"),
            entity_event("dropped", event_type="DELETE"),
            {"data": {"inputType": "LINK", "eventType": "ADD", "link": {"id": "l1"}}},
            status_event("working"),
        ],
        result,
    )
    assert [e["value"] for e in result.entities] == ["keep"]
    assert len(result.links) == 1
    assert result.messages == ["working"]


@pytest.mark.parametrize("terminal_state", ["COMPLETED", "FINISHED"])
async def test_both_success_states_terminate_the_poll_loop(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch, terminal_state: str
) -> None:
    """The SDK reports success as either COMPLETED or FINISHED; both must end polling."""
    fake = FakeServer(
        [
            {"result": {"state": "RUNNING", "events": []}},
            {"result": {"state": terminal_state, "events": [entity_event("found")]}},
        ]
    )
    patch_transport(monkeypatch, fake)

    result = await TransformClient(config).run_transform("t", "maltego.Phrase", "x", timeout=5)

    assert result.state == terminal_state
    assert result.succeeded
    assert [e["value"] for e in result.entities] == ["found"]


async def test_failed_run_surfaces_its_status_message(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeServer(
        [{"result": {"state": "FAILED", "events": [status_event("Invalid input: boom")]}}]
    )
    patch_transport(monkeypatch, fake)

    result = await TransformClient(config).run_transform("t", "maltego.Phrase", "x", timeout=5)

    assert result.state == "FAILED"
    assert not result.succeeded
    assert "Invalid input: boom" in result.messages


async def test_events_are_not_double_counted_across_polls(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server replays the whole event list each poll, so only new events count."""
    first = entity_event("one")
    second = entity_event("two")
    fake = FakeServer(
        [
            {"result": {"state": "RUNNING", "events": [first]}},
            {"result": {"state": "COMPLETED", "events": [first, second]}},
        ]
    )
    patch_transport(monkeypatch, fake)

    result = await TransformClient(config).run_transform("t", "maltego.Phrase", "x", timeout=5)

    assert [e["value"] for e in result.entities] == ["one", "two"]


async def test_timeout_cancels_the_run_server_side(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeServer([{"result": {"state": "RUNNING", "events": []}}])
    patch_transport(monkeypatch, fake)

    result = await TransformClient(config).run_transform("t", "maltego.Phrase", "x", timeout=0.01)

    assert result.state == "TIMED_OUT"
    assert not result.succeeded
    assert fake.cancelled, "a run abandoned by the client must be cancelled server-side"


async def test_missing_run_id_is_an_error(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"result": {}})

    patch_handler(monkeypatch, handler)

    with pytest.raises(TransformServerError, match="no runId"):
        await TransformClient(config).run_transform("t", "maltego.Phrase", "x")


async def test_unreachable_server_explains_how_to_start_it(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    patch_handler(monkeypatch, handler)

    with pytest.raises(TransformServerError, match="server_start"):
        await TransformClient(config).list_transforms()


async def test_http_error_status_is_reported(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="no such transform")

    patch_handler(monkeypatch, handler)

    with pytest.raises(TransformServerError, match="404"):
        await TransformClient(config).get_transform("missing")
