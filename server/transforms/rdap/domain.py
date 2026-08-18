"""Domain registration transforms backed by RDAP.

These need no API key, so they run on a fresh clone and are what
``scripts/smoke_test_transforms.py`` exercises end to end without configuration.

RDAP is the IETF replacement for WHOIS (RFC 9083) and is served by the registries
themselves, so the data is authoritative. Each transform below turns one part of the
record into entities an investigator can pivot from — nameservers to their hosts, the
registrar to its abuse contacts — rather than a wall of text.
"""

from typing import Any

from maltego.entities import DNSName, Domain, EmailAddress, PhoneNumber, Phrase
from maltego.model.context import MaltegoContext
from maltego.server import register_transform
from transforms.rdap.api import fetch_domain, validate_domain, vcard_field

TRANSFORM_SET = "rdap"

# Registry status codes worth surfacing verbatim: each one explains why a domain cannot
# be transferred, updated, or resolved. The rest are routine and add noise.
NOTABLE_STATUSES = (
    "client hold",
    "server hold",
    "pending delete",
    "redemption period",
    "pending transfer",
)


def _events(record: dict[str, Any]) -> dict[str, str]:
    """Map an RDAP record's events to ``{action: date}``, dates trimmed to the day."""
    events: dict[str, str] = {}
    for event in record.get("events", []):
        action = event.get("eventAction")
        date = event.get("eventDate")
        if isinstance(action, str) and isinstance(date, str):
            events[action] = date[:10]
    return events


def _entities_with_role(record: dict[str, Any], role: str) -> list[dict[str, Any]]:
    """Return the RDAP entities carrying ``role``, searching one level of nesting.

    Abuse contacts sit inside the registrar entity rather than at the top level, so a
    flat scan of ``record["entities"]`` misses them.
    """
    found: list[dict[str, Any]] = []
    for entity in record.get("entities", []):
        if not isinstance(entity, dict):
            continue
        if role in (entity.get("roles") or []):
            found.append(entity)
        for nested in entity.get("entities", []):
            if isinstance(nested, dict) and role in (nested.get("roles") or []):
                found.append(nested)
    return found


@register_transform(display_name="RDAP: Domain to Registration", transform_set=TRANSFORM_SET)
async def rdap_domain_to_registration(
    input_entity: Domain, context: MaltegoContext
) -> list[Phrase]:
    """Return registration, expiry, and status for a domain.

    Registration and expiry dates are the two facts most often wanted first: a domain
    registered days ago behaves very differently in an investigation from one registered
    in 1995.
    """
    try:
        domain = validate_domain(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return []

    record = await fetch_domain(domain, context)
    if record is None:
        return []

    results: list[Phrase] = []
    events = _events(record)
    for action, label in (
        ("registration", "Registered"),
        ("expiration", "Expires"),
        ("last changed", "Last changed"),
    ):
        if action in events:
            results.append(Phrase(value=f"{label}: {events[action]}"))

    statuses = [s for s in record.get("status", []) if isinstance(s, str)]
    notable = [s for s in statuses if s.lower() in NOTABLE_STATUSES]
    if notable:
        results.append(Phrase(value=f"Status: {', '.join(notable)}"))

    for registrar in _entities_with_role(record, "registrar"):
        name = vcard_field(registrar, "fn")
        if name:
            results.append(Phrase(value=f"Registrar: {name}"))

    if not results:
        context.log.inform(f"{domain} is registered but the registry published no details")
    return results


@register_transform(display_name="RDAP: Domain to Nameservers", transform_set=TRANSFORM_SET)
async def rdap_domain_to_nameservers(
    input_entity: Domain, context: MaltegoContext
) -> list[DNSName]:
    """Return the delegated nameservers for a domain.

    Nameservers are the most useful pivot in a registration record: shared nameservers
    across otherwise unrelated domains are a common way to group infrastructure.
    """
    try:
        domain = validate_domain(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return []

    record = await fetch_domain(domain, context)
    if record is None:
        return []

    names = []
    for nameserver in record.get("nameservers", []):
        if not isinstance(nameserver, dict):
            continue
        host = nameserver.get("ldhName")
        if isinstance(host, str) and host.strip():
            names.append(host.strip().lower())

    if not names:
        context.log.inform(
            f"{domain} has no delegated nameservers — it may be registered but unused"
        )
    return [DNSName(value=name) for name in sorted(set(names))]


@register_transform(display_name="RDAP: Domain to Abuse Contact", transform_set=TRANSFORM_SET)
async def rdap_domain_to_abuse_contact(
    input_entity: Domain, context: MaltegoContext
) -> list[EmailAddress | PhoneNumber]:
    """Return the registrar's abuse contacts for a domain.

    This is the address a takedown or abuse report goes to. It sits inside the registrar
    entity rather than at the top of the record, which is why it is easy to miss.
    """
    try:
        domain = validate_domain(input_entity.value)
    except ValueError as exc:
        context.log.fatal(f"Invalid input: {exc}")
        return []

    record = await fetch_domain(domain, context)
    if record is None:
        return []

    results: list[EmailAddress | PhoneNumber] = []
    for contact in _entities_with_role(record, "abuse"):
        email = vcard_field(contact, "email")
        if email:
            results.append(EmailAddress(value=email))
        phone = vcard_field(contact, "tel")
        if phone:
            # jCard carries telephone numbers as a tel: URI; the entity wants the number.
            results.append(PhoneNumber(value=phone.removeprefix("tel:")))

    if not results:
        context.log.inform(f"The registrar for {domain} publishes no abuse contact")
    return results
