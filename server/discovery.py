"""Discovery of transforms under ``transforms/local/``.

``transforms/local/`` is gitignored: it is where integrations that should not be published
live. It is discovered rather than imported by name because a fresh clone does not have it,
and a missing static import would stop the server booting.

This lives apart from ``project.py`` so it can be imported without the SDK or the sample
transforms: ``project.py`` registers every sample at import time, which a test of the
discovery rules has no business doing.
"""

import importlib
import pkgutil
from pathlib import Path


def discover_local_transforms(local_dir: Path, package: str) -> list[str]:
    """Import every transform module under ``local_dir``, if that directory exists.

    Two layouts are loaded, because an agent authoring a one-off transform should not have
    to learn the package convention first:

    - a package (a directory with ``__init__.py``): every module in it except ``api``,
      which holds shared client helpers, registers no transforms and is imported by its
      siblings anyway;
    - a single-file module directly in ``local_dir``, e.g. ``local/foo.py``.

    Names starting with ``_`` are skipped at both levels, so private helpers and
    ``__init__`` are never imported as transforms.

    Args:
        local_dir: The directory to scan; ``transforms/local`` in the server.
        package: The dotted package name ``local_dir`` is importable as.

    Returns:
        The dotted names of the modules imported, in import order, for the startup log.
    """
    if not local_dir.is_dir():
        return []

    imported: list[str] = []
    # iter_modules yields .py files and directories with __init__.py, sorted by name.
    for entry in pkgutil.iter_modules([str(local_dir)]):
        if entry.name.startswith("_"):
            continue
        if not entry.ispkg:
            imported.append(_import(f"{package}.{entry.name}"))
            continue
        for module in pkgutil.iter_modules([str(local_dir / entry.name)]):
            if module.name == "api" or module.name.startswith("_"):
                continue
            imported.append(_import(f"{package}.{entry.name}.{module.name}"))
    return imported


def _import(dotted: str) -> str:
    """Import ``dotted`` for its registration side effects and return the name."""
    importlib.import_module(dotted)
    return dotted
