"""Tests for transform scaffolding engine."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

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
from transformatron.scaffold.generator import generate_api_module, generate_transform_module
from transformatron.scaffold.schema import (
    OutputFieldMapping,
    infer_input_entity,
    infer_output_entity,
    qualified_entity_type,
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
    assert "TestLookupOutput = AS | Phrase" in lookup_code
    assert "-> list[TestLookupOutput]:" in lookup_code
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


def _service_config(service_id: str = "custom_api") -> ScaffoldServiceConfig:
    """Return a minimal single-transform service config for write tests."""
    return ScaffoldServiceConfig(
        service_id=service_id,
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


def test_write_scaffold_refuses_to_overwrite(tmp_path: Path) -> None:
    """A second scaffold of the same service must not clobber the first."""
    project_dir = tmp_path / "server"
    project_dir.mkdir()
    cfg = _service_config()

    write_scaffold(cfg, project_dir)
    hand_edited = project_dir / "transforms" / "custom_api" / "api.py"
    hand_edited.write_text("# hand-written behaviour a generator cannot recover\n")

    with pytest.raises(FileExistsError, match="already exists"):
        write_scaffold(cfg, project_dir)

    assert hand_edited.read_text().startswith("# hand-written")


def test_write_scaffold_force_overwrites(tmp_path: Path) -> None:
    """`force=True` is the deliberate escape hatch from the overwrite guard."""
    project_dir = tmp_path / "server"
    project_dir.mkdir()
    cfg = _service_config()

    write_scaffold(cfg, project_dir)
    api_py = project_dir / "transforms" / "custom_api" / "api.py"
    api_py.write_text("# stale\n")

    write_scaffold(cfg, project_dir, force=True)

    assert "IntegrationClient" in api_py.read_text()


def test_write_scaffold_leaves_no_partial_write_on_clash(tmp_path: Path) -> None:
    """A collision on any file aborts before the others are touched."""
    project_dir = tmp_path / "server"
    project_dir.mkdir()
    cfg = _service_config()
    svc_dir = project_dir / "transforms" / "custom_api"
    svc_dir.mkdir(parents=True)
    (svc_dir / "lookup.py").write_text("# only this one exists\n")

    with pytest.raises(FileExistsError):
        write_scaffold(cfg, project_dir)

    assert not (svc_dir / "api.py").exists()
    assert (svc_dir / "lookup.py").read_text() == "# only this one exists\n"


def test_generated_code_has_no_unused_imports() -> None:
    """Generated modules must not import what they do not use (ruff F401).

    `server/transforms/` is linted, so a scaffold that emits unused imports hands the
    author a failing gate on untouched, freshly generated code.
    """
    # No auth key: neither `os` nor TransformSetting is referenced by the output.
    cfg = _service_config()
    cfg.auth_key_name = None
    api_code = generate_api_module(cfg)

    assert "import os" not in api_code
    assert "TransformSetting" not in api_code
    # A Domain validator uses `re`; nothing here uses `ipaddress`.
    assert "import ipaddress" not in api_code

    # MAX_ITEMS is only imported when a list mapping actually caps a slice.
    transform_code = generate_transform_module(cfg, cfg.transforms[0])
    assert "MAX_ITEMS" not in transform_code


def test_multiple_validators_are_blank_line_separated() -> None:
    """Two input types emit two validators, which need two blank lines between them.

    Single-validator services hid this: the separator only had to hold before `fetch`.
    """
    cfg = ScaffoldServiceConfig(
        service_id="multi",
        display_name="Multi",
        base_url="https://api.multi.com",
        auth_key_name="MULTI_API_KEY",
        transforms=[
            ScaffoldTransformConfig(
                transform_id="by_ip",
                display_name="Multi: By IP",
                input_entity="IPv4Address",
                endpoint_path="/ip",
            ),
            ScaffoldTransformConfig(
                transform_id="by_domain",
                display_name="Multi: By Domain",
                input_entity="Domain",
                endpoint_path="/domain",
            ),
        ],
    )

    code = generate_api_module(cfg)

    assert "\n\n\ndef validate_domain" in code
    assert "\n\n\nasync def fetch" in code


def test_openapi_search_param_falls_back_to_real_name() -> None:
    """An endpoint with no entity-like parameter still uses its real query param name.

    Leaving the "input_val" placeholder builds a query string the upstream rejects, and
    it only fails at runtime against the live API.
    """
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Searchy", "version": "1.0.0"},
        "servers": [{"url": "https://searchy.example"}],
        "paths": {
            "/api/v1/search": {
                "get": {
                    "operationId": "searchdatasource",
                    "summary": "Search",
                    "parameters": [
                        {"name": "q", "in": "query", "required": True},
                        {"name": "size", "in": "query"},
                    ],
                    "responses": {"200": {"content": {"application/json": {"schema": {}}}}},
                }
            }
        },
    }

    cfg = parse_openapi_spec(spec, service_name="searchy")
    transform = cfg.transforms[0]

    assert transform.input_param_name == "q"
    assert transform.input_entity == "Phrase"
    assert "input_val" not in generate_transform_module(cfg, transform)


def test_openapi_path_parameter_is_not_turned_into_a_query_param() -> None:
    """A templated path keeps interpolating {target} rather than gaining a query string."""
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Pathy", "version": "1.0.0"},
        "servers": [{"url": "https://pathy.example"}],
        "paths": {
            "/api/v1/hostname/{hostname}": {
                "get": {
                    "operationId": "gethostname",
                    "summary": "Hostname",
                    "parameters": [{"name": "hostname", "in": "path", "required": True}],
                    "responses": {"200": {"content": {"application/json": {"schema": {}}}}},
                }
            }
        },
    }

    cfg = parse_openapi_spec(spec, service_name="pathy")
    code = generate_transform_module(cfg, cfg.transforms[0])

    assert 'path = f"/api/v1/hostname/{target}"' in code
    assert "params = None" in code


def test_generated_entity_imports_are_isort_ordered() -> None:
    """Acronym entities sort before CamelCase ones, matching ruff's isort rule."""
    cfg = _service_config()
    cfg.transforms[0].output_mappings = [
        OutputFieldMapping(field_name="status", entity_type="Phrase"),
        OutputFieldMapping(field_name="link", entity_type="URL"),
        OutputFieldMapping(field_name="ip", entity_type="IPv4Address"),
    ]

    code = generate_transform_module(cfg, cfg.transforms[0])

    assert "from maltego.entities import URL, Domain, IPv4Address, Phrase" in code


