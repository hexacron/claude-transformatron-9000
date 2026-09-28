"""Code generator for creating Maltego transform modules and updating server/project.py.

Everything taken from a spec or a sample response — field names, titles, paths, header
names — reaches the generated source only as an escaped string literal, never as an
identifier or raw text. A key like ``as-name`` or a title containing a quote would
otherwise produce a module that fails to import, and because ``project.py`` imports every
module at startup, one bad module takes every transform on the server down with it.
"""

from __future__ import annotations

import json
import math
import textwrap
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from transformatron.scaffold.schema import (
    OutputFieldMapping,
    ScaffoldServiceConfig,
    ScaffoldTransformConfig,
    qualified_entity_type,
)

_LINE_LIMIT = 100

_VALIDATORS = {
    "IPv4Address": ("validate_ip", "return str(ipaddress.ip_address(value.strip()))"),
    "IPv6Address": ("validate_ip", "return str(ipaddress.ip_address(value.strip()))"),
    "Domain": (
        "validate_domain",
        'domain = value.strip().lower().rstrip(".")\n'
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

# Inputs whose validator accepts `/`, `?`, `#` or spaces. Interpolated raw into a URL path,
# they would rewrite the request rather than fill one segment of it.
_FREE_TEXT_INPUTS = frozenset({"Phrase", "URL", "EmailAddress"})


def _get_validator(entity_type: str) -> tuple[str, str]:
    return _VALIDATORS.get(entity_type, ("validate_phrase", "return value.strip()"))


def _isort_key(name: str) -> tuple[int, str]:
    """Sort entity names the way ruff's isort rule orders a `from` import.

    Ruff puts fully-uppercase names (``AS``, ``CVE``, ``URL``) ahead of the rest, then
    orders what remains case-insensitively — so ``Website`` precedes ``WHOISRecord`` and
    ``BTCAddress`` precedes ``MacAddress``. Sorting the remainder by raw codepoint instead
    puts every embedded acronym in the wrong place, and generated code that disagrees with
    ruff lands an I001 violation on a file the author has not touched.
    """
    return (0 if name.isupper() else 1, name.lower())


# ---------------------------------------------------------------------------------------
# Source rendering
# ---------------------------------------------------------------------------------------


def _lit(text: str) -> str:
    """Return a Python string literal for ``text``, quoted the way ``ruff format`` would.

    JSON string escaping is valid Python string escaping, so the body is safe for any
    input. Non-ASCII characters are escaped too: a curly apostrophe or an en dash from a
    spec title is a ruff RUF001 violation when written literally, and the escape renders
    the same text at run time. Ruff prefers double quotes unless the text holds more of
    them than single quotes, in which case it switches to avoid escapes — matching that
    keeps ``ruff format --check`` passing on generated code.
    """
    body = json.dumps(text)[1:-1]
    if text.count('"') > text.count("'"):
        body = body.replace('\\"', '"').replace("'", "\\'")
        return f"'{body}'"
    return f'"{body}"'


def _braces(text: str) -> str:
    return text.replace("{", "{{").replace("}", "}}")


def _fstring(literal_text: str, expression: str) -> str:
    """Return an f-string rendering ``literal_text`` followed by ``{expression}``.

    ``_lit`` escapes quotes and backslashes but leaves braces alone, so the literal part is
    brace-escaped first and the replacement field added unescaped.
    """
    return "f" + _lit(_braces(literal_text) + "{" + expression + "}")


def _path_fstring(template: str, expression: str) -> str:
    """Return an f-string for a path template holding one ``{target}`` placeholder."""
    before, _, after = template.partition("{target}")
    return "f" + _lit(_braces(before) + "{" + expression + "}" + _braces(after))


@dataclass(frozen=True)
class _Code:
    """Source text emitted verbatim inside a rendered literal."""

    source: str


def _flat(value: Any) -> str:
    if isinstance(value, _Code):
        return value.source
    if isinstance(value, str):
        return _lit(value)
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, float) and not math.isfinite(value):
        # json.loads accepts NaN and Infinity, whose repr() is a bare name that does not
        # exist at run time; the module would fail to import and take the server down.
        return f'float("{value!r}")'
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, dict):
        inner = ", ".join(f"{_lit(str(k))}: {_flat(v)}" for k, v in value.items())
        return "{" + inner + "}"
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_flat(v) for v in value) + "]"
    return _lit(str(value))


