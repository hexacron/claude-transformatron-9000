"""Scaffolded transforms, imported and run against a recorded HTTP exchange.

Parsing and linting prove a scaffold is well-formed. These prove it does what the request
showed: the right method, path, query and body go out, and the response comes back as
entities.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from maltego import entities

from transformatron.scaffold import (
    ScaffoldServiceConfig,
    parse_curl_command,
    parse_openapi_spec,
    write_scaffold,
)


@dataclass
class _Response:
    payload: Any

    def json(self) -> Any:
        return self.payload


@dataclass
class _RecordingClient:
    """Stands in for the generated module's IntegrationClient."""

    payload: Any
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def request(self, method: str, url: str, context: Any, **kwargs: Any) -> _Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        return _Response(self.payload)


@dataclass
class _Log:
    messages: list[str] = field(default_factory=list)

    def fatal(self, message: str) -> None:
        self.messages.append(f"FATAL {message}")

    def inform(self, message: str) -> None:
        self.messages.append(message)


@dataclass
class _Loaded:
    transforms: dict[str, Callable[..., Any]]
    client: _RecordingClient

    async def run(
        self, transform_id: str, entity: Any, settings: dict[str, str] | None = None
    ) -> tuple[list[Any], list[str]]:
        log = _Log()
        results = await self.transforms[transform_id](
            entity, settings or {}, SimpleNamespace(log=log)
        )
        return results, log.messages


