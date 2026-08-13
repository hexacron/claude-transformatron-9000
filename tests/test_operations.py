"""Tests for the operations shared by the MCP and CLI front ends."""

from __future__ import annotations

from typing import Any

import pytest

from transformatron import operations
from transformatron.client import RunResult
from transformatron.config import TransformatronConfig


@pytest.fixture
def config(tmp_path) -> TransformatronConfig:
    project = tmp_path / "server"
    project.mkdir()
    (project / "project.py").write_text("")
    return TransformatronConfig(project_dir=project, state_dir=tmp_path / "state")


def test_format_transform_renders_input_and_output_types() -> None:
    rendered = operations.format_transform(
        {
            "name": "acme.demo.lookup",
            "displayName": "Lookup",
            "input": {"typeIds": ["maltego.Domain"]},
            "output": {"typeIds": ["maltego.IPv4Address", "maltego.AS"]},
        }
    )

    assert "acme.demo.lookup" in rendered
    assert "maltego.Domain -> maltego.IPv4Address, maltego.AS" in rendered


def test_format_transform_marks_a_missing_output_type_as_none() -> None:
    """An untyped return annotation must be visible, since it breaks client routing."""
    rendered = operations.format_transform(
        {"name": "acme.demo.broken", "input": {"typeIds": ["maltego.Domain"]}, "output": {}}
    )

    assert "-> NONE" in rendered


def _run_result(entities: list[dict[str, Any]], state: str = "COMPLETED") -> RunResult:
    return RunResult(run_id="r1", state=state, entities=entities)


async def test_run_transform_always_reports_the_entity_count(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count is the only signal that distinguishes a working transform from a silent failure."""

    async def fake_run(self, *args: object, **kwargs: object) -> RunResult:
        return _run_result([])

    monkeypatch.setattr(operations.TransformClient, "run_transform", fake_run)

    rendered = await operations.run_transform(
        config, "acme.demo.lookup", "maltego.Domain", "example.com"
    )

    assert "State: COMPLETED (success)" in rendered
    assert "Entities (0):" in rendered


async def test_run_transform_renders_returned_entities(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(self, *args: object, **kwargs: object) -> RunResult:
        return _run_result([{"type": "maltego.IPv4Address"}])

    monkeypatch.setattr(operations.TransformClient, "run_transform", fake_run)

    rendered = await operations.run_transform(
        config, "acme.demo.lookup", "maltego.Domain", "example.com"
    )

    assert "Entities (1):" in rendered
    assert "maltego.IPv4Address" in rendered


async def test_list_transforms_reports_an_empty_server(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_list(self) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(operations.TransformClient, "list_transforms", fake_list)

    assert "no transforms" in await operations.list_transforms(config)