def _render(value: Any, indent: int, head: str, tail: str) -> list[str]:
    """Render ``head + value + tail`` as lines, exploding collections the way ruff does.

    A collection that fits on its line stays flat; one that does not gets one element per
    line with a trailing comma, recursively.
    """
    pad = " " * indent
    flat = f"{pad}{head}{_flat(value)}{tail}"
    if len(flat) <= _LINE_LIMIT or not isinstance(value, dict | list | tuple) or not value:
        return [flat]
    opener, closer = ("{", "}") if isinstance(value, dict) else ("[", "]")
    lines = [f"{pad}{head}{opener}"]
    items = value.items() if isinstance(value, dict) else ((None, v) for v in value)
    for key, item in items:
        item_head = f"{_lit(str(key))}: " if key is not None else ""
        lines.extend(_render(item, indent + 4, item_head, ","))
    lines.append(f"{pad}{closer}{tail}")
    return lines


# Typographic characters common in spec prose, folded to the ASCII ruff expects in
# docstrings (RUF002). Anything else outside ASCII is dropped from docstrings, which are
# documentation only; literals keep the full text through escapes instead.
_DOCSTRING_FOLD = str.maketrans(
    {"\u2018": "'", "\u2019": "'", "\u201c": "'", "\u201d": "'", "\u2013": "-", "\u2014": "-"}
)


def _docstring(text: str, indent: int) -> str:
    """Return a docstring holding ``text``, safe for any content and within the line limit."""
    folded = unicodedata.normalize("NFKD", text.translate(_DOCSTRING_FOLD))
    ascii_text = folded.encode("ascii", "ignore").decode()
    collapsed = " ".join(ascii_text.replace("\\", "/").replace('"', "'").split()) or "Transform."
    pad = " " * indent
    lines = textwrap.wrap(collapsed, width=_LINE_LIMIT - indent - 6)
    if len(lines) == 1:
        return f'{pad}"""{lines[0]}"""'
    body = "\n".join(f"{pad}{line}" for line in lines[1:])
    return f'{pad}"""{lines[0]}\n{body}\n{pad}"""'


def _append(indent: int, entity: str, value: str) -> list[str]:
    """Return ``results.append(Entity(value=...))``, wrapped the way ruff would wrap it.

    Ruff splits the outer call first and, if the constructor still does not fit on its
    own line, the constructor's argument list too.
    """
    pad = " " * indent
    constructor = f"{entity}(value={value})"
    single = f"{pad}results.append({constructor})"
    if len(single) <= _LINE_LIMIT:
        return [single]
    if len(f"{pad}    {constructor}") <= _LINE_LIMIT:
        return [f"{pad}results.append(", f"{pad}    {constructor}", f"{pad})"]
    return [
        f"{pad}results.append(",
        f"{pad}    {entity}(",
        f"{pad}        value={value},",
        f"{pad}    )",
        f"{pad})",
    ]


def _get(indent: int, target: str, source: str, key: str) -> list[str]:
    """Return ``target = source.get("key")``, split onto three lines if it is too long."""
    pad = " " * indent
    literal = _lit(key)
    single = f"{pad}{target} = {source}.get({literal})"
    if len(single) <= _LINE_LIMIT:
        return [single]
    return [f"{pad}{target} = {source}.get(", f"{pad}    {literal}", f"{pad})"]


# ---------------------------------------------------------------------------------------
# api.py
# ---------------------------------------------------------------------------------------


