"""Parsers for converting cURL commands and OpenAPI specs into a service config."""

from __future__ import annotations

import json
import keyword
import re
import shlex
from typing import Any
from urllib.parse import parse_qsl, unquote, urljoin, urlparse

from transformatron.scaffold.schema import (
    OutputFieldMapping,
    ScaffoldServiceConfig,
    ScaffoldTransformConfig,
    infer_input_entity,
    infer_output_mappings,
)

# Query parameters that carry a credential rather than the thing being looked up.
_AUTH_QUERY_RE = re.compile(
    r"^(api[_-]?key|apikey|key|token|access[_-]?token|auth[_-]?token|auth)$", re.I
)
_SERVICE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_GENERIC_HOST_LABELS = frozenset({"api", "www", "v1", "v2", "v3"})

# curl options that take a value, so the value is never mistaken for the URL.
_CURL_VALUE_FLAGS = frozenset(
    {
        "-A",
        "--user-agent",
        "-o",
        "--output",
        "-e",
        "--referer",
        "-m",
        "--max-time",
        "--connect-timeout",
        "-b",
        "--cookie",
        "-x",
        "--proxy",
        "--retry",
        "-w",
        "--write-out",
    }
)
_CURL_DATA_FLAGS = frozenset(
    {"-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode"}
)

_FALLBACK_OUTPUT = OutputFieldMapping(
    field_name="result", entity_type="Phrase", label_prefix="Result: "
)
_DISPLAY_NAME_LIMIT = 80
_SEARCH_FIELD_RE = re.compile(
    r"^(q|query|search|term|keyword|keywords|text|name|username|user|value)$", re.I
)


def _slugify(text: str) -> str:
    """Convert text into an ASCII snake_case slug; ``getPetById`` becomes ``get_pet_by_id``.

    ASCII only, and never ending in an underscore: the SDK rejects transform names outside
    ``[A-Za-z0-9_.-]`` or ending in a separator, and it rejects them when the server
    starts, so a bad name takes every other transform down with it.
    """
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text.strip())
    text = re.sub(r"[^a-z0-9\s_-]", "", text.lower())
    return re.sub(r"[-\s_]+", "_", text).strip("_")


def _is_identifier(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name)


def _service_id(service_name: str | None, derived: str) -> str:
    """Return the package name for the service, or raise if it cannot be one.

    The id becomes a directory under ``transforms/`` and a dotted import in
    ``project.py``, so anything that is not a Python identifier breaks the server's startup
    import — and a path separator would write outside ``transforms/`` altogether.
    """
    if service_name is not None:
        svc_id = _slugify(service_name)
        if not _SERVICE_NAME_RE.match(service_name) or not _is_identifier(svc_id):
            raise ValueError(
                f"Service name {service_name!r} is not usable as a package name. Use letters, "
                "digits, underscores and hyphens, starting with a letter, and not a Python "
                "keyword."
            )
        return svc_id
    if not _is_identifier(derived):
        raise ValueError(
            f"Could not derive a package name from {derived!r}. Pass a service name, e.g. "
            "--service my_api."
        )
    return derived


def _function_name(text: str) -> str:
    """Return a Python function name for an operation id."""
    slug = _slugify(text)
    if not slug:
        return "operation"
    return slug if _is_identifier(slug) else f"op_{slug}"


def _display_name(title: str, summary: str) -> str:
    """Return ``title: summary`` short enough to fit a Maltego menu and the line limit.

    Measured escaped, because that is how it is written into the generated module: a
    ``display_name=`` argument longer than the line limit fails the project's lint gate.
    """
    name = f"{title}: {' '.join(summary.split())}"
    if len(json.dumps(name)) - 2 <= _DISPLAY_NAME_LIMIT:
        return name
    words = name.split(" ")
    while words and len(json.dumps(" ".join(words) + "...")) - 2 > _DISPLAY_NAME_LIMIT:
        words.pop()
    return " ".join(words) + "..." if words else name[: _DISPLAY_NAME_LIMIT - 3] + "..."


def _load_sample(sample_response: Any) -> Any:
    if isinstance(sample_response, str):
        try:
            return json.loads(sample_response)
        except json.JSONDecodeError as exc:
            raise ValueError(f"The sample response is not valid JSON: {exc}") from exc
    return sample_response


