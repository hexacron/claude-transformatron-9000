"""Tests for server process management."""

from __future__ import annotations

import dataclasses
import os

import httpx
import pytest

from transformatron import lifecycle
from transformatron.config import TransformatronConfig


@pytest.fixture
def config(tmp_path) -> TransformatronConfig:
    project = tmp_path / "server"
    project.mkdir()
    (project / "project.py").write_text("")
    return TransformatronConfig(project_dir=project, state_dir=tmp_path / "state")


def test_read_pid_clears_a_stale_pid_file(config: TransformatronConfig) -> None:
    """A crashed server must not block the next start."""
    config.state_dir.mkdir(parents=True)
    # PID 2**31-1 is above the kernel maximum, so it cannot name a live process.
    config.pid_file.write_text("2147483647")

    assert lifecycle.read_pid(config) is None
    assert not config.pid_file.exists()


def test_read_pid_clears_a_corrupt_pid_file(config: TransformatronConfig) -> None:
    config.state_dir.mkdir(parents=True)
    config.pid_file.write_text("not-a-pid")

    assert lifecycle.read_pid(config) is None
    assert not config.pid_file.exists()


def test_read_pid_returns_a_live_process(config: TransformatronConfig) -> None:
    config.state_dir.mkdir(parents=True)
    config.pid_file.write_text(str(os.getpid()))

    assert lifecycle.read_pid(config) == os.getpid()


def test_exited_child_is_not_reported_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exited child we started is a zombie until reaped, and os.kill(pid, 0)
    still succeeds for it. Liveness must come from the Popen handle instead, or
    every stop falls through to SIGKILL."""
    pid = 424242

    class ExitedChild:
        returncode = 0

        def poll(self) -> int:
            return 0

    monkeypatch.setitem(lifecycle._OWNED, pid, ExitedChild())
    # Simulate the kernel still answering for the unreaped pid.
    monkeypatch.setattr(lifecycle.os, "kill", lambda *a: None)

    assert not lifecycle._pid_is_alive(pid)
    assert pid not in lifecycle._OWNED, "a reaped child must be forgotten"


def test_live_owned_child_is_reported_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    pid = 424243

    class RunningChild:
        def poll(self) -> None:
            return None

    monkeypatch.setitem(lifecycle._OWNED, pid, RunningChild())

    assert lifecycle._pid_is_alive(pid)


def test_start_refuses_when_already_running(config: TransformatronConfig) -> None:
    config.state_dir.mkdir(parents=True)
    config.pid_file.write_text(str(os.getpid()))

    with pytest.raises(lifecycle.ServerLifecycleError, match="already running"):
        lifecycle.start(config)


def test_start_reports_a_missing_entrypoint(tmp_path) -> None:
    config = TransformatronConfig(project_dir=tmp_path / "absent", state_dir=tmp_path / "state")

    with pytest.raises(lifecycle.ServerLifecycleError, match="maltego-transforms start"):
        lifecycle.start(config)


def test_stop_is_safe_when_not_running(config: TransformatronConfig) -> None:
    assert lifecycle.stop(config) == "Server is not running."


def test_build_server_env_pins_host_port_and_scheme(config: TransformatronConfig) -> None:
    env = lifecycle.build_server_env(config)

    assert env["MALTEGO_SERVER_HTTP_ADDR"] == config.host
    assert env["MALTEGO_SERVER_HTTP_PORT"] == str(config.port)
    assert env["MALTEGO_SERVER_PROTOCOL"] == "http"


def test_build_server_env_carries_identity(config: TransformatronConfig) -> None:
    """Identity reaches the server the same way host and port do, via MALTEGO_SERVER_*."""
    named = dataclasses.replace(
        config, server_name="Acme Intel", namespace="acme.intel", author="Acme"
    )

    env = lifecycle.build_server_env(named)

    assert env["MALTEGO_SERVER_SERVER_NAME"] == "Acme Intel"
    assert env["MALTEGO_SERVER_NS"] == "acme.intel"
    assert env["MALTEGO_SERVER_AUTHOR"] == "Acme"


def test_configured_identity_outranks_the_ambient_environment(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale MALTEGO_SERVER_NS in the shell must not override transformatron.toml.

    build_server_env copies os.environ in before setting its own keys, so an inherited
    value would win if identity were merged in the other order — and the server would
    register transform ids under a namespace nobody configured.
    """
    monkeypatch.setenv("MALTEGO_SERVER_NS", "stale.from.shell")
    named = dataclasses.replace(config, namespace="acme.intel")

    assert lifecycle.build_server_env(named)["MALTEGO_SERVER_NS"] == "acme.intel"


