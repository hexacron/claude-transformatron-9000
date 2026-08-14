"""Code generator for creating Maltego transform modules and updating server/project.py."""

from __future__ import annotations

import re
from pathlib import Path

from transformatron.scaffold.schema import (
    ScaffoldServiceConfig,
    ScaffoldTransformConfig,
)

_VALIDATORS = {
    "IPv4Address": ("validate_ip", "return str(ipaddress.ip_address(value.strip()))"),
    "IPv6Address": ("validate_ip", "return str(ipaddress.ip_address(value.strip()))"),
    "Domain": (
        "validate_domain",
        "domain = value.strip().lower()\n"
        '    if not re.match(r"^[a-z0-9.-]+\\.[a-z]{2,}$", domain):\n'
        '        raise ValueError("expected a valid domain name")\n'
        "    return domain",
    ),
    "URL": (
        "validate_url",
        "url = value.strip()\n"
        '    if not (url.startswith("http://") or url.startswith("https://")):\n'
        '        raise ValueError("expected an http:// or https:// URL")\n'
        "    return url",
    ),
    "EmailAddress": (
        "validate_email",
        "email = value.strip().lower()\n"
        '    if "@" not in email:\n'
        '        raise ValueError("expected a valid email address")\n'
        "    return email",
    ),
    "Hash": (
        "validate_hash",
        "h = value.strip()\n"
        '    if not re.match(r"^[a-fA-F0-9]{32,128}$", h):\n'
        '        raise ValueError("expected a hex hash string")\n'
        "    return h",
    ),
    "CVE": (
        "validate_cve",
        "cve = value.strip().upper()\n"
        '    if not re.match(r"^CVE-\\d{4}-\\d+$", cve):\n'
        '        raise ValueError("expected format CVE-YYYY-NNNN")\n'
        "    return cve",
    ),
    "Phrase": ("validate_phrase", "return value.strip()"),
}


def _get_validator(entity_type: str) -> tuple[str, str]:
    return _VALIDATORS.get(entity_type, ("validate_phrase", "return value.strip()"))


def _isort_key(name: str) -> tuple[int, str]:
    """Sort entity names the way ruff's isort rule orders a `from` import.

    Ruff puts all-caps names (``AS``, ``CVE``, ``URL``) ahead of CamelCase ones rather
    than sorting the list plainly, so generated imports have to match or every scaffold
    lands an I001 violation on code the project lints.
    """
    return (0 if name.isupper() else 1, name)


