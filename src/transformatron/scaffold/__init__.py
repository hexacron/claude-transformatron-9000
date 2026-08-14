"""Spec-to-Transform Scaffolder package."""

from __future__ import annotations

from transformatron.scaffold.generator import (
    generate_service_code,
    inject_project_import,
    write_scaffold,
)
from transformatron.scaffold.parser import parse_curl_command, parse_openapi_spec
from transformatron.scaffold.schema import (
    OutputFieldMapping,
    ScaffoldServiceConfig,
    ScaffoldTransformConfig,
)

__all__ = [
    "OutputFieldMapping",
    "ScaffoldServiceConfig",
    "ScaffoldTransformConfig",
    "generate_service_code",
    "inject_project_import",
    "parse_curl_command",
    "parse_openapi_spec",
    "write_scaffold",
]
