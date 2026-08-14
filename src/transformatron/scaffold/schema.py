"""Schema models and entity mapping heuristics for transform scaffolding."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from maltego import entities


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
    emit_input_constraint: bool = True

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


def qualified_entity_type(entity_class_name: str) -> str:
    """Return the Maltego ``TYPE_NAME`` for a std-entities class name.

    Read off the class rather than rebuilt as ``"maltego." + ClassName``: that rule holds
    for most entities but breaks for 72 of the 244 the package exports. Every
    ``Affiliation*`` class maps to ``maltego.affiliation.<Name>``, every ``STIX2*`` class to
    a hyphenated ``maltego.STIX2.<name>`` (``STIX2attackpattern`` →
    ``maltego.STIX2.attack-pattern``), and ``Hashtag`` to a lowercase ``maltego.hashtag``.
    Reconstructing the string emits a constraint naming an entity type that does not exist,
    which the client silently fails to route on.

    Args:
        entity_class_name: Bare class name, e.g. ``"Domain"``.

    Returns:
        The qualified type name, e.g. ``"maltego.Domain"``.

    Raises:
        ValueError: If no such entity class is exported by ``maltego.entities``.
    """
    entity_class = getattr(entities, entity_class_name, None)
    type_name = getattr(entity_class, "TYPE_NAME", None)
    if not isinstance(type_name, str):
        raise ValueError(
            f"'{entity_class_name}' is not an entity class exported by maltego.entities. "
            f"Check the name against `python -c 'import maltego.entities; "
            f"print(dir(maltego.entities))'`."
        )
    return type_name


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

# Widened coverage, following the entity table in the SDK's own
# maltego-transform-design/references/standard-entity-selection.md. Anything not matched
# here becomes a Phrase, which renders as text an investigator cannot pivot from — so a
# mapped entity is worth having wherever the field name is unambiguous.
#
# `org`/`organization` deliberately stay with _ISP_RE above: on the netblock and IP-lookup
# APIs this scaffolder targets, that field is the network operator, not a company.
_PERSON_RE = re.compile(r"^(person|name|full_name|owner|author|contact|registrant)$", re.I)
_COMPANY_RE = re.compile(r"^(company|company_name|employer|vendor|manufacturer)$", re.I)
_PHONE_RE = re.compile(r"^(phone|phone_number|tel|telephone|mobile|fax)$", re.I)
_ALIAS_RE = re.compile(r"^(alias|username|user_name|handle|screen_name|nick|nickname|login)$", re.I)
_IMAGE_RE = re.compile(r"^(image|image_url|avatar|photo|thumbnail|screenshot|logo)$", re.I)
_MAC_RE = re.compile(r"^(mac|mac_address|macaddr|hardware_address)$", re.I)
_PORT_RE = re.compile(r"^(port|ports|open_ports|dest_port|src_port)$", re.I)
_BTC_RE = re.compile(r"^(bitcoin|bitcoin_address|btc|btc_address)$", re.I)
_CRYPTO_RE = re.compile(r"^(wallet|wallet_address|crypto_address|cryptocurrency_address)$", re.I)
_WEBSITE_RE = re.compile(r"^(website|site|web_site|homepage)$", re.I)
_MALWARE_RE = re.compile(r"^(malware|malware_name|family|malware_family|trojan)$", re.I)
_CERT_RE = re.compile(r"^(certificate|cert|ssl_cert|x509|tls_cert)$", re.I)
_WHOIS_RE = re.compile(r"^(whois|whois_record|whois_data)$", re.I)


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
    if _PERSON_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="Person", is_list=is_list)
    if _COMPANY_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="Company", is_list=is_list)
    if _PHONE_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="PhoneNumber", is_list=is_list)
    if _ALIAS_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="Alias", is_list=is_list)
    if _IMAGE_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="Image", is_list=is_list)
    if _MAC_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="MacAddress", is_list=is_list)
    if _PORT_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="Port", is_list=is_list)
    if _BTC_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="BTCAddress", is_list=is_list)
    if _CRYPTO_RE.match(key):
        return OutputFieldMapping(
            field_name=key, entity_type="CryptocurrencyAddress", is_list=is_list
        )
    if _WEBSITE_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="Website", is_list=is_list)
    if _MALWARE_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="Malware", is_list=is_list)
    if _CERT_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="X509Certificate", is_list=is_list)
    if _WHOIS_RE.match(key):
        return OutputFieldMapping(field_name=key, entity_type="WHOISRecord", is_list=is_list)

    # Format human-readable title for Phrase
    title = key.replace("_", " ").title()
    return OutputFieldMapping(
        field_name=key,
        entity_type="Phrase",
        label_prefix=f"{title}: ",
        is_list=is_list,
    )
