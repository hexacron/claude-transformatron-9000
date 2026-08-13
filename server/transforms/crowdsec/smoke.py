"""Transforms over the CrowdSec CTI ``/smoke`` dataset.

One record carries reputation, network, geolocation, attack behaviour and CVE data,
so it is split across transforms by what an analyst pivots to rather than by request:
each one fetches the same record and maps a different slice of it.

See ``api.py`` for the shared client and the credential setting.
"""

from typing import Any

from maltego.entities import (
    AS,
    CVE,
    ISP,
    AttackPattern,
    City,
    Country,
    DNSName,
    IPv4Address,
    Netblock,
    Phrase,
)
from maltego.model.context import MaltegoContext
from maltego.server import register_transform
from transforms.crowdsec.api import (
    MAX_ITEMS,
    TRANSFORM_SET,
    api_key_setting,
    fetch,
    named_labels,
    validate_ip,
)


async def _smoke_record(
    value: str, settings: dict[str, Any], context: MaltegoContext
) -> dict[str, Any] | None:
    """Validate `value` as an IP address and return its CTI record, or None."""
    try:
        ip = validate_ip(value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return None
    return await fetch(f"/smoke/{ip}", settings, context)


@register_transform(
    display_name="CrowdSec: IP Reputation",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def crowdsec_ip_reputation(
    input_entity: IPv4Address, settings: dict[str, Any], context: MaltegoContext
) -> list[Phrase]:
    """Return the reputation verdict, confidence, scores and activity window."""
    data = await _smoke_record(input_entity.value, settings, context)
    if data is None:
        return []

    results: list[Phrase] = []

    reputation = data.get("reputation")
    if reputation:
        results.append(Phrase(value=f"Reputation: {reputation}"))

    confidence = data.get("confidence")
    if confidence:
        results.append(Phrase(value=f"Confidence: {confidence}"))

    # A high background noise score means the address scans indiscriminately, which
    # is context for the reputation rather than a finding on its own.
    noise = data.get("background_noise")
    if noise:
        results.append(Phrase(value=f"Background noise: {noise}"))

    overall = data.get("scores", {}).get("overall", {})
    if isinstance(overall, dict) and overall:
        parts = [f"{name} {value}" for name, value in overall.items()]
        results.append(Phrase(value=f"Scores — {', '.join(parts)}"))

    history = data.get("history", {})
    first_seen, last_seen = history.get("first_seen"), history.get("last_seen")
    if first_seen or last_seen:
        results.append(Phrase(value=f"Seen {first_seen or 'unknown'} to {last_seen or 'unknown'}"))

    # False positives are the reason not to act on a verdict, so they are surfaced
    # alongside it rather than buried in the behaviour transform.
    for label in named_labels(data.get("classifications", {}).get("false_positives")):
        results.append(Phrase(value=f"False positive: {label}"))

    if not results:
        context.log.inform("CrowdSec returned a record with no reputation data")

    return results


@register_transform(
    display_name="CrowdSec: IP to Attack Behaviour",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def crowdsec_ip_to_behaviour(
    input_entity: IPv4Address, settings: dict[str, Any], context: MaltegoContext
) -> list[AttackPattern | CVE | Phrase]:
    """Return the attack categories, scenarios, MITRE techniques and CVEs reported."""
    data = await _smoke_record(input_entity.value, settings, context)
    if data is None:
        return []

    results: list[AttackPattern | CVE | Phrase] = []

    for label in named_labels(data.get("behaviors")):
        results.append(AttackPattern(value=label))

    for label in named_labels(data.get("mitre_techniques")):
        results.append(AttackPattern(value=label))

    cves = data.get("cves")
    if isinstance(cves, list):
        for cve in cves[:MAX_ITEMS]:
            if cve:
                results.append(CVE(value=str(cve)))

    # Scenarios are CrowdSec hub rule names rather than a standard taxonomy, so they
    # stay Phrases instead of being forced into AttackPattern.
    for label in named_labels(data.get("attack_details")):
        results.append(Phrase(value=f"Scenario: {label}"))

    for label in named_labels(data.get("classifications", {}).get("classifications")):
        results.append(Phrase(value=f"Classification: {label}"))

    if not results:
        context.log.inform("CrowdSec reported no attack behaviour for this address")

    return results


@register_transform(
    display_name="CrowdSec: IP to Network",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def crowdsec_ip_to_network(
    input_entity: IPv4Address, settings: dict[str, Any], context: MaltegoContext
) -> list[AS | ISP | Netblock | DNSName]:
    """Return the ASN, operator, containing ranges and reverse DNS for an address."""
    data = await _smoke_record(input_entity.value, settings, context)
    if data is None:
        return []

    results: list[AS | ISP | Netblock | DNSName] = []

    as_num = data.get("as_num")
    if as_num:
        results.append(AS(value=str(as_num)))

    as_name = data.get("as_name")
    if as_name:
        results.append(ISP(value=str(as_name)))

    # Both ranges are returned: the announced range and the /24 it sits in. They are
    # often the same, so the second is only added when it differs.
    ranges = [data.get("ip_range"), data.get("ip_range_24")]
    for cidr in dict.fromkeys(r for r in ranges if r):
        results.append(Netblock(value=str(cidr)))

    reverse_dns = data.get("reverse_dns")
    if reverse_dns:
        results.append(DNSName(value=str(reverse_dns)))

    return results


@register_transform(
    display_name="CrowdSec: IP to Location",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def crowdsec_ip_to_location(
    input_entity: IPv4Address, settings: dict[str, Any], context: MaltegoContext
) -> list[City | Country]:
    """Return the city and country CrowdSec places the address in."""
    data = await _smoke_record(input_entity.value, settings, context)
    if data is None:
        return []

    location = data.get("location", {})
    if not isinstance(location, dict):
        return []

    results: list[City | Country] = []

    city = location.get("city")
    if city:
        results.append(City(value=str(city)))

    country = location.get("country")
    if country:
        results.append(Country(value=str(country)))

    if not results:
        context.log.inform("CrowdSec returned no geolocation for this address")

    return results


@register_transform(
    display_name="CrowdSec: IP to Targeted Countries",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def crowdsec_ip_to_targets(
    input_entity: IPv4Address, settings: dict[str, Any], context: MaltegoContext
) -> list[Country]:
    """Return the countries this address was reported attacking, most-targeted first.

    ``target_countries`` is a country-code to percentage mapping of where the reports
    against this address came from — the victims, not the address's own location.
    """
    data = await _smoke_record(input_entity.value, settings, context)
    if data is None:
        return []

    targets = data.get("target_countries")
    if not isinstance(targets, dict) or not targets:
        context.log.inform("CrowdSec reported no targeted countries for this address")
        return []

    ranked = sorted(targets.items(), key=lambda item: item[1], reverse=True)
    return [Country(value=code) for code, _ in ranked[:MAX_ITEMS] if code]