def generate_api_module(config: ScaffoldServiceConfig) -> str:
    """Generate the api.py client helper for a service."""
    used_validators: dict[str, str] = {}
    for t in config.transforms:
        val_func, val_impl = _get_validator(t.input_entity)
        used_validators[val_func] = val_impl

    val_code_blocks = []
    for val_name, val_body in used_validators.items():
        val_code_blocks.append(f"""def {val_name}(value: str) -> str:
    \"\"\"Validate and sanitize input value or raise ValueError.\"\"\"
    {val_body}
""")

    # Two blank lines between top-level defs, or `ruff format --check` reformats the
    # generated file and the project's format gate fails on brand-new code. A service
    # whose transforms take different input types emits one validator per type, so the
    # separator has to hold between them and not just before `fetch`.
    validators_str = "\n\n\n".join(block.rstrip("\n") for block in val_code_blocks)

    # Only the regex-based validators use `re`, and only IP inputs use `ipaddress`.
    # Emitting both unconditionally leaves an unused import, which fails the project's
    # ruff gate (F401) on freshly generated code — see server/transforms/ being linted.
    stdlib_imports = []
    if "ipaddress.ip_address" in validators_str:
        stdlib_imports.append("import ipaddress")
    if config.auth_key_name:
        stdlib_imports.append("import os")
    if "re.match" in validators_str:
        stdlib_imports.append("import re")
    stdlib_imports_str = "\n".join(stdlib_imports)

    auth_header_expr = ""
    if config.auth_key_name:
        if config.auth_type == "bearer":
            auth_header_expr = 'headers={"Authorization": f"Bearer {api_key}"}'
        elif config.auth_type == "header":
            auth_header_expr = f'headers={{"{config.auth_header_name or "X-API-KEY"}": api_key}}'

    # TransformSetting is only referenced by the generated api_key_setting(), so an
    # unauthenticated service must not import it (F401).
    setting_import_str = ""
    if config.auth_key_name:
        setting_import_str = "from maltego.server import TransformSetting\n"

    setting_code = ""
    if config.auth_key_name:
        setting_code = f"""API_KEY = "{config.auth_key_name}"


def api_key_setting() -> TransformSetting:
    \"\"\"Return the shared API key setting declaration.\"\"\"
    return TransformSetting(
        name=API_KEY,
        display_name="{config.display_name} API Key",
        auth=True,
        is_global=True,
    )


"""

    auth_check_code = ""
    if config.auth_key_name:
        auth_check_code = f"""    api_key = settings.get(API_KEY, "") or os.environ.get(API_KEY, "")
    if not api_key:
        context.log.fatal(
            "No {config.display_name} API key configured. Enter it in Maltego Graph Desktop "
            "under the transform settings ('{config.display_name} API Key'), or export "
            "{config.auth_key_name} before starting the server."
        )
        return None
"""

    return f'''"""Shared client helpers for {config.display_name} API.

Generated by transformatron scaffold.
"""

{stdlib_imports_str}
from typing import Any

from maltego.model.context import MaltegoContext
from maltego.model.exception import MaltegoException, MaltegoHTTPDataProviderNotFound
{setting_import_str}from maltego.util import IntegrationClient

BASE_URL = "{config.base_url.rstrip("/")}"
TRANSFORM_SET = "{config.display_name}"
MAX_ITEMS = 20

client = IntegrationClient()


{setting_code}{validators_str}


async def fetch(
    path: str,
    settings: dict[str, Any],
    context: MaltegoContext,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Fetch `path` from {config.display_name}, returning None on failure."""
{auth_check_code}
    try:
        response = await client.get(
            f"{{BASE_URL}}{{path}}",
            context=context,
            {auth_header_expr + "," if auth_header_expr else ""}
            params=params,
        )
    except MaltegoHTTPDataProviderNotFound:
        context.log.inform("{config.display_name} has no record for the requested input")
        return None
    except MaltegoException as exc:
        context.log.fatal(f"{config.display_name} lookup failed: {{exc.message}}")
        return None

    data = response.json()
    if not isinstance(data, dict):
        context.log.fatal("{config.display_name} returned an unexpected response shape")
        return None
    return data
'''


def generate_transform_module(
    config: ScaffoldServiceConfig,
    transform: ScaffoldTransformConfig,
) -> str:
    """Generate a single transform module file."""
    val_func, _ = _get_validator(transform.input_entity)

    needed_entities = {transform.input_entity} | set(transform.output_entity_types)
    entities_import_str = ", ".join(sorted(needed_entities, key=_isort_key))
    union_return_str = " | ".join(sorted(set(transform.output_entity_types), key=_isort_key))

    settings_arg = "[api_key_setting()]" if config.auth_key_name else "[]"

    # MAX_ITEMS only appears in the generated body when a list mapping caps its slice,
    # and ruff's isort rule (I001) wants this list sorted. Both are F401/I001 failures
    # on generated code otherwise.
    api_imports = ["TRANSFORM_SET", val_func, "fetch"]
    if any(mapping.is_list for mapping in transform.output_mappings):
        api_imports.append("MAX_ITEMS")
    if config.auth_key_name:
        api_imports.append("api_key_setting")
    api_imports_str = ", ".join(sorted(api_imports, key=_isort_key))

    path_template = transform.endpoint_path
    if "{" in path_template:
        for placeholder in re.findall(r"\{([^}]+)\}", path_template):
            path_template = path_template.replace(f"{{{placeholder}}}", "{target}")
        path_code = f'path = f"{path_template}"\n    params = None'
    else:
        path_code = (
            f'path = "{path_template}"\n    params = {{"{transform.input_param_name}": target}}'
        )

    extraction_lines = []
    for mapping in transform.output_mappings:
        field_name = mapping.field_name
        entity_cls = mapping.entity_type
        if mapping.is_list:
            extraction_lines.append(f"""    items = data.get("{field_name}")
    if isinstance(items, list):
        for item in items[:MAX_ITEMS]:
            if item:
                results.append({entity_cls}(value=str(item)))""")
        else:
            if entity_cls == "Phrase":
                extraction_lines.append(f"""    val_{field_name} = data.get("{field_name}")
    if val_{field_name} is not None:
        results.append(Phrase(value=f"{mapping.label_prefix}{{val_{field_name}}}"))""")
            elif mapping.strip_prefix:
                extraction_lines.append(f"""    val_{field_name} = str(data.get("{field_name}", ""))
    if val_{field_name}:
        results.append({entity_cls}(value=val_{field_name}.removeprefix("{mapping.strip_prefix}")))""")
            else:
                extraction_lines.append(f"""    val_{field_name} = data.get("{field_name}")
    if val_{field_name}:
        results.append({entity_cls}(value=str(val_{field_name})))""")

    extractions_str = "\n\n".join(extraction_lines)

    return f'''"""Transforms for {config.display_name}: {transform.display_name}."""

from typing import Any

from maltego.entities import {entities_import_str}
from maltego.model.context import MaltegoContext
from maltego.server import register_transform
from transforms.{config.service_id}.api import {api_imports_str}


@register_transform(
    display_name="{transform.display_name}",
    transform_set=TRANSFORM_SET,
    settings={settings_arg},
)
async def {transform.transform_id}(
    input_entity: {transform.input_entity}, settings: dict[str, Any], context: MaltegoContext
) -> list[{union_return_str}]:
    """{transform.description or transform.display_name}"""
    try:
        target = {val_func}(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {{exc}}")
        return []

    {path_code}
    data = await fetch(path, settings, context, params=params)
    if data is None:
        return []

    results: list[{union_return_str}] = []

{extractions_str}

    if not results:
        context.log.inform("{config.display_name} returned no mapped entities for this input")

    return results
'''


