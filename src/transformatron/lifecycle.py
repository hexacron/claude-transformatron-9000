"""Start, stop, and inspect the local transform server process."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass

import httpx

from transformatron.config import TransformatronConfig

STARTUP_TIMEOUT = 45.0
SHUTDOWN_TIMEOUT = 10.0
HEALTH_POLL_INTERVAL = 0.4


class ServerLifecycleError(RuntimeError):
    """Raised when the server cannot be started or stopped."""


@dataclass
class ServerStatus:
    """Observed state of the managed server process.

    Attributes:
        running: Whether the recorded PID belongs to a live process.
        pid: The recorded process id, if any.
        healthy: Whether the server answered a health probe.
        detail: Human-readable explanation of the current state.
    """

    running: bool
    pid: int | None
    healthy: bool
    detail: str


# Popen handles for servers this process started. A child that has exited but
# not been waited on stays a zombie, and os.kill(pid, 0) still succeeds for it,
# so liveness checks must reap through the handle rather than trust the signal.
_OWNED: dict[int, subprocess.Popen] = {}


def _pid_is_alive(pid: int) -> bool:
    """Return whether a process with this pid is running (not an unreaped zombie)."""
    owned = _OWNED.get(pid)
    if owned is not None:
        if owned.poll() is not None:
            del _OWNED[pid]
            return False
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pid(config: TransformatronConfig) -> int | None:
    """Return the recorded pid if it names a live process, else ``None``.

    Clears a stale pid file so a crashed server does not block a restart.
    """
    if not config.pid_file.exists():
        return None
    try:
        pid = int(config.pid_file.read_text().strip())
    except ValueError:
        config.pid_file.unlink(missing_ok=True)
        return None
    if not _pid_is_alive(pid):
        config.pid_file.unlink(missing_ok=True)
        return None
    return pid


def read_scheme(config: TransformatronConfig) -> str:
    """Return the scheme the running server was started with.

    The scheme is runtime state: ``project.py`` defaults to ``https``, but
    :func:`build_server_env` overrides it per start, so a server may be serving
    either scheme. Probing the wrong one makes a healthy server look
    unreachable, so the scheme is recorded at startup.
    """
    if not config.scheme_file.exists():
        return config.scheme
    scheme = config.scheme_file.read_text().strip()
    return scheme if scheme in ("http", "https") else config.scheme


def resolve_config(config: TransformatronConfig) -> TransformatronConfig:
    """Return ``config`` addressing the running server's actual scheme."""
    return config.with_scheme(read_scheme(config))


def probe_health(config: TransformatronConfig, timeout: float = 3.0) -> bool:
    """Return whether the server answers on its status endpoint."""
    resolved = resolve_config(config)
    try:
        response = httpx.get(f"{resolved.api_url}/status", timeout=timeout, verify=False)
    except httpx.HTTPError:
        return False
    return response.status_code < 500


def tail_log(config: TransformatronConfig, lines: int = 50) -> str:
    """Return the last ``lines`` lines of the server log."""
    if not config.log_file.exists():
        return "(no log file yet — the server has not been started)"
    with config.log_file.open("r", errors="replace") as handle:
        return "".join(deque(handle, maxlen=lines)) or "(log is empty)"


def status(config: TransformatronConfig) -> ServerStatus:
    """Report whether the server process is running and answering requests."""
    resolved = resolve_config(config)
    pid = read_pid(config)
    if pid is None:
        healthy = probe_health(config)
        if healthy:
            return ServerStatus(
                running=False,
                pid=None,
                healthy=True,
                detail=(
                    f"Something is already serving {resolved.base_url}, but it was not "
                    "started by this tool, so it cannot be stopped here."
                ),
            )
        return ServerStatus(False, None, False, "Server is not running.")
    healthy = probe_health(config)
    detail = (
        f"Server is running (pid {pid}) and answering at {resolved.base_url}."
        if healthy
        else f"Process {pid} is alive but not answering yet at {resolved.base_url}."
    )
    return ServerStatus(True, pid, healthy, detail)