def _outputs_from_sample(sample: Any) -> tuple[list[OutputFieldMapping], bool]:
    """Return the output mappings for a sample response, and whether it is a list."""
    if isinstance(sample, dict):
        return infer_output_mappings(sample), False
    if isinstance(sample, list) and sample and isinstance(sample[0], dict):
        return infer_output_mappings(sample[0]), True
    return [], isinstance(sample, list)


# ---------------------------------------------------------------------------------------
# cURL
# ---------------------------------------------------------------------------------------


def _parse_body(raw: str) -> tuple[dict[str, Any], str]:
    """Return a request body and its encoding: a JSON object, or form fields."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        return dict(pairs), "form"
    if not isinstance(parsed, dict):
        raise ValueError(
            "The request body is JSON but not an object. Scaffolding supports object bodies "
            "and form fields; write this request by hand."
        )
    return parsed, "json"


def parse_curl_command(
    curl_cmd: str,
    sample_response: dict[str, Any] | list[Any] | str | None = None,
    service_name: str | None = None,
) -> ScaffoldServiceConfig:
    """Parse a cURL command and optional sample JSON response into a service config."""
    tokens = shlex.split(re.sub(r"\\\s*\n", " ", curl_cmd.strip()))

    method: str | None = None
    url = ""
    headers: dict[str, str] = {}
    data_parts: list[str] = []
    data_in_query = False
    notes: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        value = tokens[i + 1] if i + 1 < len(tokens) else None
        if token in ("-X", "--request") and value is not None:
            method = value.upper()
        elif token in ("-H", "--header") and value is not None:
            key, sep, header_value = value.partition(":")
            if sep:
                headers[key.strip()] = header_value.strip()
        elif (token in _CURL_DATA_FLAGS or token == "--json") and value is not None:
            data_parts.append(value)
        elif token == "--url" and value is not None:
            url = value
        elif token in ("-u", "--user") and value is not None:
            notes.append("Basic authentication (-u) is not scaffolded; add it to api.py by hand.")
        elif token in _CURL_VALUE_FLAGS and value is not None:
            pass
        else:
            if token in ("-G", "--get"):
                data_in_query = True
            elif not url and not token.startswith("-") and re.match(r"^https?://", token):
                url = token
            i += 1
            continue
        i += 2

    if not url:
        raise ValueError("Could not find an http:// or https:// URL in the cURL command.")

    parsed_url = urlparse(url)
    host = parsed_url.hostname or ""
    labels = host.split(".")
    derived = _slugify(labels[0]) if labels else "service"
    if derived in _GENERIC_HOST_LABELS and len(labels) >= 2:
        derived = _slugify(labels[-2])
    svc_id = _service_id(service_name, derived)
    svc_title = svc_id.replace("_", " ").title()

    # Authentication: a key-like header, or a key-like query parameter.
    auth_type = "none"
    auth_header_name: str | None = None
    auth_query_param: str | None = None
    for h_name, h_val in headers.items():
        h_upper = h_name.upper()
        if "AUTH" in h_upper or "KEY" in h_upper or "TOKEN" in h_upper:
            auth_header_name = h_name
            auth_type = "bearer" if h_val.lower().startswith("bearer") else "header"
            break

    query = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
    for q_name in list(query):
        if _AUTH_QUERY_RE.match(q_name):
            del query[q_name]
            if auth_type == "none":
                auth_type = "query"
                auth_query_param = q_name

    body: dict[str, Any] | None = None
    body_encoding = "json"
    if data_parts:
        raw_body = "&".join(data_parts)
        if data_in_query:
            query.update(parse_qsl(raw_body, keep_blank_values=True))
        else:
            body, body_encoding = _parse_body(raw_body)
    http_method = method or ("POST" if body is not None else "GET")

    # Input: the first part of the request whose name or value looks like an entity, with
    # the stronger types preferred over a domain-looking value. Falling back to a Phrase
    # keeps free-text endpoints (a username, a search term) working.
    path = parsed_url.path or "/"
    segments = [s for s in path.split("/") if s]
    candidates: list[tuple[str, str | int, str]] = [("query", k, v) for k, v in query.items()]
    candidates += [("path", idx, unquote(seg)) for idx, seg in enumerate(segments)]
    if body is not None:
        candidates += [("body", k, v) for k, v in body.items() if isinstance(v, str)]

    def entity_of(candidate: tuple[str, str | int, str]) -> str:
        location, key, sample = candidate
        return infer_input_entity(str(key) if location != "path" else "", sample)[0]

    typed = [c for c in candidates if entity_of(c) != "Phrase"]
    typed.sort(key=lambda c: entity_of(c) == "Domain")
    chosen = typed[0] if typed else None
    if chosen is None:
        # No field looks like an entity, so this is a free-text endpoint. A field named like
        # a search term wins, then a lone query or body field, then any non-numeric text
        # field (`limit=5` is a setting, not the input). A path segment is the last resort,
        # since it is usually the resource rather than the thing looked up.
        fields = [c for c in candidates if c[0] != "path"]
        body_fields = [c for c in fields if c[0] == "body"]
        chosen = next((c for c in fields if _SEARCH_FIELD_RE.match(str(c[1]))), None)
        if chosen is None and len(query) == 1:
            chosen = next(c for c in fields if c[0] == "query")
        if chosen is None and len(body_fields) == 1:
            chosen = body_fields[0]
        chosen = chosen or next((c for c in fields if c[2] and not c[2].isdigit()), None)
        if chosen is None and segments:
            chosen = ("path", len(segments) - 1, unquote(segments[-1]))
    if chosen is None:
        raise ValueError(
            "Could not tell which part of the request carries the input. Put a real sample "
            "value (an IP, a domain, a username) in the URL, query string, or body."
        )

    location, key, _sample = chosen
    input_entity = entity_of(chosen)
    endpoint_path = path
    input_param_name = "target"
    if location == "path":
        parts = [seg if idx != key else "{target}" for idx, seg in enumerate(segments)]
        endpoint_path = "/" + "/".join(parts) + ("/" if path.endswith("/") else "")
    else:
        input_param_name = str(key)

    outputs, response_is_list = _outputs_from_sample(_load_sample(sample_response))

    transform = ScaffoldTransformConfig(
        transform_id=f"{svc_id}_lookup",
        display_name=f"{svc_title}: Lookup",
        input_entity=input_entity,
        endpoint_path=endpoint_path,
        input_param_name=input_param_name,
        input_location=location,
        http_method=http_method,
        output_mappings=outputs or [_FALLBACK_OUTPUT],
        description=f"Look up a {input_entity} with {svc_title}.",
        query_params=query,
        request_body=body,
        body_encoding=body_encoding,
        response_is_list=response_is_list,
    )

    return ScaffoldServiceConfig(
        service_id=svc_id,
        display_name=svc_title,
        base_url=f"{parsed_url.scheme}://{parsed_url.netloc}",
        auth_key_name=f"{svc_id.upper()}_API_KEY" if auth_type != "none" else None,
        auth_header_name=auth_header_name,
        auth_query_param=auth_query_param,
        auth_type=auth_type,
        transforms=[transform],
        notes=notes,
    )


# ---------------------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------------------

_EXAMPLE_BY_FORMAT = {
    "ipv4": "192.0.2.1",
    "ipv6": "2001:db8::1",
    "email": "user@example.com",
    "uri": "https://example.com",
    "url": "https://example.com",
    "hostname": "example.com",
}
_EXAMPLE_DEPTH_LIMIT = 4


class _Spec:
    """An OpenAPI document with local ``$ref`` resolution."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document

    def resolve(self, node: Any) -> Any:
        """Follow ``$ref`` pointers until reaching a concrete node.

        Only local refs (``#/...``) are followed; a remote or cyclic ref resolves to an
        empty object rather than recursing forever.
        """
        seen: set[str] = set()
        while isinstance(node, dict) and isinstance(node.get("$ref"), str):
            ref = node["$ref"]
            if not ref.startswith("#/") or ref in seen:
                return {}
            seen.add(ref)
            target: Any = self.document
            for part in ref[2:].split("/"):
                part = unquote(part).replace("~1", "/").replace("~0", "~")
                target = target.get(part) if isinstance(target, dict) else None
            node = target if target is not None else {}
        return node

    def example(self, schema: Any, depth: int = 0) -> Any:
        """Build a representative value for a schema, preferring the spec's own examples."""
        schema = self.resolve(schema)
        if not isinstance(schema, dict) or depth > _EXAMPLE_DEPTH_LIMIT:
            return None
        if "example" in schema:
            return schema["example"]
        if "allOf" in schema:
            merged: dict[str, Any] = {}
            for part in schema["allOf"]:
                value = self.example(part, depth + 1)
                if isinstance(value, dict):
                    merged.update(value)
            return merged
        for combinator in ("oneOf", "anyOf"):
            if schema.get(combinator):
                return self.example(schema[combinator][0], depth + 1)

        schema_type = schema.get("type")
        if isinstance(schema_type, list):
            schema_type = next((t for t in schema_type if t != "null"), None)
        if schema_type == "array" or "items" in schema:
            item = self.example(schema.get("items", {}), depth + 1)
            return [item] if item is not None else []
        if schema_type == "object" or "properties" in schema:
            properties = schema.get("properties", {}) or {}
            return {name: self.example(prop, depth + 1) for name, prop in properties.items()}
        if schema.get("enum"):
            return schema["enum"][0]
        if "default" in schema:
            return schema["default"]
        if schema_type == "string":
            return _EXAMPLE_BY_FORMAT.get(str(schema.get("format", "")).lower(), "string")
        if schema_type in ("integer", "number"):
            return 0
        if schema_type == "boolean":
            return False
        return None


