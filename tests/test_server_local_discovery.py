"""Discovery of the gitignored ``transforms/local/`` directory.

An agent authoring a private transform drops it in ``transforms/local/`` and restarts; if
discovery skips the file, the transform silently never registers and the author chases a
mapping bug that is really a loading one.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"

# A name no real module uses, so imports made by one test cannot satisfy another's.
PACKAGE = "tfm_discovery_case"


def _load_discover() -> Callable[[Path, str], list[str]]:
    """Import server/discovery.py by path: it is not part of the installed package."""
    spec = importlib.util.spec_from_file_location(
        "tfm_server_discovery", SERVER_DIR / "discovery.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.discover_local_transforms


@pytest.fixture
def local_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An importable, empty stand-in for ``transforms/local/``."""
    local = tmp_path / PACKAGE
    local.mkdir()
    (local / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    yield local
    for name in [m for m in sys.modules if m == PACKAGE or m.startswith(f"{PACKAGE}.")]:
        del sys.modules[name]


def _write(path: Path, source: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)


def test_loads_packages_and_single_file_modules(local_dir: Path) -> None:
    _write(local_dir / "flat.py", "LOADED = True\n")
    _write(local_dir / "svc" / "__init__.py")
    _write(local_dir / "svc" / "lookup.py", "LOADED = True\n")

    imported = _load_discover()(local_dir, PACKAGE)

    assert imported == [f"{PACKAGE}.flat", f"{PACKAGE}.svc.lookup"]
    assert sys.modules[f"{PACKAGE}.flat"].LOADED
    assert sys.modules[f"{PACKAGE}.svc.lookup"].LOADED


def test_skips_private_names_and_package_api(local_dir: Path) -> None:
    # Any of these being imported would raise, so a skip failure cannot pass silently.
    boom = "raise RuntimeError('must not be imported')\n"
    _write(local_dir / "_helpers.py", boom)
    _write(local_dir / "_private" / "__init__.py", boom)
    _write(local_dir / "svc" / "__init__.py")
    _write(local_dir / "svc" / "api.py", boom)
    _write(local_dir / "svc" / "_shared.py", boom)
    _write(local_dir / "svc" / "lookup.py")
    # A directory without __init__.py is not a package the server can import by name.
    _write(local_dir / "notes" / "scratch.py", boom)

    imported = _load_discover()(local_dir, PACKAGE)

    assert imported == [f"{PACKAGE}.svc.lookup"]


def test_missing_directory_loads_nothing(tmp_path: Path) -> None:
    assert _load_discover()(tmp_path / "absent", PACKAGE) == []
