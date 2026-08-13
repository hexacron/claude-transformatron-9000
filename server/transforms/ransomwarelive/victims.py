"""Transforms pivoting from victim organisations and their domains.

These are the entry points into the dataset: start from a `Company` or `Domain`
already on the graph and find out whether it appears in a ransomware leak site
listing, then pivot to the group that claimed it.
"""

from typing import Any

from maltego.entities import URL, Company, Domain, Location, Malware, Phrase, Website
from maltego.model.context import MaltegoContext
from maltego.server import register_transform
from transforms.ransomwarelive.api import (
    TRANSFORM_SET,
    api_key_setting,
    fetch,
    normalise_victim,
)

SEARCH_LIMIT = 100


async def _search(
    query: str, settings: dict[str, Any], context: MaltegoContext
) -> list[dict[str, Any]]:
    """Return normalised victim records matching `query`."""
    term = query.strip()
    if not term:
        context.log.fatal("Invalid input: expected a company name or domain to search for")
        return []

    # `q` is sent as a query parameter, not interpolated into the path, so the
    # HTTP layer handles escaping.
    data = await fetch("/victims/search", settings, context, params={"q": term})
    if data is None:
        return []

    victims = [normalise_victim(v) for v in data.get("victims", [])]
    context.log.inform(f"Matched {data.get('count', len(victims))} victim listings")
    return victims[:SEARCH_LIMIT]


@register_transform(
    display_name="Ransomware.live: Search Victims",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def search_victims(
    input_entity: Company, settings: dict[str, Any], context: MaltegoContext
) -> list[Company]:
    """Return victim listings whose organisation name matches the input."""
    results: list[Company] = []
    for victim in await _search(input_entity.value, settings, context):
        name = victim["victim"]
        if not name:
            continue
        attributes = [
            f"Group: {victim['group']}" if victim["group"] else "",
            f"Attack date: {victim['attackdate'][:10]}" if victim["attackdate"] else "",
            f"Country: {victim.get('country')}" if victim.get("country") else "",
            f"Sector: {victim.get('activity')}" if victim.get("activity") else "",
        ]
        results.append(Company(value=name, note="\n".join(a for a in attributes if a)))
    return results


@register_transform(
    display_name="Ransomware.live: Domain to Breach",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def domain_to_breach(
    input_entity: Domain, settings: dict[str, Any], context: MaltegoContext
) -> list[Company | Malware]:
    """Return the victim organisation and claiming group for a domain.

    Only listings whose `website` field matches the input domain are returned,
    so a substring hit on an unrelated organisation name does not produce a
    false attribution.
    """
    domain = input_entity.value.strip().lower().removeprefix("www.")
    results: list[Company | Malware] = []
    seen_groups: set[str] = set()

    for victim in await _search(domain, settings, context):
        website = (victim.get("website") or "").strip().lower().removeprefix("www.")
        if website != domain:
            continue

        name = victim["victim"]
        group = victim["group"]
        if name:
            note = f"Group: {group}" if group else ""
            results.append(Company(value=name, note=note))
        if group and group not in seen_groups:
            seen_groups.add(group)
            results.append(Malware(value=group))

    if not results:
        context.log.inform("No leak site listing matched this domain exactly")
    return results


@register_transform(
    display_name="Ransomware.live: Victim Details",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def victim_details(
    input_entity: Company, settings: dict[str, Any], context: MaltegoContext
) -> list[Malware | Website | Location | Phrase | URL]:
    """Return the group, website, country, sector and leak page for a victim.

    Takes the first exact name match from the search index.
    """
    name = input_entity.value.strip()
    matches = [v for v in await _search(name, settings, context) if v["victim"].strip() == name]
    if not matches:
        context.log.inform("No exact victim match; try Search Victims first")
        return []

    victim = matches[0]
    results: list[Malware | Website | Location | Phrase | URL] = []

    if victim["group"]:
        results.append(Malware(value=victim["group"]))
    if victim.get("website"):
        results.append(Website(value=victim["website"]))
    if victim.get("country"):
        results.append(Location(value=victim["country"]))
    if victim.get("activity"):
        results.append(Phrase(value=f"Sector: {victim['activity']}"))
    if victim["attackdate"]:
        results.append(Phrase(value=f"Attack date: {victim['attackdate'][:10]}"))
    for field in ("permalink", "post_url", "screenshot"):
        target = victim.get(field)
        if target:
            results.append(URL(value=target, note=field))

    return results


@register_transform(
    display_name="Ransomware.live: Victim to Press",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def victim_to_press(
    input_entity: Company, settings: dict[str, Any], context: MaltegoContext
) -> list[URL]:
    """Return press articles covering the attack on a victim organisation."""
    name = input_entity.value.strip()
    results: list[URL] = []
    for victim in await _search(name, settings, context):
        if victim["victim"].strip() != name:
            continue
        article = victim.get("press")
        if article:
            results.append(URL(value=article, note=f"Press coverage: {victim['victim']}"))

    if not results:
        context.log.inform("No press coverage linked to this victim")
    return results
