"""Schema models and entity mapping heuristics for transform scaffolding."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field, replace
from typing import Any

from maltego import entities


@dataclass
class OutputFieldMapping:
    """Mapping from a response field to a Maltego entity.

    ``sub_field`` reaches one level down: into each element when ``is_list`` is set (a list
    of objects), or into the nested object otherwise.
    """

    field_name: str
    entity_type: str
    label_prefix: str = ""
    strip_prefix: str = ""
    is_list: bool = False
    sub_field: str | None = None


@dataclass
class ScaffoldTransformConfig:
    """Configuration for generating a single Maltego transform function."""

    transform_id: str
    display_name: str
    input_entity: str
    endpoint_path: str
    input_param_name: str = "input_val"
    input_location: str = "query"  # "path", "query", or "body"
    http_method: str = "GET"
    output_mappings: list[OutputFieldMapping] = field(default_factory=list)
    description: str = ""
    emit_input_constraint: bool = True
    # Sent with every request. The entry named by input_param_name is replaced by the
    # input value at run time when the input travels in that location.
    query_params: dict[str, Any] = field(default_factory=dict)
    request_body: dict[str, Any] | None = None
    body_encoding: str = "json"  # "json" or "form"
    response_is_list: bool = False

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
    # What the scaffolder could not do and the author has to know about: skipped
    # operations, unsupported authentication, and the like.
    notes: list[str] = field(default_factory=list)


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


_EMAIL_VALUE_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
_CVE_VALUE_RE = re.compile(r"^CVE-\d{4}-\d+$", re.I)
_HASH_VALUE_RE = re.compile(r"^[a-fA-F0-9]{32,128}$")
_DOMAIN_VALUE_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+([a-z]{2,63})\.?$", re.I
)
# File extensions that the domain pattern would otherwise accept as a TLD. A path segment
# like `lookup.json` or `api.php` is a resource name, not a domain to pivot on.
_FILE_EXTENSIONS = frozenset(
    {"json", "xml", "php", "html", "htm", "txt", "csv", "asp", "aspx", "jsp", "cgi", "yaml"}
)


def infer_value_entity(value: Any) -> str | None:
    """Return the entity a value unambiguously looks like, or None.

    Strict on purpose: this decides between entity types from data alone, so a loose
    rule (any string containing ``:`` as IPv6, say) would turn timestamps into addresses.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        pass
    else:
        return "IPv4Address" if address.version == 4 else "IPv6Address"
    if text.startswith(("http://", "https://")):
        return "URL"
    if _EMAIL_VALUE_RE.match(text):
        return "EmailAddress"
    if _CVE_VALUE_RE.match(text):
        return "CVE"
    domain = _DOMAIN_VALUE_RE.match(text)
    if domain and domain.group(1).lower() not in _FILE_EXTENSIONS:
        return "Domain"
    return None


def infer_input_entity(param_name: str, sample_val: str | None = None) -> tuple[str, str]:
    """Infer the input entity type and validation helper kind from param name and sample value.

    The name is consulted first, then the value.

    Returns:
        A tuple of (entity_type, validator_kind), e.g. ("IPv4Address", "ip").
    """
    clean_name = param_name.strip("{}<>:")

    by_name = (
        (_IP_PARAM_RE, ("IPv4Address", "ip")),
        (_IPV6_PARAM_RE, ("IPv6Address", "ip")),
        (_DOMAIN_PARAM_RE, ("Domain", "domain")),
        (_URL_PARAM_RE, ("URL", "url")),
        (_EMAIL_PARAM_RE, ("EmailAddress", "email")),
        (_HASH_PARAM_RE, ("Hash", "hash")),
        (_CVE_PARAM_RE, ("CVE", "cve")),
    )
    for pattern, result in by_name:
        if pattern.match(clean_name):
            return result

    kinds = {
        "IPv4Address": "ip",
        "IPv6Address": "ip",
        "Domain": "domain",
        "URL": "url",
        "EmailAddress": "email",
        "CVE": "cve",
    }
    by_value = infer_value_entity(sample_val)
    if by_value:
        return by_value, kinds[by_value]
    if sample_val and _HASH_VALUE_RE.match(sample_val.strip()):
        return "Hash", "hash"

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


# Name-based matches that the value may overrule. `name` maps to Person, but a list of DNS
# answers carries domains and addresses under `name` and `data`; a value that is
# unambiguously an address or domain is better evidence than a generic key.
_WEAK_NAME_MATCHES = frozenset({"Phrase", "Person"})

# Envelope fields that describe the response rather than the thing looked up.
_ENVELOPE_KEYS = frozenset({"status", "ok", "success", "error", "message", "code"})


def infer_output_entity(field_name: str, sample_val: Any = None) -> OutputFieldMapping:
    """Infer output entity mapping from a response key and sample value."""
    is_list = isinstance(sample_val, list)
    mapping = _infer_output_by_name(field_name, is_list)
    if mapping.entity_type in _WEAK_NAME_MATCHES:
        first = sample_val[0] if is_list and sample_val else sample_val
        by_value = infer_value_entity(first)
        if by_value:
            return OutputFieldMapping(field_name=field_name, entity_type=by_value, is_list=is_list)
    return mapping


def _title(key: str) -> str:
    return key.replace("_", " ").title()


def infer_output_mappings(record: dict[str, Any]) -> list[OutputFieldMapping]:
    """Map every field of one response record to an entity.

    Nested data is followed one level: a nested object contributes each of its scalar
    fields, and a list of objects contributes the element fields that map to a real entity
    (or, failing that, its first text field). Deeper structure is left for the author —
    guessing through it produces entities nobody asked for.
    """
    mappings: list[OutputFieldMapping] = []
    for key, value in record.items():
        if key.lower() in _ENVELOPE_KEYS:
            continue
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, dict | list):
                    continue
                mapping = infer_output_entity(sub_key, sub_value)
                label = f"{_title(key)} {_title(sub_key)}: " if mapping.label_prefix else ""
                mappings.append(
                    replace(mapping, field_name=key, sub_field=sub_key, label_prefix=label)
                )
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            element = value[0]
            candidates = [
                infer_output_entity(sub_key, sub_value)
                for sub_key, sub_value in element.items()
                if not isinstance(sub_value, dict | list)
            ]
            chosen = [m for m in candidates if m.entity_type != "Phrase"]
            if not chosen:
                chosen = [m for m in candidates if isinstance(element[m.field_name], str)][:1]
            mappings.extend(
                replace(m, field_name=key, sub_field=m.field_name, is_list=True, label_prefix="")
                for m in chosen
            )
        else:
            mappings.append(infer_output_entity(key, value))
    return mappings


def _infer_output_by_name(field_name: str, is_list: bool) -> OutputFieldMapping:
    """Infer an output mapping from the field name alone."""
    key = field_name

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