def generate_api_module(config: ScaffoldServiceConfig) -> str:
    """Generate the api.py client helper for a service."""
    used_validators: dict[str, str] = {}
    for t in config.transforms:
        val_func, val_impl = _get_validator(t.input_entity)
        used_validators[val_func] = val_impl

    # Two blank lines between top-level defs, or `ruff format --check` reformats the
    # generated file and the project's format gate fails on brand-new code.
    validators_str = "\n\n\n".join(
        f'def {name}(value: str) -> str:\n    """Validate and normalise the input value, or raise'
        f' ValueError."""\n    {body}'
        for name, body in used_validators.items()
    )

    authed = config.auth_key_name is not None
    bodies = [t.body_encoding for t in config.transforms if t.request_body is not None]
    uses_json = "json" in bodies
    uses_form = "form" in bodies

    # Emit each stdlib import only where it is used: an unused one fails ruff (F401) on
    # freshly generated code, and server/transforms/ is linted.
    stdlib_imports = []
    if "ipaddress.ip_address" in validators_str:
        stdlib_imports.append("import ipaddress")
    if authed:
        stdlib_imports.append("import os")
    if "re.match" in validators_str:
        stdlib_imports.append("import re")
    stdlib_imports_str = "".join(f"{line}\n" for line in stdlib_imports)
    setting_import_str = "from maltego.server import TransformSetting\n" if authed else ""

    setting_code = ""
    auth_check_code = ""
    headers_arg = ""
    if authed:
        setting_code = f"""API_KEY = {_lit(config.auth_key_name or "")}


def api_key_setting() -> TransformSetting:
    \"\"\"Return the shared API key setting declaration.\"\"\"
    return TransformSetting(
        name=API_KEY,
        display_name=f"{{SERVICE_NAME}} API Key",
        auth=True,
        is_global=True,
    )


"""
        auth_check_code = """    api_key = settings.get(API_KEY, "") or os.environ.get(API_KEY, "")
    if not api_key:
        context.log.fatal(
            f"No {SERVICE_NAME} API key configured. Enter it in Maltego Graph Desktop under "
            f"the transform settings ('{SERVICE_NAME} API Key'), or export {API_KEY} before "
            "starting the server."
        )
        return None
"""
        if config.auth_type == "query":
            query_param = config.auth_query_param or config.auth_header_name or "apikey"
            # params may arrive as None, so it is copied before the key is added.
            auth_check_code += (
                f"    params = dict(params or {{}})\n    params[{_lit(query_param)}] = api_key\n"
            )
        elif config.auth_type == "bearer":
            headers_arg = '            headers={"Authorization": f"Bearer {api_key}"},\n'
        else:
            header_name = _lit(config.auth_header_name or "X-API-KEY")
            headers_arg = f"            headers={{{header_name}: api_key}},\n"

    body_params = ""
    body_args = ""
    if uses_json:
        body_params += "    json_body: dict[str, Any] | None = None,\n"
        body_args += "            json=json_body,\n"
    if uses_form:
        body_params += "    form: dict[str, Any] | None = None,\n"
        body_args += "            data=form,\n"

    module_doc = _docstring(
        f"Shared client helpers for the {config.display_name} API. Generated by "
        "transformatron scaffold.",
        0,
    )
    return f'''{module_doc}

{stdlib_imports_str}from typing import Any

from maltego.model.context import MaltegoContext
from maltego.model.exception import MaltegoException, MaltegoHTTPDataProviderNotFound
{setting_import_str}from maltego.util import IntegrationClient

BASE_URL = {_lit(config.base_url.rstrip("/"))}
SERVICE_NAME = {_lit(config.display_name)}
TRANSFORM_SET = SERVICE_NAME
MAX_ITEMS = 20

client = IntegrationClient()


{setting_code}{validators_str}


async def fetch(
    path: str,
    settings: dict[str, Any],
    context: MaltegoContext,
    method: str = "GET",
    params: dict[str, Any] | None = None,
{body_params}) -> Any:
    """Request `path` and return the decoded JSON, or None on failure."""
{auth_check_code}    try:
        response = await client.request(
            method,
            f"{{BASE_URL}}{{path}}",
            context=context,
{headers_arg}            params=params,
{body_args}        )
    except MaltegoHTTPDataProviderNotFound:
        context.log.inform(f"{{SERVICE_NAME}} has no record for the requested input")
        return None
    except MaltegoException as exc:
        context.log.fatal(f"{{SERVICE_NAME}} lookup failed: {{exc.message}}")
        return None

    try:
        return response.json()
    except ValueError:
        context.log.fatal(f"{{SERVICE_NAME}} returned a response that is not JSON")
        return None
'''


# ---------------------------------------------------------------------------------------
# Transform modules
# ---------------------------------------------------------------------------------------


def _camel(snake: str) -> str:
    """Convert a snake_case transform id into a CamelCase name for a type alias."""
    return "".join(part.title() for part in snake.split("_") if part)


def _format_import(module: str, names: list[str]) -> str:
    """Render ``from module import ...``, wrapping one name per line when it would be long.

    A response with many mapped fields, or a long service id, pushes the single-line form
    past the project's 100-character limit (E501), and ruff's formatter then rewrites it,
    failing ``ruff format --check`` on freshly generated code.
    """
    single_line = f"from {module} import {', '.join(names)}"
    if len(single_line) <= _LINE_LIMIT:
        return single_line
    joined = "".join(f"    {name},\n" for name in names)
    return f"from {module} import (\n{joined})"


def _value_expr(mapping: OutputFieldMapping, var: str) -> str:
    """Return the expression turning the raw value in ``var`` into an entity value."""
    if mapping.entity_type == "Phrase" and mapping.label_prefix:
        return _fstring(mapping.label_prefix, var)
    if mapping.strip_prefix:
        return f"str({var}).removeprefix({_lit(mapping.strip_prefix)})"
    if mapping.entity_type in ("Domain", "DNSName"):
        return f'str({var}).rstrip(".")'
    return f"str({var})"


