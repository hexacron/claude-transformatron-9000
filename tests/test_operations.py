"""Tests for the operations shared by the MCP and CLI front ends."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from transformatron import lifecycle, operations
from transformatron.client import RunResult, TransformServerError
from transformatron.config import CONFIG_FILE_NAME, TransformatronConfig

CLI_PATH = Path(__file__).resolve().parents[1] / "scripts" / "transformatron_cli.py"


@pytest.fixture
def config(tmp_path) -> TransformatronConfig:
    project = tmp_path / "server"
    project.mkdir()
    (project / "project.py").write_text("")
    return TransformatronConfig(project_dir=project, state_dir=tmp_path / "state")


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The CLI script, loading its config from an empty ``tmp_path`` repository root."""
    monkeypatch.setattr("transformatron.config.REPO_ROOT", tmp_path)
    spec = importlib.util.spec_from_file_location("transformatron_cli", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cli(monkeypatch: pytest.MonkeyPatch, cli: ModuleType, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["transformatron_cli", *argv])
    return cli.main()


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


def _server_status(healthy: bool, detail: str) -> Any:
    def fake_status(config: TransformatronConfig) -> lifecycle.ServerStatus:
        return lifecycle.ServerStatus(running=healthy, pid=None, healthy=healthy, detail=detail)

    return fake_status


async def test_status_renders_a_non_json_answer_as_a_failure_message(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Something other than the transform server on the port must not crash status."""
    monkeypatch.setattr(lifecycle, "status", _server_status(True, "Server is running."))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>hi</html>"))
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *args, **kwargs: original(*args, **kwargs, transport=transport)
    )

    rendered = await operations.status(config)

    assert isinstance(rendered, operations.Failure)
    assert "not JSON: <html>hi</html>" in rendered


@pytest.mark.parametrize(
    ("state", "entities", "exit_code"),
    [
        ("COMPLETED", [{"id": "e1"}], 0),
        # Finding nothing is a legitimate answer; the output's entity count reports it.
        ("COMPLETED", [], 0),
        ("FINISHED", [], 0),
        ("FAILED", [], 1),
        ("TIMED_OUT", [], 1),
    ],
)
def test_cli_run_exits_nonzero_only_when_the_run_did_not_succeed(
    cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    entities: list[dict[str, Any]],
    exit_code: int,
) -> None:
    async def fake_run(self, *args: object, **kwargs: object) -> RunResult:
        return _run_result(entities, state=state)

    monkeypatch.setattr(operations.TransformClient, "run_transform", fake_run)

    assert run_cli(monkeypatch, cli, "run", "acme.demo", "maltego.Domain", "x.com") == exit_code


def test_cli_exits_nonzero_when_the_server_is_unreachable(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def unreachable(self) -> list[dict[str, Any]]:
        raise TransformServerError("Could not reach the transform server")

    monkeypatch.setattr(operations.TransformClient, "list_transforms", unreachable)

    assert run_cli(monkeypatch, cli, "list") == 1
    assert "Could not reach" in capsys.readouterr().out


def test_cli_status_exits_nonzero_when_the_server_is_not_running(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lifecycle, "status", _server_status(False, "Server is not running."))

    assert run_cli(monkeypatch, cli, "status") == 1


def test_cli_exits_nonzero_when_start_fails(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(config: TransformatronConfig, ssl: bool = False) -> lifecycle.ServerStatus:
        raise lifecycle.ServerLifecycleError("port 3000 is already in use")

    monkeypatch.setattr(lifecycle, "start", refuse)

    assert run_cli(monkeypatch, cli, "start") == 1
    assert capsys.readouterr().out.startswith("Failed to start: port 3000 is already in use")


def test_cli_exits_zero_on_success(
    cli: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli(monkeypatch, cli, "seed-url") == 0
    assert "Seed URL:" in capsys.readouterr().out


def test_cli_reports_a_bad_config_file_in_one_line(
    cli: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A typo in transformatron.toml must read as an error message, not a traceback."""
    (tmp_path / CONFIG_FILE_NAME).write_text("[server\n")

    assert run_cli(monkeypatch, cli, "seed-url") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert "not valid TOML" in captured.err
    assert "Traceback" not in captured.err