def _json_content(container: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON media type entry of a response or request body, if any."""
    content = container.get("content", {}) or {}
    for media_type, entry in content.items():
        if "json" in media_type and isinstance(entry, dict):
            return entry
    return {}


def _base_url(spec: dict[str, Any], spec_url: str | None, notes: list[str]) -> str:
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict) and servers[0].get("url"):
        server = servers[0]
        url = str(server["url"])
        for name, variable in (server.get("variables") or {}).items():
            url = url.replace("{" + name + "}", str(variable.get("default", "")))
    elif "host" in spec:
        schemes = spec.get("schemes") or ["https"]
        url = f"{schemes[0]}://{spec['host']}{spec.get('basePath', '')}"
    else:
        url = ""

    if not urlparse(url).scheme:
        if spec_url:
            url = urljoin(spec_url, url or "/")
        else:
            notes.append(
                "The spec does not give an absolute server URL, so BASE_URL in api.py is a "
                "placeholder. Set it, or scaffold from the spec's URL instead of a file."
            )
            url = "https://api.example.com" + url
    return url.rstrip("/")


def _parameter_value(spec: _Spec, param: dict[str, Any]) -> Any:
    """Return a usable value for a parameter the input does not fill, or None."""
    schema = spec.resolve(param.get("schema", {})) or {}
    for source in (param, schema):
        if "default" in source:
            return source["default"]
        if source.get("enum"):
            return source["enum"][0]
        if "example" in source:
            return source["example"]
    return None


def parse_openapi_spec(
    spec: dict[str, Any] | str,
    service_name: str | None = None,
    operations: list[str] | None = None,
    spec_url: str | None = None,
) -> ScaffoldServiceConfig:
    """Parse an OpenAPI (v3) or Swagger (v2) specification into a service config.

    Args:
        spec: The document, parsed or as JSON text.
        service_name: Package name for the service; derived from the title if omitted.
        operations: Operation ids to scaffold. Without it every GET operation is
            scaffolded and POST operations are skipped, because a POST may create or
            change data upstream and should be chosen deliberately.
        spec_url: Where the document came from, to resolve a relative server URL.

    Raises:
        ValueError: If the spec is malformed, names nothing to scaffold, or an
            operation in ``operations`` does not exist.
    """
    document = json.loads(spec) if isinstance(spec, str) else spec
    if not isinstance(document, dict):
        raise ValueError("The OpenAPI spec is not a JSON object.")
    resolver = _Spec(document)
    notes: list[str] = []

    title = str((document.get("info") or {}).get("title") or "API Service")
    svc_id = _service_id(service_name, _slugify(title))
    base_url = _base_url(document, spec_url, notes)

    # Authentication: the first scheme the generator can express.
    auth_type = "none"
    auth_header_name: str | None = None
    auth_query_param: str | None = None
    components = document.get("components") or {}
    schemes = components.get("securitySchemes") or document.get("securityDefinitions") or {}
    unsupported: list[str] = []
    for scheme_name, scheme in schemes.items():
        scheme = resolver.resolve(scheme)
        if not isinstance(scheme, dict):
            continue
        scheme_type = str(scheme.get("type", "")).lower()
        location = scheme.get("in")
        if scheme_type == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
            auth_type, auth_header_name = "bearer", "Authorization"
            break
        if scheme_type == "apikey" and location in ("header", "query") and scheme.get("name"):
            auth_type = "header" if location == "header" else "query"
            auth_header_name = scheme["name"] if location == "header" else None
            auth_query_param = scheme["name"] if location == "query" else None
            break
        unsupported.append(f"{scheme_name} ({scheme_type or 'unknown'})")
    if auth_type == "none":
        # Some specs declare no scheme and instead list the key as an ordinary query
        # parameter on every operation. Treated as data, it would be sent as a literal
        # placeholder or even chosen as the input, so it is recognised as auth here.
        auth_query_param = _declared_key_param(resolver, document)
        if auth_query_param:
            auth_type = "query"
    if auth_type == "none" and unsupported:
        notes.append(
            f"The spec's authentication ({', '.join(unsupported)}) is not scaffolded; "
            "requests go out unauthenticated until you add it to api.py."
        )

    wanted = set(operations or [])
    found: set[str] = set()
    skipped_posts: list[str] = []
    transforms: list[ScaffoldTransformConfig] = []

    for path_str, raw_item in (document.get("paths") or {}).items():
        path_item = resolver.resolve(raw_item)
        if not isinstance(path_item, dict):
            continue
        shared_params = path_item.get("parameters") or []
        for http_method in ("get", "post"):
            operation = resolver.resolve(path_item.get(http_method))
            if not isinstance(operation, dict) or not operation:
                continue
            op_key = str(operation.get("operationId") or f"{http_method}_{path_str}")
            transform_id = _function_name(op_key)
            if wanted:
                matches = wanted & {op_key, transform_id}
                if not matches:
                    continue
                found |= matches
            elif http_method == "post":
                skipped_posts.append(op_key)
                continue

            transform, reason = _openapi_transform(
                resolver,
                path_str,
                http_method,
                operation,
                shared_params,
                transform_id,
                title,
                auth_query_param,
            )
            if transform is None:
                notes.append(f"Skipped {op_key}: {reason}")
                continue
            transforms.append(transform)

    missing = sorted(wanted - found)
    if missing:
        raise ValueError(
            f"No operation named {', '.join(missing)} in the spec. Use an operationId from "
            "the spec, or the snake_case name the scaffolder derives from it."
        )
    if skipped_posts:
        notes.append(
            f"Skipped {len(skipped_posts)} POST operation(s), which may create or change data "
            f"upstream: {', '.join(skipped_posts)}. Name one with --operation to scaffold it."
        )
    if not transforms:
        raise ValueError(
            "Nothing to scaffold. " + (" ".join(notes) if notes else "The spec has no operations.")
        )

    return ScaffoldServiceConfig(
        service_id=svc_id,
        display_name=title,
        base_url=base_url,
        auth_key_name=f"{svc_id.upper()}_API_KEY" if auth_type != "none" else None,
        auth_header_name=auth_header_name,
        auth_query_param=auth_query_param,
        auth_type=auth_type,
        transforms=transforms,
        notes=notes,
    )


def _operations(spec: _Spec, document: dict[str, Any]) -> list[tuple[list[Any], dict[str, Any]]]:
    """Return (path-level parameters, operation) for every GET and POST operation."""
    found = []
    for raw_item in (document.get("paths") or {}).values():
        path_item = spec.resolve(raw_item)
        if not isinstance(path_item, dict):
            continue
        for http_method in ("get", "post"):
            operation = spec.resolve(path_item.get(http_method))
            if isinstance(operation, dict) and operation:
                found.append((path_item.get("parameters") or [], operation))
    return found


def _declared_key_param(spec: _Spec, document: dict[str, Any]) -> str | None:
    """Return a key-like query parameter every operation declares, if there is one.

    Requiring it on every operation keeps an ordinary field that happens to be called
    ``token`` (a pagination cursor, say) from being mistaken for the credential.
    """
    operations = _operations(spec, document)
    if not operations:
        return None
    per_operation = [
        {
            param["name"]
            for raw in [*shared, *(operation.get("parameters") or [])]
            if isinstance(param := spec.resolve(raw), dict)
            and param.get("in") == "query"
            and _AUTH_QUERY_RE.match(str(param.get("name", "")))
        }
        for shared, operation in operations
    ]
    common = set.intersection(*per_operation)
    return min(common) if common else None


def _openapi_transform(
    spec: _Spec,
    path_str: str,
    http_method: str,
    operation: dict[str, Any],
    shared_params: list[Any],
    transform_id: str,
    title: str,
    auth_query_param: str | None,
) -> tuple[ScaffoldTransformConfig | None, str]:
    """Build one transform from an operation, or return why it cannot be scaffolded."""
    # Operation-level parameters override path-level ones with the same name and location.
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in [*shared_params, *(operation.get("parameters") or [])]:
        param = spec.resolve(raw)
        if isinstance(param, dict) and param.get("name") and param.get("in"):
            merged[(param["name"], param["in"])] = param
    # The credential is sent by api.py; as an ordinary parameter it would go out as a
    # placeholder value, or be chosen as the input.
    params = [
        p
        for p in merged.values()
        if p["in"] in ("path", "query")
        and not (p["in"] == "query" and p["name"] == auth_query_param)
    ]

    # The body: an OpenAPI 3 requestBody, or a Swagger 2 `in: body` / `in: formData` param.
    body_schema: dict[str, Any] = {}
    body_encoding = "json"
    body_required: set[str] = set()
    body_example: dict[str, Any] | None = None
    request_body = spec.resolve(operation.get("requestBody") or {})
    swagger_body = next((p for p in merged.values() if p["in"] == "body"), None)
    form_params = [p for p in merged.values() if p["in"] == "formData"]
    if isinstance(request_body, dict) and request_body:
        body_schema = spec.resolve(_json_content(request_body).get("schema", {})) or {}
    elif swagger_body is not None:
        body_schema = spec.resolve(swagger_body.get("schema", {})) or {}
    if body_schema:
        example = spec.example(body_schema)
        body_example = example if isinstance(example, dict) else None
        body_required = set(body_schema.get("required") or [])
    elif form_params:
        body_encoding = "form"
        body_example = {
            p["name"]: _parameter_value(spec, p) or spec.example(p) or "" for p in form_params
        }
        body_required = {p["name"] for p in form_params if p.get("required")}

    # Input: an entity-like parameter or body field, then a required path parameter, then
    # any required parameter, then any parameter at all.
    candidates: list[tuple[str, str, Any]] = [
        (p["in"], p["name"], _parameter_value(spec, p)) for p in params
    ]
    if body_example:
        candidates += [("body", name, value) for name, value in body_example.items()]

    def entity_of(candidate: tuple[str, str, Any]) -> str:
        sample = candidate[2] if isinstance(candidate[2], str) else None
        return infer_input_entity(candidate[1], sample)[0]

    required = {(p["in"], p["name"]) for p in params if p.get("required")}
    chosen = next((c for c in candidates if entity_of(c) != "Phrase"), None)
    chosen = chosen or next((c for c in candidates if c[0] == "path"), None)
    chosen = chosen or next((c for c in candidates if (c[0], c[1]) in required), None)
    chosen = chosen or (candidates[0] if candidates else None)
    if chosen is None:
        return None, "it takes no parameter to carry the input."
    location, input_name, _ = chosen

    endpoint_path = path_str
    query: dict[str, Any] = {}
    for param in params:
        name, where = param["name"], param["in"]
        if (where, name) == (location, input_name):
            if where == "path":
                endpoint_path = endpoint_path.replace("{" + name + "}", "{target}")
            else:
                query[name] = ""
            continue
        value = _parameter_value(spec, param)
        if where == "path":
            if value is None:
                return None, f"path parameter {{{name}}} has no default to fill it with."
            endpoint_path = endpoint_path.replace("{" + name + "}", str(value))
        elif param.get("required"):
            if value is None:
                return None, f"required query parameter {name!r} has no default."
            query[name] = value

    body: dict[str, Any] | None = None
    if body_example is not None:
        body = {
            name: value
            for name, value in body_example.items()
            if name == input_name or name in body_required
        }

    responses = operation.get("responses") or {}
    ok_key = next((code for code in ("200", "201") if code in responses), None)
    ok_key = ok_key or next((str(c) for c in responses if str(c).startswith("2")), None)
    ok_response = spec.resolve(responses.get(ok_key, {})) if ok_key else {}
    json_entry = _json_content(ok_response) if isinstance(ok_response, dict) else {}
    sample = json_entry.get("example")
    if sample is None:
        response_schema = json_entry.get("schema") or ok_response.get("schema") or {}
        sample = spec.example(response_schema)
    outputs, response_is_list = _outputs_from_sample(sample)

    summary = str(operation.get("summary") or transform_id.replace("_", " ").title())
    return (
        ScaffoldTransformConfig(
            transform_id=transform_id,
            display_name=_display_name(title, summary),
            input_entity=entity_of(chosen),
            endpoint_path=endpoint_path,
            input_param_name=input_name if location != "path" else "target",
            input_location=location,
            http_method=http_method.upper(),
            output_mappings=outputs or [_FALLBACK_OUTPUT],
            description=str(operation.get("description") or summary),
            query_params=query,
            request_body=body,
            body_encoding=body_encoding,
            response_is_list=response_is_list,
        ),
        "",
    )