def _present(mapping: OutputFieldMapping, var: str) -> str:
    """Return the test for whether a mapped value is worth emitting.

    A Phrase keeps falsy values (``False``, ``0``) because they are information; any other
    entity needs a non-empty value to be meaningful.
    """
    return f"{var} is not None" if mapping.entity_type == "Phrase" else var


def _extraction(mapping: OutputFieldMapping, source: str, indent: int) -> list[str]:
    """Return the statements that turn one mapped field of ``source`` into entities."""
    pad = " " * indent
    entity = mapping.entity_type
    if mapping.is_list:
        lines = [
            *_get(indent, "items", source, mapping.field_name),
            f"{pad}if isinstance(items, list):",
            f"{pad}    for item in items[:MAX_ITEMS]:",
        ]
        if mapping.sub_field is None:
            lines.append(f"{pad}        if {_present(mapping, 'item')}:")
            lines.extend(_append(indent + 12, entity, _value_expr(mapping, "item")))
        else:
            lines += [
                f"{pad}        if not isinstance(item, dict):",
                f"{pad}            continue",
                *_get(indent + 8, "value", "item", mapping.sub_field),
                f"{pad}        if {_present(mapping, 'value')}:",
            ]
            lines.extend(_append(indent + 12, entity, _value_expr(mapping, "value")))
        return lines
    if mapping.sub_field is not None:
        lines = [
            *_get(indent, "nested", source, mapping.field_name),
            f"{pad}if isinstance(nested, dict):",
            *_get(indent + 4, "value", "nested", mapping.sub_field),
            f"{pad}    if {_present(mapping, 'value')}:",
        ]
        lines.extend(_append(indent + 8, entity, _value_expr(mapping, "value")))
        return lines
    lines = [
        *_get(indent, "value", source, mapping.field_name),
        f"{pad}if {_present(mapping, 'value')}:",
    ]
    lines.extend(_append(indent + 4, entity, _value_expr(mapping, "value")))
    return lines


def _request_lines(transform: ScaffoldTransformConfig) -> tuple[list[str], list[str], bool]:
    """Return the request-building statements, the fetch() keyword args, and whether the
    statements use ``quote``."""
    lines: list[str] = []
    kwargs: list[str] = []
    uses_quote = False
    target = _Code("target")

    if transform.input_location == "path" and "{target}" in transform.endpoint_path:
        expression = "target"
        if transform.input_entity in _FREE_TEXT_INPUTS:
            lines.append('    encoded = quote(target, safe="")')
            expression = "encoded"
            uses_quote = True
        lines.append(f"    path = {_path_fstring(transform.endpoint_path, expression)}")
    else:
        lines.append(f"    path = {_lit(transform.endpoint_path)}")

    query = dict(transform.query_params)
    if transform.input_location == "query":
        query[transform.input_param_name] = target
    if query:
        lines.extend(_render(query, 4, "params = ", ""))
        kwargs.append("params=params")

    if transform.request_body is not None:
        body = dict(transform.request_body)
        if transform.input_location == "body":
            body[transform.input_param_name] = target
        lines.extend(_render(body, 4, "body = ", ""))
        kwargs.append("json_body=body" if transform.body_encoding == "json" else "form=body")

    if transform.http_method != "GET":
        kwargs.insert(0, f"method={_lit(transform.http_method)}")
    return lines, kwargs, uses_quote