def _load(
    cfg: ScaffoldServiceConfig, payload: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> _Loaded:
    """Write the scaffold to a scratch project and import it as the server would."""
    project = tmp_path / "server"
    project.mkdir()
    written = write_scaffold(cfg, project)

    # Generated code imports `transforms.<service>`; make that resolve to the scratch
    # project rather than anything a previous test left behind.
    for name in [m for m in sys.modules if m == "transforms" or m.startswith("transforms.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(project))
    importlib.invalidate_caches()

    api = importlib.import_module(f"transforms.{cfg.service_id}.api")
    client = _RecordingClient(payload)
    monkeypatch.setattr(api, "client", client)

    transforms: dict[str, Callable[..., Any]] = {}
    for path in written:
        if path.name in ("__init__.py", "api.py"):
            continue
        module = importlib.import_module(f"transforms.{cfg.service_id}.{path.stem}")
        for t in cfg.transforms:
            if hasattr(module, t.transform_id):
                transforms[t.transform_id] = getattr(module, t.transform_id)
    return _Loaded(transforms, client)


def _values(results: list[Any], entity_type: type) -> list[str]:
    return [r.value for r in results if isinstance(r, entity_type)]


async def test_keyless_get_fills_the_input_path_segment_and_keeps_other_query_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The input sits in the middle of the path and a fixed query param rides along."""
    cfg = parse_curl_command(
        "curl 'https://ipinfo.io/8.8.8.8/json?fields=org'",
        sample_response={"ip": "8.8.8.8", "hostname": "dns.google", "org": "AS15169 Google"},
    )
    loaded = _load(cfg, {"ip": "1.1.1.1", "hostname": "one.one.one.one"}, tmp_path, monkeypatch)

    results, messages = await loaded.run("ipinfo_lookup", entities.IPv4Address(value="1.1.1.1"))

    [call] = loaded.client.calls
    assert (call["method"], call["url"]) == ("GET", "https://ipinfo.io/1.1.1.1/json")
    assert call["params"] == {"fields": "org"}
    assert "headers" not in call
    assert _values(results, entities.Domain) == ["one.one.one.one"]
    assert not any(m.startswith("FATAL") for m in messages)


async def test_records_nested_in_a_list_become_entities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DNS answers arrive as a list of objects; their addresses must reach the graph."""
    cfg = parse_curl_command(
        "curl 'https://dns.google/resolve?name=python.org&type=A'",
        sample_response={
            "Status": 0,
            "TC": False,
            "Answer": [{"name": "python.org.", "type": 1, "data": "151.101.0.223"}],
        },
    )
    payload = {
        "Status": 0,
        "TC": False,
        "Answer": [
            {"name": "example.com.", "type": 1, "data": "192.0.2.1"},
            {"name": "example.com.", "type": 1, "data": "192.0.2.2"},
        ],
    }
    loaded = _load(cfg, payload, tmp_path, monkeypatch)

    results, _ = await loaded.run("dns_lookup", entities.Domain(value="example.com"))

    [call] = loaded.client.calls
    assert call["params"] == {"name": "example.com", "type": "A"}
    assert _values(results, entities.IPv4Address) == ["192.0.2.1", "192.0.2.2"]
    assert set(_values(results, entities.Domain)) == {"example.com"}
    assert "Tc: False" in _values(results, entities.Phrase)


async def test_post_sends_the_body_with_the_input_and_reads_a_list_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = parse_curl_command(
        "curl -X POST https://api.search.example/v1/search "
        '-H \'Authorization: Bearer abc\' -d \'{"query": "8.8.8.8", "limit": 5}\'',
        sample_response=[{"ip": "8.8.8.8", "country": "US"}],
        service_name="searchy",
    )
    payload = [{"ip": "1.1.1.1", "country": "AU"}, {"ip": "1.0.0.1", "country": "AU"}]
    loaded = _load(cfg, payload, tmp_path, monkeypatch)

    results, _ = await loaded.run(
        "searchy_lookup", entities.IPv4Address(value="1.1.1.1"), {"SEARCHY_API_KEY": "k1"}
    )

    [call] = loaded.client.calls
    assert call["method"] == "POST"
    assert call["url"] == "https://api.search.example/v1/search"
    assert call["json"] == {"query": "1.1.1.1", "limit": 5}
    assert call["headers"] == {"Authorization": "Bearer k1"}
    assert _values(results, entities.IPv4Address) == ["1.1.1.1", "1.0.0.1"]


async def test_form_body_is_sent_as_form_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = parse_curl_command(
        "curl https://api.formy.example/lookup -d 'domain=example.com&mode=full'",
        sample_response={"registrar": "Example Registrar"},
    )
    loaded = _load(cfg, {"registrar": "R"}, tmp_path, monkeypatch)

    await loaded.run("formy_lookup", entities.Domain(value="python.org"))

    [call] = loaded.client.calls
    assert call["method"] == "POST"
    assert call["data"] == {"domain": "python.org", "mode": "full"}
    assert "json" not in call


async def test_missing_key_stops_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = parse_curl_command(
        "curl -H 'X-Api-Key: abc' https://api.keyed.example/ip/8.8.8.8",
        sample_response={"ip": "8.8.8.8"},
    )
    loaded = _load(cfg, {"ip": "1.1.1.1"}, tmp_path, monkeypatch)
    monkeypatch.delenv("KEYED_API_KEY", raising=False)

    results, messages = await loaded.run("keyed_lookup", entities.IPv4Address(value="1.1.1.1"))

    assert results == []
    assert loaded.client.calls == []
    assert any("KEYED_API_KEY" in m for m in messages)


async def test_field_names_that_are_not_identifiers_still_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hyphens, quotes, braces and keywords in response keys are data, not code."""
    sample = {
        "as-name": "Google",
        'say "hi"': "x",
        "{brace}": "y",
        "class": "z",
        "geo": {"country_code": "US"},
    }
    cfg = parse_curl_command("curl https://api.odd.example/ip/8.8.8.8", sample_response=sample)
    loaded = _load(cfg, sample, tmp_path, monkeypatch)

    results, _ = await loaded.run("odd_lookup", entities.IPv4Address(value="8.8.8.8"))

    phrases = _values(results, entities.Phrase)
    assert "As-Name: Google" in phrases
    assert 'Say "Hi": x' in phrases
    assert "{Brace}: y" in phrases
    assert "Class: z" in phrases
    assert _values(results, entities.Country) == ["US"]


async def test_openapi_ref_schemas_path_params_and_query_auth_work_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Refs", "version": "1"},
        "servers": [{"url": "/v2"}],
        "components": {
            "securitySchemes": {"key": {"type": "apiKey", "in": "query", "name": "token"}},
            "parameters": {"Ip": {"name": "ip", "in": "path", "required": True}},
            "schemas": {
                "Host": {
                    "type": "object",
                    "properties": {
                        "hostnames": {"type": "array", "items": {"type": "string"}},
                        "asn": {"type": "string"},
                    },
                }
            },
        },
        "paths": {
            "/host/{ip}": {
                "parameters": [{"$ref": "#/components/parameters/Ip"}],
                "get": {
                    "operationId": "getHost",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Host"}
                                }
                            }
                        }
                    },
                },
            }
        },
    }
    cfg = parse_openapi_spec(spec, spec_url="https://refs.example/openapi.json")
    payload = {"hostnames": ["a.example", "b.example"], "asn": "AS64500"}
    loaded = _load(cfg, payload, tmp_path, monkeypatch)

    results, _ = await loaded.run(
        "get_host", entities.IPv4Address(value="192.0.2.9"), {"REFS_API_KEY": "t0k"}
    )

    [call] = loaded.client.calls
    assert call["url"] == "https://refs.example/v2/host/192.0.2.9"
    assert call["params"] == {"token": "t0k"}
    assert _values(results, entities.AS) == ["64500"]
    assert len(results) == 3
