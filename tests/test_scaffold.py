"""Tests for transform scaffolding engine."""

from __future__ import annotations

import json
from pathlib import Path

from transformatron import operations
from transformatron.config import TransformatronConfig
from transformatron.scaffold import (
    ScaffoldServiceConfig,
    ScaffoldTransformConfig,
    generate_service_code,
    inject_project_import,
    parse_curl_command,
    parse_openapi_spec,
    write_scaffold,
)
from transformatron.scaffold.schema import (
    OutputFieldMapping,
    infer_input_entity,
    infer_output_entity,
)


def test_infer_input_entity() -> None:
    assert infer_input_entity("ip")[0] == "IPv4Address"
    assert infer_input_entity("ip_address")[0] == "IPv4Address"
    assert infer_input_entity("target", "8.8.8.8")[0] == "IPv4Address"
    assert infer_input_entity("domain")[0] == "Domain"
    assert infer_input_entity("target", "example.com")[0] == "Domain"
    assert infer_input_entity("url")[0] == "URL"
    assert infer_input_entity("email")[0] == "EmailAddress"
    assert infer_input_entity("hash")[0] == "Hash"
    assert infer_input_entity("cve")[0] == "CVE"
    assert infer_input_entity("custom_param")[0] == "Phrase"


def test_infer_output_entity() -> None:
    asn_mapping = infer_output_entity("asn")
    assert asn_mapping.entity_type == "AS"
    assert asn_mapping.strip_prefix == "AS"

    country_mapping = infer_output_entity("country_code")
    assert country_mapping.entity_type == "Country"

    isp_mapping = infer_output_entity("as_name")
    assert isp_mapping.entity_type == "ISP"

    city_mapping = infer_output_entity("city")
    assert city_mapping.entity_type == "City"

    cve_mapping = infer_output_entity("cves", sample_val=["CVE-2024-1234"])
    assert cve_mapping.entity_type == "CVE"
    assert cve_mapping.is_list is True

    phrase_mapping = infer_output_entity("reputation_score")
    assert phrase_mapping.entity_type == "Phrase"
    assert phrase_mapping.label_prefix == "Reputation Score: "


def test_parse_curl_command_with_path_and_header() -> None:
    curl = 'curl -H "X-API-KEY: secret123" https://api.greynoise.io/v3/community/8.8.8.8'
    sample = {
        "ip": "8.8.8.8",
        "noise": True,
        "classification": "benign",
        "link": "https://viz.greynoise.io/ip/8.8.8.8",
    }

    cfg = parse_curl_command(curl, sample_response=sample)

    assert cfg.service_id == "greynoise"
    assert cfg.base_url == "https://api.greynoise.io"
    assert cfg.auth_header_name == "X-API-KEY"
    assert cfg.auth_key_name == "GREYNOISE_API_KEY"
    assert len(cfg.transforms) == 1

    t = cfg.transforms[0]
    assert t.input_entity == "IPv4Address"
    assert "{target}" in t.endpoint_path

    out_types = t.output_entity_types
    assert "Phrase" in out_types
    assert "URL" in out_types


def test_parse_curl_command_with_bearer_token_and_query_param() -> None:
    curl = 'curl -H "Authorization: Bearer token_xyz" "https://api.example.com/v1/lookup?domain=example.com"'
    cfg = parse_curl_command(curl, service_name="example_svc")

    assert cfg.service_id == "example_svc"
    assert cfg.auth_type == "bearer"
    assert cfg.transforms[0].input_entity == "Domain"
    assert cfg.transforms[0].input_param_name == "domain"