def build_server_env(config: TransformatronConfig, ssl: bool = False) -> dict[str, str]:
    """Build the environment that pins the server to our host, port, and scheme.

    The generated ``project.py`` hardcodes its ``ServerHTTPSettings`` and parses no
    command-line arguments. Those settings are a pydantic ``BaseSettings`` whose
    ``MALTEGO_SERVER_`` environment variables take precedence over the values
    passed in code, so the environment is the only way to steer the server
    without editing the user's project file.

    Raises:
        ServerLifecycleError: If HTTPS is requested but certificates are missing.
    """
    env = dict(os.environ)
    env["MALTEGO_SERVER_HTTP_ADDR"] = config.host
    env["MALTEGO_SERVER_HTTP_PORT"] = str(config.port)
    env["MALTEGO_SERVER_PROTOCOL"] = "https" if ssl else "http"
    env["PYTHONUNBUFFERED"] = "1"

    if ssl:
        if not (config.cert_file.exists() and config.key_file.exists()):
            raise ServerLifecycleError(
                "HTTPS was requested but no certificates exist. Run generate_certs first."
            )
        env["MALTEGO_SERVER_CERT_FILE"] = str(config.cert_file)
        env["MALTEGO_SERVER_CERT_KEY"] = str(config.key_file)
    return env


def start(config: TransformatronConfig, ssl: bool = False) -> ServerStatus:
    """Launch the transform server and wait until it answers a health probe.

    Args:
        config: Server locations and connection details.
        ssl: Serve over HTTPS using the generated certificate pair.

    Returns:
        The status observed once the server is healthy.

    Raises:
        ServerLifecycleError: If the server is already running, the entrypoint is
            missing, certificates are absent, or startup times out.
    """
    existing = read_pid(config)
    if existing is not None:
        raise ServerLifecycleError(
            f"Server is already running (pid {existing}). Use server_restart to reload it."
        )
    if not config.entrypoint.exists():
        raise ServerLifecycleError(
            f"No server entrypoint at {config.entrypoint}. "
            "Generate one with: maltego-transforms start server --with-skills"
        )

    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.scheme_file.write_text("https" if ssl else "http")
    log_handle = config.log_file.open("ab")
    try:
        process = subprocess.Popen(
            [sys.executable, config.entrypoint.name],
            cwd=config.project_dir,
            env=build_server_env(config, ssl=ssl),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        log_handle.close()

    _OWNED[process.pid] = process
    config.pid_file.write_text(str(process.pid))
    return _await_healthy(config, process)


def _await_healthy(config: TransformatronConfig, process: subprocess.Popen) -> ServerStatus:
    """Wait for the server to answer, failing fast if the process dies."""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            config.pid_file.unlink(missing_ok=True)
            raise ServerLifecycleError(
                f"Server exited immediately with code {process.returncode}. "
                f"Recent log output:\n{tail_log(config, 30)}"
            )
        if probe_health(config):
            resolved = resolve_config(config)
            return ServerStatus(
                running=True,
                pid=process.pid,
                healthy=True,
                detail=f"Server started (pid {process.pid}) at {resolved.base_url}.",
            )
        time.sleep(HEALTH_POLL_INTERVAL)

    raise ServerLifecycleError(
        f"Server did not answer at {resolve_config(config).base_url} within "
        f"{STARTUP_TIMEOUT:g}s. Recent log output:\n{tail_log(config, 30)}"
    )


def stop(config: TransformatronConfig) -> str:
    """Terminate the server, escalating to SIGKILL if it does not exit."""
    pid = read_pid(config)
    if pid is None:
        return "Server is not running."

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + SHUTDOWN_TIMEOUT
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            config.pid_file.unlink(missing_ok=True)
            config.scheme_file.unlink(missing_ok=True)
            _OWNED.pop(pid, None)
            return f"Server stopped (pid {pid})."
        time.sleep(HEALTH_POLL_INTERVAL)

    os.kill(pid, signal.SIGKILL)
    owned = _OWNED.pop(pid, None)
    if owned is not None:
        owned.wait(timeout=SHUTDOWN_TIMEOUT)
    config.pid_file.unlink(missing_ok=True)
    config.scheme_file.unlink(missing_ok=True)
    return f"Server did not exit within {SHUTDOWN_TIMEOUT:g}s and was killed (pid {pid})."


def restart(config: TransformatronConfig, ssl: bool = False) -> ServerStatus:
    """Stop the server if running, then start it again.

    This is the reload path after adding or editing a transform module.
    """
    stop(config)
    return start(config, ssl=ssl)
