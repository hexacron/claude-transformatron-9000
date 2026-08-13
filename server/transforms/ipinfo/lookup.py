"""Transforms over the IPinfo Lite API.

Lite covers ASN, organisation and country-level geolocation. Each transform accepts
both IPv4 and IPv6 because the endpoint serves both. See ``api.py`` for the shared
client and the two undocumented response behaviours it handles.
"""

from typing import Any

from maltego.entities import AS, ISP, Country, Domain, IPv4Address, IPv6Address, Location
from maltego.model.context import MaltegoContext
from maltego.server import register_transform
from transforms.ipinfo.api import TRANSFORM_SET, api_token_setting, fetch, validate_ip


async def _record(
    value: str, settings: dict[str, Any], context: MaltegoContext
) -> dict[str, Any] | None:
    """Validate `value` as an IP address and return its IPinfo record, or None."""
    try:
        ip = validate_ip(value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return None
    return await fetch(ip, settings, context)


def _network_entities(data: dict[str, Any]) -> list[AS | ISP | Domain]:
    """Map an IPinfo record onto the ASN, operator and operator domain."""
    results: list[AS | ISP | Domain] = []

    # "AS15169" — strip the prefix so the value is the number Maltego's AS entity expects.
    asn = str(data.get("asn", ""))
    if asn:
        results.append(AS(value=asn.removeprefix("AS")))

    as_name = data.get("as_name")
    if as_name:
        results.append(ISP(value=as_name))

    as_domain = data.get("as_domain")
    if as_domain:
        results.append(Domain(value=as_domain))

    return results


@register_transform(
    display_name="IPinfo: IP to Network",
    transform_set=TRANSFORM_SET,
    settings=[api_token_setting()],
)
async def ipinfo_ip_to_network(
    input_entity: IPv4Address, settings: dict[str, Any], context: MaltegoContext
) -> list[AS | ISP | Domain]:
    """Return the ASN, operator name and operator domain for an IPv4 address."""
    data = await _record(input_entity.value, settings, context)
    if data is None:
        return []
    return _network_entities(data)


@register_transform(
    display_name="IPinfo: IPv6 to Network",
    transform_set=TRANSFORM_SET,
    settings=[api_token_setting()],
)
async def ipinfo_ipv6_to_network(
    input_entity: IPv6Address, settings: dict[str, Any], context: MaltegoContext
) -> list[AS | ISP | Domain]:
    """Return the ASN, operator name and operator domain for an IPv6 address."""
    data = await _record(input_entity.value, settings, context)
    if data is None:
        return []
    return _network_entities(data)


@register_transform(
    display_name="IPinfo: IP to Location",
    transform_set=TRANSFORM_SET,
    settings=[api_token_setting()],
)
async def ipinfo_ip_to_location(
    input_entity: IPv4Address, settings: dict[str, Any], context: MaltegoContext
) -> list[Country | Location]:
    """Return the country and continent for an IPv4 address.

    Lite is country-level only. There is no city or coordinate data on this tier, so
    the Location entity carries the continent rather than a street-level place.
    """
    data = await _record(input_entity.value, settings, context)
    if data is None:
        return []

    results: list[Country | Location] = []

    country = data.get("country")
    if country:
        results.append(Country(value=country))

    continent = data.get("continent")
    if continent:
        results.append(Location(value=continent))

    if not results:
        context.log.inform("IPinfo returned no geolocation for this address")

    return results
