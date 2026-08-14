"""Transforms over the GreyNoise Community API.

One record answers two independent questions — is this address scanning the internet
(``noise``), and is it a known benign service (``riot``) — so it is split into two
transforms rather than one that mixes verdicts. Both fetch the same record.

See ``api.py`` for the shared client, the credential setting, and the reason an HTTP 404
from this API is treated as a result rather than a miss.
"""

from typing import Any

from maltego.entities import URL, IPv4Address, Phrase
from maltego.model.context import MaltegoContext
from maltego.server import register_transform
from transforms.greynoise.api import TRANSFORM_SET, api_key_setting, fetch, validate_ipv4


async def _community_record(
    value: str, settings: dict[str, Any], context: MaltegoContext
) -> dict[str, Any] | None:
    """Validate `value` as an IPv4 address and return its Community record, or None."""
    try:
        ip = validate_ipv4(value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return None
    return await fetch(ip, settings, context)


@register_transform(
    display_name="GreyNoise: IP Reputation",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def greynoise_ip_reputation(
    input_entity: IPv4Address, settings: dict[str, Any], context: MaltegoContext
) -> list[Phrase | URL]:
    """Return the scanning verdict, classification and GreyNoise Visualizer link.

    An address GreyNoise has never observed still produces a verdict — "not observed
    scanning" is the useful negative result this API exists to give, so it is put on the
    graph rather than returned as nothing.
    """
    data = await _community_record(input_entity.value, settings, context)
    if data is None:
        return []

    results: list[Phrase | URL] = []

    # classification is absent unless the address was actually observed, so the noise
    # flag is what decides the verdict line.
    if data.get("noise"):
        classification = data.get("classification") or "unknown"
        results.append(Phrase(value=f"GreyNoise: observed scanning ({classification})"))
    else:
        results.append(Phrase(value="GreyNoise: not observed scanning the internet"))

    # "unknown" is the upstream's placeholder for an unattributed scanner, which reads
    # on a graph as though the actor were named that.
    actor = data.get("name")
    if actor and actor != "unknown":
        results.append(Phrase(value=f"Actor: {actor}"))

    last_seen = data.get("last_seen")
    if last_seen:
        results.append(Phrase(value=f"Last seen: {last_seen}"))

    link = data.get("link")
    if link:
        results.append(URL(value=str(link)))

    return results


@register_transform(
    display_name="GreyNoise: IP to RIOT Status",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def greynoise_ip_to_riot(
    input_entity: IPv4Address, settings: dict[str, Any], context: MaltegoContext
) -> list[Phrase]:
    """Return whether the address belongs to a known benign service.

    RIOT is GreyNoise's set of common business services — public resolvers, CDN edges,
    SaaS ranges. A RIOT hit is grounds for ruling an address out, which is why it is a
    separate transform from the scanning verdict rather than another field on it.
    """
    data = await _community_record(input_entity.value, settings, context)
    if data is None:
        return []

    if not data.get("riot"):
        context.log.inform("GreyNoise does not list this address as a known benign service")
        return [Phrase(value="RIOT: not a known benign service")]

    results = [Phrase(value="RIOT: known benign service")]

    # On a RIOT hit `name` is the provider (e.g. "Google Public DNS") rather than the
    # scanner-actor placeholder it carries on the noise side.
    provider = data.get("name")
    if provider and provider != "unknown":
        results.append(Phrase(value=f"Provider: {provider}"))

    classification = data.get("classification")
    if classification:
        results.append(Phrase(value=f"Classification: {classification}"))

    return results
