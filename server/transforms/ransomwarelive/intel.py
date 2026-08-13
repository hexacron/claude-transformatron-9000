"""Indicator, detection and response-contact transforms.

Covers the threat-hunting side of the API: IOCs and YARA rules for a group,
ransom notes and negotiation history, and national CSIRT contacts for a country.
"""

from typing import Any

from maltego.entities import (
    URL,
    BTCAddress,
    Country,
    Domain,
    EmailAddress,
    Hash,
    IPv4Address,
    Malware,
    MalwareSignature,
    Phrase,
    Website,
)
from maltego.model.context import MaltegoContext
from maltego.server import register_transform
from transforms.ransomwarelive.api import (
    TRANSFORM_SET,
    api_key_setting,
    fetch,
    validate_country,
    validate_group,
)

# Some groups publish thousands of hashes; keep the graph usable.
IOC_LIMIT = 200

IOCEntity = IPv4Address | Domain | Hash | EmailAddress | BTCAddress | URL


def _ioc_entity(ioc_type: str, value: str) -> IOCEntity | None:
    """Map an IOC type string to the matching Maltego entity, or None if unmapped."""
    if ioc_type == "ip":
        return IPv4Address(value=value)
    if ioc_type == "domain":
        return Domain(value=value)
    if ioc_type in ("md5", "sha1", "sha256"):
        return Hash(value=value, note=ioc_type)
    if ioc_type == "email":
        return EmailAddress(value=value)
    if ioc_type == "btc":
        return BTCAddress(value=value)
    if ioc_type == "url":
        return URL(value=value)
    return None


@register_transform(
    display_name="Ransomware.live: Group to IOCs",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def group_to_iocs(
    input_entity: Malware, settings: dict[str, Any], context: MaltegoContext
) -> list[IPv4Address | Domain | Hash | EmailAddress | BTCAddress | URL]:
    """Return indicators of compromise attributed to a ransomware group."""
    try:
        group = validate_group(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return []

    data = await fetch(f"/iocs/{group}", settings, context)
    if data is None:
        return []

    results: list[IOCEntity] = []
    skipped: set[str] = set()
    # `iocs` maps an indicator type to a list of values.
    for ioc_type, values in (data.get("iocs") or {}).items():
        for value in values:
            entity = _ioc_entity(ioc_type, value)
            if entity is None:
                skipped.add(ioc_type)
                continue
            results.append(entity)
            if len(results) >= IOC_LIMIT:
                context.log.inform(f"Indicator list truncated at {IOC_LIMIT}")
                return results

    if skipped:
        context.log.inform(f"Unmapped IOC types skipped: {', '.join(sorted(skipped))}")
    context.log.inform(f"Returned {len(results)} indicators")
    return results


@register_transform(
    display_name="Ransomware.live: Group to YARA Rules",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def group_to_yara(
    input_entity: Malware, settings: dict[str, Any], context: MaltegoContext
) -> list[MalwareSignature]:
    """Return YARA detection rules published for a ransomware group."""
    try:
        group = validate_group(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return []

    data = await fetch(f"/yara/{group}", settings, context)
    if data is None:
        return []

    results: list[MalwareSignature] = []
    for rule in data.get("rules", []):
        filename = rule.get("filename")
        if not filename:
            continue
        # The full rule text goes in the note so it can be copied out of the client.
        results.append(MalwareSignature(value=filename, note=rule.get("content", "")))

    return results


@register_transform(
    display_name="Ransomware.live: Group to Ransom Notes",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def group_to_ransomnotes(
    input_entity: Malware, settings: dict[str, Any], context: MaltegoContext
) -> list[Phrase]:
    """Return the ransom note identifiers published for a ransomware group."""
    try:
        group = validate_group(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return []

    data = await fetch(f"/ransomnotes/{group}", settings, context)
    if data is None:
        return []

    return [
        Phrase(value=note, note=f"Ransom note published by {group}")
        for note in data.get("ransomnotes", [])
    ]


@register_transform(
    display_name="Ransomware.live: Group to Negotiations",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def group_to_negotiations(
    input_entity: Malware, settings: dict[str, Any], context: MaltegoContext
) -> list[Phrase]:
    """Return negotiation chat summaries with ransom demands and outcomes."""
    try:
        group = validate_group(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return []

    data = await fetch(f"/negotiations/{group}", settings, context)
    if data is None:
        return []

    results: list[Phrase] = []
    for chat in data.get("chats", []):
        chat_id = chat.get("id")
        if not chat_id:
            continue
        initial = chat.get("initialransom") or "N/A"
        negotiated = chat.get("negotiatedransom") or "N/A"
        paid = "paid" if chat.get("paid") else "not paid"
        note = (
            f"Messages: {chat.get('message_count', 0)}\n"
            f"Initial demand: {initial}\n"
            f"Negotiated: {negotiated}\n"
            f"Outcome: {paid}"
        )
        results.append(Phrase(value=f"Negotiation {chat_id} ({paid})", note=note))

    context.log.inform(f"Returned {len(results)} negotiation chats")
    return results


@register_transform(
    display_name="Ransomware.live: Country to CSIRT Contacts",
    transform_set=TRANSFORM_SET,
    settings=[api_key_setting()],
)
async def country_to_csirt(
    input_entity: Country, settings: dict[str, Any], context: MaltegoContext
) -> list[EmailAddress | Website]:
    """Return national CSIRT/CERT incident response contacts for a country."""
    try:
        country = validate_country(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return []

    data = await fetch(f"/csirt/{country}", settings, context)
    if data is None:
        return []

    results: list[EmailAddress | Website] = []
    seen: set[str] = set()
    for team in data.get("results", []):
        name = team.get("team_full") or team.get("team") or ""
        email = team.get("email")
        if email and email not in seen:
            seen.add(email)
            results.append(EmailAddress(value=email, note=name))
        website = team.get("website")
        if website and website not in seen:
            seen.add(website)
            results.append(Website(value=website, note=name))

    context.log.inform(f"Returned contacts for {data.get('count', 0)} teams")
    return results