def generate_transform_module(
    config: ScaffoldServiceConfig,
    transform: ScaffoldTransformConfig,
) -> str:
    """Generate a single transform module file."""
    val_func, _ = _get_validator(transform.input_entity)

    output_types = sorted(set(transform.output_entity_types), key=_isort_key)
    needed_entities = {transform.input_entity} | set(output_types)

    # A transform emitting many entity types produces a union too long to inline at both
    # the return annotation and the results declaration (E501). Naming it once keeps both
    # sites short whatever the width. Three widths, matching what ruff's formatter would
    # settle on: the bare assignment if it fits, else the body on its own indented line
    # inside parens if *that* fits, else one operand per line.
    alias_name = f"{_camel(transform.transform_id)}Output"
    alias_body = " | ".join(output_types)
    if len(f"{alias_name} = {alias_body}") <= _LINE_LIMIT:
        alias_decl = f"{alias_name} = {alias_body}\n\n\n"
    elif len(f"    {alias_body}") <= _LINE_LIMIT:
        alias_decl = f"{alias_name} = (\n    {alias_body}\n)\n\n\n"
    else:
        wrapped = "\n    | ".join(output_types)
        alias_decl = f"{alias_name} = (\n    {wrapped}\n)\n\n\n"

    settings_arg = "[api_key_setting()]" if config.auth_key_name else "[]"

    # Without an input constraint the Maltego client offers the transform on every entity
    # of its input type. EntityTypeConstraint matches parent types too, so this does not
    # over-narrow subclasses of the declared input.
    constraint_arg = ""
    constraint_import_str = ""
    if transform.emit_input_constraint:
        qualified = _lit(qualified_entity_type(transform.input_entity))
        constraint_arg = f"\n    input_constraint=EntityTypeConstraint(entity_type={qualified}),"
        constraint_import_str = "from maltego.model.input_constraints import EntityTypeConstraint\n"

    request_lines, fetch_kwargs, uses_quote = _request_lines(transform)
    fetch_args = ", ".join(["path", "settings", "context", *fetch_kwargs])
    fetch_call = f"    data = await fetch({fetch_args})"
    if len(fetch_call) > _LINE_LIMIT:
        joined = "".join(f"        {arg},\n" for arg in fetch_args.split(", "))
        fetch_call = f"    data = await fetch(\n{joined}    )"

    uses_max_items = transform.response_is_list or any(
        mapping.is_list for mapping in transform.output_mappings
    )
    api_imports = ["TRANSFORM_SET", val_func, "fetch"]
    if uses_max_items:
        api_imports.append("MAX_ITEMS")
    if config.auth_key_name:
        api_imports.append("api_key_setting")
    quote_import_str = "from urllib.parse import quote\n" if uses_quote else ""

    if transform.response_is_list:
        expected_shape = "list"
        record_indent = 8
        loop = (
            "    for record in data[:MAX_ITEMS]:\n"
            "        if not isinstance(record, dict):\n"
            "            continue\n"
        )
    else:
        expected_shape = "dict"
        record_indent = 4
        loop = ""
    source = "record" if transform.response_is_list else "data"
    blocks = ["\n".join(_extraction(m, source, record_indent)) for m in transform.output_mappings]
    extractions_str = "\n\n".join(blocks)

    module_doc = _docstring(f"Transforms for {config.display_name}: {transform.display_name}.", 0)
    request_str = "\n".join(request_lines)

    return f"""{module_doc}

from typing import Any
{quote_import_str}
{_format_import("maltego.entities", sorted(needed_entities, key=_isort_key))}
from maltego.model.context import MaltegoContext
{constraint_import_str}from maltego.server import register_transform
{_format_import(f"transforms.{config.service_id}.api", sorted(api_imports, key=_isort_key))}

{alias_decl}@register_transform(
    display_name={_lit(transform.display_name)},
    transform_set=TRANSFORM_SET,
    settings={settings_arg},{constraint_arg}
)
async def {transform.transform_id}(
    input_entity: {transform.input_entity}, settings: dict[str, Any], context: MaltegoContext
) -> list[{alias_name}]:
{_docstring(transform.description or transform.display_name, 4)}
    try:
        target = {val_func}(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {{exc}}")
        return []

{request_str}
{fetch_call}
    if data is None:
        return []
    if not isinstance(data, {expected_shape}):
        context.log.fatal(f"{{TRANSFORM_SET}} returned an unexpected response shape")
        return []

    results: list[{alias_name}] = []
{loop}{extractions_str}

    if not results:
        context.log.inform(f"{{TRANSFORM_SET}} returned no mapped entities for this input")

    return results
"""


def generate_service_code(config: ScaffoldServiceConfig) -> dict[str, str]:
    """Generate all files for a service integration."""
    files: dict[str, str] = {
        "__init__.py": "",
        "api.py": generate_api_module(config),
    }

    if len(config.transforms) == 1:
        files["lookup.py"] = generate_transform_module(config, config.transforms[0])
    else:
        # Distinct operations can slugify to the same id ("do scan" and "do-scan" both
        # become do_scan). Keying the dict on that alone silently dropped every transform
        # but the last, and the function name collided inside the module too, so the
        # server registered one of them. Suffix the duplicates instead. `api` is taken by
        # the shared client: an operation named "API" would otherwise overwrite it, and
        # every sibling's `from .api import fetch` would fail at server startup.
        used = {"api"}
        for t in config.transforms:
            name, n = t.transform_id, 1
            while name in used:
                n += 1
                name = f"{t.transform_id}_{n}"
            used.add(name)
            unique = t if name == t.transform_id else replace(t, transform_id=name)
            files[f"{name}.py"] = generate_transform_module(config, unique)

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
                f"({', '.join(sorted(clashes))}). Scaffolding would overwrite it. Choose "
                f"another service name, delete the directory, or scaffold again with force "
                f"(--force on the CLI) to overwrite."
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
