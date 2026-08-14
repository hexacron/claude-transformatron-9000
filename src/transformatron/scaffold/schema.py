"""Schema models and entity mapping heuristics for transform scaffolding."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OutputFieldMapping:
    """Mapping from a response field to a Maltego entity."""

    field_name: str
    entity_type: str
    label_prefix: str = ""
    strip_prefix: str = ""
    is_list: bool = False


@dataclass
class ScaffoldTransformConfig:
    """Configuration for generating a single Maltego transform function."""

    transform_id: str
    display_name: str
    input_entity: str
    endpoint_path: str
    input_param_name: str = "input_val"
    http_method: str = "GET"
    output_mappings: list[OutputFieldMapping] = field(default_factory=list)
    description: str = ""

    @property
    def output_entity_types(self) -> list[str]:
        """Return unique output entity types declared in output mappings."""
        types: list[str] = []
        for m in self.output_mappings:
            if m.entity_type not in types:
                types.append(m.entity_type)
        return types or ["Phrase"]


@dataclass
class ScaffoldServiceConfig:
    """Configuration for an entire service integration (module package)."""

    service_id: str
    display_name: str
    base_url: str
    auth_key_name: str | None = None
    auth_header_name: str | None = "X-API-KEY"
    auth_query_param: str | None = None
    auth_type: str = "header"  # "header", "bearer", "query", "none"
    transforms: list[ScaffoldTransformConfig] = field(default_factory=list)


# Input entity heuristic patterns
_IP_PARAM_RE = re.compile(r"^(ip|ipv4|ip_address|address|ipaddress|host_ip)$", re.I)
_IPV6_PARAM_RE = re.compile(r"^(ipv6|ip6|ipv6_address)$", re.I)
_DOMAIN_PARAM_RE = re.compile(r"^(domain|hostname|host|fqdn|site)$", re.I)
_URL_PARAM_RE = re.compile(r"^(url|link|target_url|uri)$", re.I)
_EMAIL_PARAM_RE = re.compile(r"^(email|mail|email_address)$", re.I)
_HASH_PARAM_RE = re.compile(r"^(hash|md5|sha1|sha256|sha512|checksum)$", re.I)
_CVE_PARAM_RE = re.compile(r"^(cve|cve_id|vulnerability)$", re.I)


def infer_input_entity(param_name: str, sample_val: str | None = None) -> tuple[str, str]:
    """Infer the input entity type and validation helper kind from param name and sample value.

    Returns:
        A tuple of (entity_type, validator_kind), e.g. ("IPv4Address", "ip").
    """
    clean_name = param_name.strip("{}<>:")
    val = (sample_val or "").strip()

    if _IP_PARAM_RE.match(clean_name) or (
        val and re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", val)
    ):
        return "IPv4Address", "ip"
    if _IPV6_PARAM_RE.match(clean_name) or (val and ":" in val and len(val) >= 3):
        return "IPv6Address", "ip"
    if _DOMAIN_PARAM_RE.match(clean_name) or (
        val and re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", val) and not val.startswith("http")
    ):
        return "Domain", "domain"
    if _URL_PARAM_RE.match(clean_name) or (
        val and (val.startswith("http://") or val.startswith("https://"))
    ):
        return "URL", "url"
    if _EMAIL_PARAM_RE.match(clean_name) or (val and "@" in val):
        return "EmailAddress", "email"
    if _HASH_PARAM_RE.match(clean_name) or (val and re.match(r"^[a-fA-F0-9]{32,64}$", val)):
        return "Hash", "hash"
    if _CVE_PARAM_RE.match(clean_name) or (val and re.match(r"^CVE-\d{4}-\d+$", val, re.I)):
        return "CVE", "cve"

    return "Phrase", "phrase"


# Output entity heuristic patterns
_AS_RE = re.compile(r"^(asn|as_number|as_num|autonomous_system)$", re.I)
_ISP_RE = re.compile(r"^(isp|org|organization|operator|as_name|carrier)$", re.I)
_COUNTRY_RE = re.compile(r"^(country|country_code|country_name|countrycode|cc)$", re.I)
_CITY_RE = re.compile(r"^(city|city_name|town)$", re.I)
_LOCATION_RE = re.compile(r"^(location|continent|region|state|province)$", re.I)
_NETBLOCK_RE = re.compile(r"^(cidr|netblock|subnet|ip_range|network)$", re.I)
_DNS_RE = re.compile(r"^(dns|reverse_dns|ptr|rdns|nameserver)$", re.I)
_CVE_OUT_RE = re.compile(r"^(cve|cves|vulnerabilities|cve_id)$", re.I)
_ATTACK_RE = re.compile(
    r"^(behavior|behaviors|technique|attack_pattern|mitre|threat|tactic)$", re.I
)


def infer_output_entity(field_name: str, sample_val: Any = None) -> OutputFieldMapping:
    """Infer output entity mapping from a response key and sample value."""
    key = field_name.strip()
    is_list = isinstance(sample_val, list)

    if _AS_RE.match(key):
        return OutputFieldMapping(
            field_name=key, entity_type="AS", strip_prefix="AS", is_list=is_list
        )
    if _ISP_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="ISP", is_list=is_list)
    if _COUNTRY_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="Country", is_list=is_list)
    if _CITY_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="City", is_list=is_list)
    if _LOCATION_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="Location", is_list=is_list)
    if _NETBLOCK_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="Netblock", is_list=is_list)
    if _DNS_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="DNSName", is_list=is_list)
    if _DOMAIN_PARAM_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="Domain", is_list=is_list)
    if _URL_PARAM_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="URL", is_list=is_list)
    if _EMAIL_PARAM_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="EmailAddress", is_list=is_list)
    if _CVE_OUT_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="CVE", is_list=is_list)
    if _ATTACK_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="AttackPattern", is_list=is_list)
    if _HASH_PARAM_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="Hash", is_list=is_list)
    if _IP_PARAM_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="IPv4Address", is_list=is_list)

    # Format human-readable title for Phrase
    title = key.replace("_", " ").title()
    return OutputFieldMapping(
        field_name=key,
        entity_type="Phrase",
        label_prefix=f"{title}: ",
        is_list=is_list,
    )