def test_parse_openapi_spec() -> None:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Threat API", "version": "1.0"},
        "servers": [{"url": "https://api.threatservice.com/v1"}],
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Threat-Key"}
            }
        },
        "paths": {
            "/ip/{ip}": {
                "get": {
                    "summary": "Check IP Reputation",
                    "operationId": "check_ip",
                    "parameters": [{"name": "ip", "in": "path", "required": True}],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "asn": {"type": "string"},
                                            "country": {"type": "string"},
                                            "score": {"type": "integer"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }

    cfg = parse_openapi_spec(spec)

    assert cfg.service_id == "threat_api"
    assert cfg.base_url == "https://api.threatservice.com/v1"
    assert cfg.auth_header_name == "X-Threat-Key"
    assert len(cfg.transforms) == 1

    t = cfg.transforms[0]
    assert t.transform_id == "check_ip"
    assert t.input_entity == "IPv4Address"
    assert set(t.output_entity_types) == {"AS", "Country", "Phrase"}


def test_generate_service_code() -> None:
    cfg = ScaffoldServiceConfig(
        service_id="test_service",
        display_name="Test Service",
        base_url="https://api.test.com",
        auth_key_name="TEST_SERVICE_API_KEY",
        transforms=[
            ScaffoldTransformConfig(
                transform_id="test_lookup",
                display_name="Test Service: Lookup",
                input_entity="IPv4Address",
                endpoint_path="/lookup/{target}",
                output_mappings=[
                    OutputFieldMapping(field_name="asn", entity_type="AS", strip_prefix="AS"),
                    OutputFieldMapping(
                        field_name="summary", entity_type="Phrase", label_prefix="Summary: "
                    ),
                ],
            )
        ],
    )

    files = generate_service_code(cfg)

    assert "api.py" in files
    assert "lookup.py" in files

    api_code = files["api.py"]
    assert 'BASE_URL = "https://api.test.com"' in api_code
    assert 'API_KEY = "TEST_SERVICE_API_KEY"' in api_code
    assert "def validate_ip" in api_code
    assert "async def fetch" in api_code

    lookup_code = files["lookup.py"]
    assert "from maltego.entities import AS, IPv4Address, Phrase" in lookup_code
    assert "-> list[AS | Phrase]:" in lookup_code
    assert "target = validate_ip(input_entity.value)" in lookup_code
    assert "results.append(AS(" in lookup_code


def test_inject_project_import(tmp_path: Path) -> None:
    project_py = tmp_path / "project.py"
    project_py.write_text("""from maltego.server import run_server

from transforms.examples.ffraud import *  # noqa: F401,F403

if __name__ == "__main__":
    run_server()
""")

    import_stmt = "from transforms.new_svc.lookup import *  # noqa: F401,F403"
    injected = inject_project_import(project_py, import_stmt)
    assert injected is True

    content = project_py.read_text()
    assert import_stmt in content
    # Verify placed before if __name__
    import_pos = content.index(import_stmt)
    main_pos = content.index('if __name__ == "__main__":')
    assert import_pos < main_pos

    # Test idempotency
    assert inject_project_import(project_py, import_stmt) is False


def test_write_scaffold(tmp_path: Path) -> None:
    project_dir = tmp_path / "server"
    project_dir.mkdir()
    project_py = project_dir / "project.py"
    project_py.write_text("from transforms.examples.ffraud import *  # noqa: F401,F403\n")

    cfg = ScaffoldServiceConfig(
        service_id="custom_api",
        display_name="Custom API",
        base_url="https://api.custom.com",
        transforms=[
            ScaffoldTransformConfig(
                transform_id="custom_lookup",
                display_name="Custom API: Lookup",
                input_entity="Domain",
                endpoint_path="/check",
                output_mappings=[OutputFieldMapping(field_name="status", entity_type="Phrase")],
            )
        ],
    )

    created = write_scaffold(cfg, project_dir)

    assert len(created) == 3  # __init__.py, api.py, lookup.py
    assert (project_dir / "transforms" / "custom_api" / "api.py").exists()
    assert (project_dir / "transforms" / "custom_api" / "lookup.py").exists()
    assert "from transforms.custom_api.lookup import *" in project_py.read_text()


def test_operations_scaffold_validation(tmp_path: Path) -> None:
    config = TransformatronConfig(project_dir=tmp_path / "server", state_dir=tmp_path / "state")
    config.project_dir.mkdir(parents=True)
    (config.project_dir / "project.py").write_text("")

    res_missing = operations.scaffold(config)
    assert "Failed to scaffold" in res_missing

    curl = "curl https://api.threat.com/v1/domain/example.com"
    res_success = operations.scaffold(
        config,
        service="threat",
        curl=curl,
        sample_response=json.dumps({"risk": "high"}),
    )
    assert "Scaffolded 'Threat'" in res_success
    assert (config.project_dir / "transforms" / "threat" / "api.py").exists()
