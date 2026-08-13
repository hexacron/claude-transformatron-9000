"""Sample transforms backed by the ffraud.com public IP reputation API.

This module is an illustrative example, not a maintained integration. It exists to
show the shape of a working transform — input/output annotations, input validation,
upstream error handling, and entity mapping — against an API that needs no API key,
so it runs on a fresh clone.

Copy it into ``server/transforms/`` and adapt it, or delete it. If you keep it,
note that ffraud.com is a third-party service this project has no affiliation with,
and its coverage is uneven (see the README's caveat on fraud scores).
"""

import ipaddress
from typing import Any

from maltego.entities import AS, DNSName, EmailAddress, IPv4Address, ISP, Location, Phrase
from maltego.model.context import MaltegoContext
from maltego.model.exception import MaltegoException, MaltegoHTTPDataProviderNotFound
from maltego.server import register_transform
from maltego.util import IntegrationClient

IP_LOOKUP_URL = "https://api.ffraud.com/public/ip/{ip}"
TRANSFORM_SET = "ffraud"

client = IntegrationClient()

# Response flags that mark an IP as anonymising or otherwise notable infrastructure.
DETECTION_FLAGS = (
    ("tor", "Tor exit node"),
    ("vpn", "VPN"),
    ("proxy", "Proxy"),
    ("relay", "Relay"),
    ("is_residential_proxy", "Residential proxy"),
    ("hosting", "Hosting / datacenter"),
    ("mobile", "Mobile network"),
    ("is_crawler", "Crawler"),
    ("is_abuser", "Known abuser"),
    ("recent_abuse", "Recent abuse"),
    ("shared_connection", "Shared connection"),
)


def _validate_ipv4(value: str) -> str:
    """Return `value` as a plain IPv4 address string, or raise `ValueError`.

    The address is interpolated into the upstream URL path, so it is validated
    rather than trusted.
    """
    address = ipaddress.ip_address(value.strip())
    if address.version != 4:
        raise ValueError(f"expected an IPv4 address, got IPv{address.version}")
    return str(address)


async def _lookup(ip: str, context: MaltegoContext) -> dict[str, Any] | None:
    """Fetch the ffraud record for `ip`, or None when it is unavailable."""
    try:
        response = await client.get(IP_LOOKUP_URL.format(ip=ip), context=context)
    except MaltegoHTTPDataProviderNotFound:
        context.log.inform("No ffraud record for the requested address")
        return None
    except MaltegoException as exc:
        context.log.fatal(f"ffraud lookup failed: {exc.message}")
        return None

    data = response.json()
    if not data.get("success"):
        context.log.inform("ffraud returned no data for the requested address")
        return None
    return data


@register_transform(display_name="ffraud: IP Reputation", transform_set=TRANSFORM_SET)
async def ffraud_ip_reputation(input_entity: IPv4Address, context: MaltegoContext) -> list[Phrase]:
    """Return the fraud score, risk band, and detection flags for an IPv4 address."""
    try:
        ip = _validate_ipv4(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return []

    data = await _lookup(ip, context)
    if data is None:
        return []

    results = [
        Phrase(value=f"Fraud score: {data.get('fraud_score', 0)} ({data.get('risk', 'unknown')})")
    ]

    detections = [label for field, label in DETECTION_FLAGS if data.get(field)]
    for label in detections:
        results.append(Phrase(value=label))

    for tag in data.get("threat_tags", []):
        results.append(Phrase(value=f"Threat tag: {tag}"))

    reason = data.get("reason")
    if reason:
        results.append(Phrase(value=reason))

    context.log.inform(f"ffraud returned {len(detections)} detection flags")
    return results


@register_transform(display_name="ffraud: IP to Network Details", transform_set=TRANSFORM_SET)
async def ffraud_ip_to_network(
    input_entity: IPv4Address, context: MaltegoContext
) -> list[AS | ISP | DNSName | Location]:
    """Return the ASN, ISP, reverse hostname, and geolocation for an IPv4 address."""
    try:
        ip = _validate_ipv4(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return []

    data = await _lookup(ip, context)
    if data is None:
        return []

    results: list[AS | ISP | DNSName | Location] = []

    asn = data.get("ASN")
    if asn:
        results.append(AS(value=str(asn)))

    isp = data.get("ISP") or data.get("organization")
    if isp:
        results.append(ISP(value=isp))

    hostname = data.get("hostname")
    if hostname:
        results.append(DNSName(value=hostname))

    geo = data.get("geo", {})
    city, country = geo.get("city"), geo.get("country_name")
    if city or country:
        results.append(Location(value=", ".join(part for part in (city, country) if part)))

    return results


@register_transform(display_name="ffraud: IP to Abuse Contact", transform_set=TRANSFORM_SET)
async def ffraud_ip_to_abuse_contact(
    input_entity: IPv4Address, context: MaltegoContext
) -> list[EmailAddress]:
    """Return the WHOIS abuse contact address for an IPv4 address."""
    try:
        ip = _validate_ipv4(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return []

    data = await _lookup(ip, context)
    if data is None:
        return []

    contact = data.get("whois", {}).get("abuse_contact")
    if not contact:
        context.log.inform("No abuse contact published for this address")
        return []

    return [EmailAddress(value=contact)]