@pytest.mark.parametrize(
    "input_entity",
    ["IPv4Address", "IPv6Address", "Domain", "URL", "EmailAddress", "Hash", "CVE", "Phrase"],
)
def test_input_constraint_matches_the_input_entity(input_entity: str) -> None:
    """Every scaffolded transform constrains itself to its own input type.

    Without the constraint the Maltego client offers the transform on every entity of the
    type, including ones the endpoint cannot serve.
    """
    cfg = _service_config()
    cfg.transforms[0].input_entity = input_entity

    code = generate_transform_module(cfg, cfg.transforms[0])

    assert f'input_constraint=EntityTypeConstraint(entity_type="maltego.{input_entity}"),' in code
    assert "from maltego.model.input_constraints import EntityTypeConstraint" in code


def test_input_constraint_can_be_suppressed() -> None:
    """Opting out emits neither the argument nor its import.

    A leftover import would be an F401 failure on freshly generated code.
    """
    cfg = _service_config()
    cfg.transforms[0].emit_input_constraint = False

    code = generate_transform_module(cfg, cfg.transforms[0])

    assert "input_constraint" not in code
    assert "EntityTypeConstraint" not in code


def test_generated_code_passes_the_project_lint_gate(tmp_path: Path) -> None:
    """Generated modules must survive `ruff check` and `ruff format --check`.

    `server/transforms/` is linted, so a scaffold that emits misordered imports or wrong
    blank-line spacing hands the author a failing gate on code they have not touched. The
    import ordering is the live risk: EntityTypeConstraint sits between the entities and
    server imports, and the entity list itself has to match ruff's case-insensitive sort
    (Website before WHOISRecord), which is easy to get wrong by hand.
    """
    wide = _service_config(service_id="wide")
    wide.transforms[0].output_mappings = [
        infer_output_entity(name)
        for name in ("whois", "website", "certificate", "bitcoin", "mac_address", "name", "link")
    ]

    for cfg in (_service_config(), _service_config(service_id="authed"), wide):
        if cfg.service_id == "authed":
            cfg.auth_key_name = "AUTHED_API_KEY"
        for name, code in generate_service_code(cfg).items():
            (tmp_path / f"{cfg.service_id}_{name}").write_text(code)

    for args in (["check"], ["format", "--check"]):
        result = subprocess.run(
            ["uv", "run", "ruff", *args, str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 0, f"ruff {args[0]} failed:\n{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize(
    ("class_name", "expected"),
    [
        ("Domain", "maltego.Domain"),
        ("IPv4Address", "maltego.IPv4Address"),
        # The "maltego." + ClassName rule breaks for 72 of the 244 exported entities.
        ("AffiliationTwitter", "maltego.affiliation.Twitter"),
        ("STIX2attackpattern", "maltego.STIX2.attack-pattern"),
        ("Hashtag", "maltego.hashtag"),
    ],
)
def test_qualified_entity_type_reads_the_class(class_name: str, expected: str) -> None:
    """TYPE_NAME comes off the class, because it is not always derivable from the name."""
    assert qualified_entity_type(class_name) == expected


def test_qualified_entity_type_rejects_unknown_names() -> None:
    """A name that is not an entity class fails loudly rather than emitting a bad type."""
    with pytest.raises(ValueError, match="not an entity class"):
        qualified_entity_type("NotAnEntity")


@pytest.mark.parametrize(
    ("field_name", "entity_type"),
    [
        ("name", "Person"),
        ("company", "Company"),
        ("phone", "PhoneNumber"),
        ("username", "Alias"),
        ("avatar", "Image"),
        ("mac_address", "MacAddress"),
        ("port", "Port"),
        ("bitcoin", "BTCAddress"),
        ("wallet", "CryptocurrencyAddress"),
        ("website", "Website"),
        ("malware", "Malware"),
        ("certificate", "X509Certificate"),
        ("whois", "WHOISRecord"),
    ],
)
def test_widened_output_entities(field_name: str, entity_type: str) -> None:
    """Common response fields map to pivotable entities rather than falling back to Phrase.

    A Phrase renders as text an investigator cannot pivot from, so every field left
    unmapped is a dead end in the graph.
    """
    assert infer_output_entity(field_name).entity_type == entity_type


def test_org_still_maps_to_isp() -> None:
    """`org` stays with ISP: on IP-lookup APIs it is the network operator, not a company."""
    assert infer_output_entity("org").entity_type == "ISP"


def test_many_output_entities_stay_within_the_line_limit() -> None:
    """A response mapping many entity types must not emit over-long imports or unions.

    Both the entity import and the output union grow with the number of mapped fields, and
    either one crossing 100 characters fails E501 on freshly generated code.
    """
    cfg = _service_config()
    cfg.transforms[0].output_mappings = [
        infer_output_entity(name)
        for name in (
            "name",
            "company",
            "phone",
            "username",
            "avatar",
            "mac_address",
            "port",
            "bitcoin",
            "wallet",
            "website",
            "malware",
            "certificate",
            "whois",
        )
    ]

    code = generate_transform_module(cfg, cfg.transforms[0])

    assert all(len(line) <= 100 for line in code.splitlines())
    # The union is named once rather than inlined at both use sites.
    assert "CustomLookupOutput = (" in code
    assert "-> list[CustomLookupOutput]:" in code


@pytest.mark.parametrize("count", range(1, 15))
def test_output_alias_matches_ruff_at_every_width(count: int, tmp_path: Path) -> None:
    """The alias must be formatted the way ruff would at any number of output types.

    Wrapping earlier or later than the formatter is not cosmetic: ruff rewrites the file
    and `ruff format --check` then fails on code the author has not touched. The boundary
    between the three layouts is the part worth pinning.
    """
    pool = [
        "URL",
        "Alias",
        "BTCAddress",
        "City",
        "Company",
        "CryptocurrencyAddress",
        "Image",
        "MacAddress",
        "Malware",
        "Person",
        "PhoneNumber",
        "Port",
        "WHOISRecord",
        "X509Certificate",
    ]
    cfg = _service_config(service_id=f"w{count}")
    cfg.transforms[0].output_mappings = [
        OutputFieldMapping(field_name=f"f{i}", entity_type=t) for i, t in enumerate(pool[:count])
    ]

    for name, code in generate_service_code(cfg).items():
        (tmp_path / name).write_text(code)

    result = subprocess.run(
        ["uv", "run", "ruff", "format", "--check", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    assert result.returncode == 0, f"{count} output types:\n{result.stdout}"


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


def _query_auth_spec() -> dict[str, object]:
    """Return a spec whose API key travels in the query string, not a header."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Queryauth", "version": "1.0.0"},
        "servers": [{"url": "https://qa.example"}],
        "components": {
            "securitySchemes": {"apiKey": {"type": "apiKey", "in": "query", "name": "apikey"}}
        },
        "paths": {
            "/lookup": {
                "get": {
                    "operationId": "lookup",
                    "summary": "Lookup",
                    "parameters": [{"name": "ip", "in": "query", "required": True}],
                    "responses": {"200": {"content": {"application/json": {"schema": {}}}}},
                }
            }
        },
    }


def test_query_auth_scheme_is_recorded_by_the_parser() -> None:
    """An `in: query` apiKey scheme records the parameter name it must be sent under."""
    cfg = parse_openapi_spec(_query_auth_spec(), service_name="qa")

    assert cfg.auth_type == "query"
    assert cfg.auth_query_param == "apikey"


def test_query_auth_key_is_actually_sent() -> None:
    """A query-auth client sends the key.

    The generated fetch() refused to run without a key and then never attached it, so
    every request went out unauthenticated and failed only against the live API.
    """
    cfg = parse_openapi_spec(_query_auth_spec(), service_name="qa")
    code = generate_api_module(cfg)

    assert 'params["apikey"] = api_key' in code
    # params may arrive as None from a templated path, so it is copied before mutation.
    assert "params = dict(params or {})" in code


def test_colliding_transform_ids_are_all_generated() -> None:
    """Operations that slugify to the same id each get a module.

    "do scan" and "do-scan" both slugify to do_scan. Keying the output dict on that
    dropped every colliding transform but the last, and the duplicate function name
    would have shadowed the survivor inside the module anyway.
    """
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Dup", "version": "1.0.0"},
        "servers": [{"url": "https://dup.example"}],
        "paths": {
            "/a/scan": {
                "get": {
                    "operationId": "do scan",
                    "parameters": [{"name": "q", "in": "query", "required": True}],
                    "responses": {},
                }
            },
            "/b/scan": {
                "get": {
                    "operationId": "do-scan",
                    "parameters": [{"name": "q", "in": "query", "required": True}],
                    "responses": {},
                }
            },
        },
    }

    cfg = parse_openapi_spec(spec, service_name="dup")
    files = generate_service_code(cfg)

    module_files = sorted(n for n in files if n not in ("__init__.py", "api.py"))
    assert module_files == ["do_scan.py", "do_scan_2.py"]
    assert "async def do_scan(" in files["do_scan.py"]
    assert "async def do_scan_2(" in files["do_scan_2.py"]


def test_generate_service_code_does_not_mutate_config() -> None:
    """Deduplicating filenames must not rewrite the caller's transform ids."""
    cfg = parse_openapi_spec(
        {
            "openapi": "3.0.0",
            "info": {"title": "Dup2", "version": "1.0.0"},
            "servers": [{"url": "https://dup2.example"}],
            "paths": {
                "/a": {"get": {"operationId": "x y", "parameters": [], "responses": {}}},
                "/b": {"get": {"operationId": "x-y", "parameters": [], "responses": {}}},
            },
        },
        service_name="dup2",
    )

    generate_service_code(cfg)

    assert [t.transform_id for t in cfg.transforms] == ["x_y", "x_y"]


def test_scaffold_reports_existing_service_instead_of_overwriting(tmp_path: Path) -> None:
    """The FileExistsError guard reaches the caller as a message, not a traceback."""
    config = TransformatronConfig(project_dir=tmp_path / "server", state_dir=tmp_path / "state")
    config.project_dir.mkdir(parents=True)
    (config.project_dir / "project.py").write_text("")

    curl = "curl https://api.threat.com/v1/domain/example.com"
    first = operations.scaffold(config, service="threat", curl=curl)
    assert "Scaffolded" in first

    second = operations.scaffold(config, service="threat", curl=curl)
    assert "Failed to scaffold" in second
    assert "already exists" in second


def test_scaffold_reports_malformed_openapi_spec(tmp_path: Path) -> None:
    """A spec that is not JSON is a caller error, reported rather than raised."""
    config = TransformatronConfig(project_dir=tmp_path / "server", state_dir=tmp_path / "state")
    config.project_dir.mkdir(parents=True)

    result = operations.scaffold(config, service="broken", openapi="{not json at all")

    assert "Failed to scaffold" in result
