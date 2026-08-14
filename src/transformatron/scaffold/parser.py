"""Parsers for converting cURL commands, OpenAPI specs, and dicts into config."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any
from urllib.parse import parse_qs, urlparse

from transformatron.scaffold.schema import (
    OutputFieldMapping,
    ScaffoldServiceConfig,
    ScaffoldTransformConfig,
    infer_input_entity,
    infer_output_entity,
)


def _slugify(text: str) -> str:
    """Convert text into a clean snake_case slug."""
    text = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"[-\s]+", "_", text)


def parse_curl_command(
    curl_cmd: str,
    sample_response: dict[str, Any] | str | None = None,
    service_name: str | None = None,
) -> ScaffoldServiceConfig:
    """Parse a cURL command and optional sample JSON response into a service config."""
    cleaned_cmd = re.sub(r"\\\s*\n", " ", curl_cmd.strip())
    tokens = shlex.split(cleaned_cmd)

    method = "GET"
    url = ""
    headers: dict[str, str] = {}

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("-X", "--request") and i + 1 < len(tokens):
            method = tokens[i + 1].upper()
            i += 2
            continue
        if token in ("-H", "--header") and i + 1 < len(tokens):
            header_line = tokens[i + 1]
            key, sep, val = header_line.partition(":")
            if sep:
                headers[key.strip()] = val.strip()
            i += 2
            continue
        if token in ("-d", "--data", "--data-raw") and i + 1 < len(tokens):
            if method == "GET":
                method = "POST"
            i += 2
            continue
        if not token.startswith("-") and ("://" in token or token.startswith("http")):
            url = token
        i += 1

    if not url:
        raise ValueError("Could not find a valid URL in the cURL command.")

    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    # Determine service name
    default_svc = parsed_url.netloc.split(".")[0] if parsed_url.netloc else "service"
    svc_id = service_name or _slugify(default_svc)
    if svc_id in ("api", "v1", "v2", "v3", "www") and parsed_url.netloc:
        parts = parsed_url.netloc.split(".")
        if len(parts) >= 2:
            svc_id = _slugify(parts[-2])

    svc_title = svc_id.replace("_", " ").title()

    # Detect Auth
    auth_key_name = f"{svc_id.upper()}_API_KEY"
    auth_header_name = "X-API-KEY"
    auth_type = "header"
    auth_query_param = None

    for h_name, h_val in headers.items():
        h_upper = h_name.upper()
        if "AUTH" in h_upper or "API-KEY" in h_upper or "KEY" in h_upper or "TOKEN" in h_upper:
            auth_header_name = h_name
            auth_type = "bearer" if h_val.lower().startswith("bearer") else "header"
            break

    # Analyze Path and Query params for input entity
    path = parsed_url.path or "/"
    query_params = parse_qs(parsed_url.query)

    path_segments = [s for s in path.split("/") if s]
    input_entity = "IPv4Address"
    input_param_name = "input_val"
    parameterized_path = path

    # Check query params first
    matched_query = False
    for q_name, q_vals in query_params.items():
        q_val = q_vals[0] if q_vals else ""
        inferred_entity, _ = infer_input_entity(q_name, q_val)
        if inferred_entity != "Phrase" or len(query_params) == 1:
            input_entity = inferred_entity
            input_param_name = q_name
            matched_query = True
            break

    # Check path segments if not matched in query
    if not matched_query and path_segments:
        last_segment = path_segments[-1]
        inferred_entity, _ = infer_input_entity(last_segment, last_segment)
        if inferred_entity != "Phrase" or len(path_segments) > 1:
            input_entity = inferred_entity
            input_param_name = "target"
            parameterized_path = "/" + "/".join([*path_segments[:-1], "{target}"])

    # Parse sample response for output mappings
    output_mappings: list[OutputFieldMapping] = []
    if isinstance(sample_response, str):
        try:
            sample_response = json.loads(sample_response)
        except json.JSONDecodeError:
            sample_response = None

    if isinstance(sample_response, dict):
        for k, v in sample_response.items():
            if k.lower() in ("status", "ok", "success", "error", "message", "code"):
                continue
            output_mappings.append(infer_output_entity(k, v))
    elif (
        isinstance(sample_response, list)
        and sample_response
        and isinstance(sample_response[0], dict)
    ):
        for k, v in sample_response[0].items():
            output_mappings.append(infer_output_entity(k, v))

    if not output_mappings:
        output_mappings = [
            OutputFieldMapping(field_name="result", entity_type="Phrase", label_prefix="Result: ")
        ]

    transform_id = f"{svc_id}_lookup"
    display_name = f"{svc_title}: Lookup"

    transform = ScaffoldTransformConfig(
        transform_id=transform_id,
        display_name=display_name,
        input_entity=input_entity,
        endpoint_path=parameterized_path,
        input_param_name=input_param_name,
        http_method=method,
        output_mappings=output_mappings,
        description=f"Lookup {input_entity} against {svc_title}.",
    )

    return ScaffoldServiceConfig(
        service_id=svc_id,
        display_name=svc_title,
        base_url=base_url,
        auth_key_name=auth_key_name,
        auth_header_name=auth_header_name,
        auth_query_param=auth_query_param,
        auth_type=auth_type,
        transforms=[transform],
    )


def parse_openapi_spec(
    spec: dict[str, Any] | str,
    service_name: str | None = None,
) -> ScaffoldServiceConfig:
    """Parse an OpenAPI (v3) or Swagger (v2) specification into a service config."""
    if isinstance(spec, str):
        spec = json.loads(spec)

    info = spec.get("info", {})
    title = info.get("title") or "API Service"
    svc_id = service_name or _slugify(title)
    svc_title = title

    # Determine Base URL
    base_url = "https://api.example.com"
    if spec.get("servers"):
        base_url = spec["servers"][0].get("url", base_url)
    elif "host" in spec:
        schemes = spec.get("schemes", ["https"])
        base_url = f"{schemes[0]}://{spec['host']}{spec.get('basePath', '')}".rstrip("/")

    # Detect Auth
    auth_key_name = f"{svc_id.upper()}_API_KEY"
    auth_header_name = "X-API-KEY"
    auth_type = "header"

    components = spec.get("components", {}) or {}
    sec_defs = spec.get("securityDefinitions", {}) or {}
    security_schemes = components.get("securitySchemes", {}) or sec_defs
    for _s_name, s_def in security_schemes.items():
        if isinstance(s_def, dict):
            sec_type = s_def.get("type", "").lower()
            if sec_type == "http" and s_def.get("scheme", "").lower() == "bearer":
                auth_type = "bearer"
                auth_header_name = "Authorization"
            elif sec_type == "apikey":
                auth_type = "header" if s_def.get("in") == "header" else "query"
                auth_header_name = s_def.get("name", "X-API-KEY")
            break

    transforms: list[ScaffoldTransformConfig] = []
    paths = spec.get("paths", {})

    for path_str, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for http_method in ("get", "post"):
            if http_method not in path_item:
                continue
            operation = path_item[http_method]
            if not isinstance(operation, dict):
                continue

            op_id = operation.get("operationId") or f"{http_method}_{path_str.replace('/', '_')}"
            transform_id = _slugify(op_id)
            summary = operation.get("summary") or op_id.replace("_", " ").title()
            display_name = f"{svc_title}: {summary}"

            # Identify input entity from parameters
            input_entity = "IPv4Address"
            input_param_name = "input_val"
            params = operation.get("parameters", [])
            for p in params:
                if isinstance(p, dict):
                    p_name = p.get("name", "")
                    inferred_entity, _ = infer_input_entity(p_name)
                    if inferred_entity != "Phrase":
                        input_entity = inferred_entity
                        input_param_name = p_name
                        break

            # Identify outputs from 200 response schema
            output_mappings: list[OutputFieldMapping] = []
            responses = operation.get("responses", {})
            ok_resp = responses.get("200") or responses.get("201") or {}
            content = ok_resp.get("content", {}).get("application/json", {})
            schema = content.get("schema") or ok_resp.get("schema") or {}
            properties = schema.get("properties", {})

            if isinstance(properties, dict):
                for prop_name, _prop_spec in properties.items():
                    if prop_name.lower() in ("status", "ok", "success", "error", "message"):
                        continue
                    output_mappings.append(infer_output_entity(prop_name))

            if not output_mappings:
                output_mappings = [
                    OutputFieldMapping(
                        field_name="result", entity_type="Phrase", label_prefix="Result: "
                    )
                ]

            transforms.append(
                ScaffoldTransformConfig(
                    transform_id=transform_id,
                    display_name=display_name,
                    input_entity=input_entity,
                    endpoint_path=path_str,
                    input_param_name=input_param_name,
                    http_method=http_method.upper(),
                    output_mappings=output_mappings,
                    description=operation.get("description") or summary,
                )
            )

    return ScaffoldServiceConfig(
        service_id=svc_id,
        display_name=svc_title,
        base_url=base_url,
        auth_key_name=auth_key_name,
        auth_header_name=auth_header_name,
        auth_type=auth_type,
        transforms=transforms,
    )