def test_read_scheme_defaults_to_http(config: TransformatronConfig) -> None:
    assert lifecycle.read_scheme(config) == "http"


def test_read_scheme_reports_recorded_https(config: TransformatronConfig) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.scheme_file.write_text("https")

    assert lifecycle.read_scheme(config) == "https"
    assert lifecycle.resolve_config(config).base_url.startswith("https://")


def test_read_scheme_ignores_junk(config: TransformatronConfig) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.scheme_file.write_text("gopher")

    assert lifecycle.read_scheme(config) == "http"


def test_probe_health_uses_recorded_scheme(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server started with ssl=True must be probed over https, not http."""
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.scheme_file.write_text("https")
    probed: list[str] = []

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        probed.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(lifecycle.httpx, "get", fake_get)

    assert lifecycle.probe_health(config) is True
    assert probed[0].startswith("https://")


def test_build_server_env_requires_certs_for_ssl(config: TransformatronConfig) -> None:
    with pytest.raises(lifecycle.ServerLifecycleError, match="generate_certs"):
        lifecycle.build_server_env(config, ssl=True)


def test_build_server_env_points_at_certs_when_present(config: TransformatronConfig) -> None:
    config.cert_file.parent.mkdir(parents=True)
    config.cert_file.write_text("cert")
    config.key_file.write_text("key")

    env = lifecycle.build_server_env(config, ssl=True)

    assert env["MALTEGO_SERVER_PROTOCOL"] == "https"
    assert env["MALTEGO_SERVER_CERT_FILE"] == str(config.cert_file)
    assert env["MALTEGO_SERVER_CERT_KEY"] == str(config.key_file)


def _record_restart_scheme(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> list[bool]:
    """Capture the ``ssl`` value ``restart`` hands to ``start``.

    ``stop`` is left real so the test also covers it deleting the scheme file
    before ``start`` would read it.
    """
    started: list[bool] = []

    def fake_start(_config: TransformatronConfig, ssl: bool = False) -> lifecycle.ServerStatus:
        started.append(ssl)
        return lifecycle.ServerStatus(running=True, pid=1, healthy=True, detail="started")

    monkeypatch.setattr(lifecycle, "start", fake_start)
    return started


def test_restart_preserves_a_running_https_server(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reloading an HTTPS server must not drop it to HTTP.

    The Maltego desktop client rejects plain HTTP client-side, so a silent
    downgrade fails with an empty server log — the hardest failure to diagnose.
    """
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.scheme_file.write_text("https")
    started = _record_restart_scheme(config, monkeypatch)

    lifecycle.restart(config)

    assert started == [True]


def test_restart_preserves_a_running_http_server(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.scheme_file.write_text("http")
    started = _record_restart_scheme(config, monkeypatch)

    lifecycle.restart(config)

    assert started == [False]


def test_restart_honours_an_explicit_ssl_request(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.scheme_file.write_text("http")
    started = _record_restart_scheme(config, monkeypatch)

    lifecycle.restart(config, ssl=True)

    assert started == [True]


def test_restart_honours_an_explicit_downgrade(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--no-ssl`` is the deliberate way back to HTTP."""
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.scheme_file.write_text("https")
    started = _record_restart_scheme(config, monkeypatch)

    lifecycle.restart(config, ssl=False)

    assert started == [False]


def test_tail_log_explains_an_absent_log(config: TransformatronConfig) -> None:
    assert "not been started" in lifecycle.tail_log(config)


def test_tail_log_returns_trailing_lines(config: TransformatronConfig) -> None:
    config.state_dir.mkdir(parents=True)
    config.log_file.write_text("".join(f"line{i}\n" for i in range(100)))

    tail = lifecycle.tail_log(config, lines=3)

    assert tail.splitlines() == ["line97", "line98", "line99"]


def test_status_flags_a_foreign_server_it_cannot_stop(
    config: TransformatronConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Something else on the port must not be reported as ours."""
    monkeypatch.setattr(lifecycle, "probe_health", lambda *a, **k: True)

    status = lifecycle.status(config)

    assert not status.running
    assert status.healthy
    assert "not started by this tool" in status.detail