def generate_service_code(config: ScaffoldServiceConfig) -> dict[str, str]:
    """Generate all files for a service integration."""
    files: dict[str, str] = {
        "__init__.py": "",
        "api.py": generate_api_module(config),
    }

    if len(config.transforms) == 1:
        files["lookup.py"] = generate_transform_module(config, config.transforms[0])
    else:
        for t in config.transforms:
            files[f"{t.transform_id}.py"] = generate_transform_module(config, t)

    return files


def inject_project_import(project_py_path: Path, module_import: str) -> bool:
    """Inject an import statement at the top of server/project.py."""
    if not project_py_path.exists():
        return False

    content = project_py_path.read_text()
    if module_import in content:
        return False

    lines = content.splitlines()
    insert_idx = 0
    for idx, line in enumerate(lines):
        if (
            line.startswith("from transforms.")
            or line.startswith("import transforms.")
            or (insert_idx == 0 and line.startswith("from maltego."))
        ):
            insert_idx = idx + 1

    lines.insert(insert_idx, module_import)
    project_py_path.write_text("\n".join(lines) + "\n")
    return True


def write_scaffold(
    config: ScaffoldServiceConfig, project_dir: Path, force: bool = False
) -> list[Path]:
    """Write generated service files to disk and wire imports into project.py.

    Args:
        config: The parsed service configuration to generate code from.
        project_dir: Directory holding ``project.py`` and ``transforms/``.
        force: Overwrite existing files instead of refusing. Off by default.

    Returns:
        The paths written, in generation order.

    Raises:
        FileExistsError: If any target file already exists and `force` is False.
            Scaffolding a service that already exists is nearly always a mistake —
            hand-written modules accumulate behaviour a generator cannot recover
            (upstream quirks found by probing the live API), and silently
            overwriting them destroys that work with no way back.
    """
    svc_dir = project_dir / "transforms" / config.service_id
    generated_files = generate_service_code(config)

    if not force:
        # Checked up front so the write is all-or-nothing: a service that collides on
        # its second file must not leave the first one replaced.
        clashes = [name for name in generated_files if (svc_dir / name).exists()]
        if clashes:
            raise FileExistsError(
                f"{config.service_id} already exists at {svc_dir} "
                f"({', '.join(sorted(clashes))}). Scaffolding would overwrite it. "
                f"Delete the directory to regenerate, or pass force=True to overwrite."
            )

    svc_dir.mkdir(parents=True, exist_ok=True)
    created_paths: list[Path] = []

    for rel_name, code in generated_files.items():
        file_path = svc_dir / rel_name
        file_path.write_text(code)
        created_paths.append(file_path)

    project_py = project_dir / "project.py"
    if project_py.exists():
        for rel_name in generated_files:
            if rel_name in ("__init__.py", "api.py"):
                continue
            mod_name = rel_name.removesuffix(".py")
            import_stmt = (
                f"from transforms.{config.service_id}.{mod_name} import *  # noqa: F401,F403"
            )
            inject_project_import(project_py, import_stmt)

    return created_paths
