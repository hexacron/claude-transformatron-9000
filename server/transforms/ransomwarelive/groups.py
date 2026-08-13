"""Transforms pivoting from a ransomware group to its profile and infrastructure.

Input is a `Malware` entity whose value is the group slug as ransomware.live
names it (lowercase, e.g. `lockbit3`, `akira`, `alphv`). Use the
"Ransomware.live: List Groups" transform on a `Phrase` to discover valid slugs.
"""

from typing import Any

from maltego.entities import CVE, TTP, URL, Company, Malware, Phrase
from maltego.model.context import MaltegoContext
from maltego.server import register_transform
from transforms.ransomwarelive.api import (
    TRANSFORM_SET,
    api_key_setting,
    fetch,
    normalise_victim,
    validate_group,
)

# Victim listings are large — LockBit alone has over 2000. Cap what lands on the graph.
VICTIM_LIMIT = 100


async def _group_detail(
    value: str, settings: dict[str, Any], context: MaltegoContext
) -> dict[str, Any] | None:
    """Fetch the group profile for `value`, or None when unavailable."""
    try:
        group = validate_group(value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return None
    return await fetch(f"/groups/{group}", settings, context)


@register_transform(
    display_name="Ransomware.live: Group Profile",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def group_profile(
    input_entity: Malware, settings: dict[str, Any], context: MaltegoContext
) -> list[Phrase]:
    """Return the activity summary and description for a ransomware group."""
    data = await _group_detail(input_entity.value, settings, context)
    if data is None:
        return []

    results: list[Phrase] = []

    victims = data.get("victims")
    if victims is not None:
        results.append(Phrase(value=f"Victims: {victims}"))

    for field, label in (("firstseen", "First seen"), ("lastseen", "Last seen")):
        stamp = data.get(field)
        if stamp:
            results.append(Phrase(value=f"{label}: {stamp[:10]}"))

    for field, label in (
        ("negotiation_count", "Negotiation chats"),
        ("ransomnotes_count", "Ransom notes"),
    ):
        count = data.get(field)
        if count:
            results.append(Phrase(value=f"{label}: {count}"))

    description = data.get("description")
    if description:
        results.append(Phrase(value=description[:250], note=description))

    return results


@register_transform(
    display_name="Ransomware.live: Group to Leak Sites",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def group_to_leak_sites(
    input_entity: Malware, settings: dict[str, Any], context: MaltegoContext
) -> list[URL]:
    """Return the known leak site URLs (Tor and clearweb) for a group."""
    data = await _group_detail(input_entity.value, settings, context)
    if data is None:
        return []

    results: list[URL] = []
    for location in data.get("locations", []):
        target = location.get("slug") or location.get("fqdn")
        if not target:
            continue
        site_type = location.get("type") or "leak site"
        available = "available" if location.get("available") else "offline"
        results.append(URL(value=target, note=f"{site_type} ({available})"))

    context.log.inform(f"Found {len(results)} leak site locations")
    return results


@register_transform(
    display_name="Ransomware.live: Group to CVEs",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def group_to_cves(
    input_entity: Malware, settings: dict[str, Any], context: MaltegoContext
) -> list[CVE]:
    """Return the CVEs a ransomware group is known to exploit."""
    data = await _group_detail(input_entity.value, settings, context)
    if data is None:
        return []

    results: list[CVE] = []
    for vulnerability in data.get("vulnerabilities", []):
        cve = vulnerability.get("CVE")
        if not cve:
            continue
        vendor = vulnerability.get("Vendor") or ""
        product = vulnerability.get("Product") or ""
        severity = vulnerability.get("severity") or ""
        score = vulnerability.get("CVSS")
        detail = " ".join(part for part in (vendor, product) if part)
        note = f"{detail} — CVSS {score} ({severity})" if detail else f"CVSS {score} ({severity})"
        results.append(CVE(value=cve, note=note))

    return results


@register_transform(
    display_name="Ransomware.live: Group to TTPs",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def group_to_ttps(
    input_entity: Malware, settings: dict[str, Any], context: MaltegoContext
) -> list[TTP]:
    """Return the MITRE ATT&CK techniques attributed to a ransomware group."""
    data = await _group_detail(input_entity.value, settings, context)
    if data is None:
        return []

    results: list[TTP] = []
    for tactic in data.get("ttps", []):
        tactic_name = tactic.get("tactic_name") or ""
        for technique in tactic.get("techniques", []):
            technique_id = technique.get("technique_id")
            technique_name = technique.get("technique_name") or ""
            if not technique_id:
                continue
            details = technique.get("technique_details") or ""
            note = f"{tactic_name}: {details}" if details else tactic_name
            results.append(TTP(value=f"{technique_id} {technique_name}".strip(), note=note))

    return results


@register_transform(
    display_name="Ransomware.live: Group to Tools",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def group_to_tools(
    input_entity: Malware, settings: dict[str, Any], context: MaltegoContext
) -> list[Phrase]:
    """Return the tools and malware families a ransomware group uses."""
    data = await _group_detail(input_entity.value, settings, context)
    if data is None:
        return []

    # `tools` is a mapping of category -> list of tool names.
    results: list[Phrase] = []
    for category, tools in (data.get("tools") or {}).items():
        for tool in tools:
            results.append(Phrase(value=tool, note=category))

    return results


@register_transform(
    display_name="Ransomware.live: Group to Victims",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def group_to_victims(
    input_entity: Malware, settings: dict[str, Any], context: MaltegoContext
) -> list[Company]:
    """Return organisations listed as victims of a ransomware group."""
    try:
        group = validate_group(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return []

    data = await fetch("/victims/", settings, context, params={"group": group})
    if data is None:
        return []

    results: list[Company] = []
    for raw in data.get("victims", [])[:VICTIM_LIMIT]:
        victim = normalise_victim(raw)
        name = victim["victim"]
        if not name:
            continue
        attributes = [
            f"Attack date: {victim['attackdate'][:10]}" if victim["attackdate"] else "",
            f"Country: {victim.get('country')}" if victim.get("country") else "",
            f"Sector: {victim.get('activity')}" if victim.get("activity") else "",
            f"Website: {victim.get('website')}" if victim.get("website") else "",
        ]
        results.append(Company(value=name, note="\n".join(a for a in attributes if a)))

    total = data.get("count", len(results))
    context.log.inform(f"Returned {len(results)} of {total} victims")
    return results


@register_transform(
    display_name="Ransomware.live: List Groups",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def list_groups(
    input_entity: Phrase, settings: dict[str, Any], context: MaltegoContext
) -> list[Malware]:
    """Return tracked ransomware groups whose name matches the input phrase.

    An empty or `*` input returns every group that has at least one victim.
    """
    data = await fetch("/groups", settings, context)
    if data is None:
        return []

    query = input_entity.value.strip().lower()
    match_all = query in ("", "*")

    results: list[Malware] = []
    for entry in data.get("groups", []):
        name = entry.get("group")
        if not name:
            continue
        altname = entry.get("altname") or ""
        victims = entry.get("victims", 0)
        if match_all:
            if not victims:
                continue
        elif query not in name.lower() and query not in altname.lower():
            continue
        note = f"Victims: {victims}"
        if altname:
            note = f"{note}\nAlso known as: {altname}"
        results.append(Malware(value=name, note=note))

    context.log.inform(f"Matched {len(results)} groups")
    return results
