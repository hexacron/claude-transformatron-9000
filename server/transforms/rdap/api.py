"""Shared client for RDAP, the registration data protocol that replaced WHOIS.

RDAP needs no API key and no registration, so these transforms work on a fresh clone.
Registry data is authoritative rather than scraped: it is the same source WHOIS served,
in a documented JSON schema (RFC 9083) instead of free text nobody could parse reliably.

Bootstrapping is the one wrinkle. `rdap.org` does not hold data; it answers a lookup with
a 302 to whichever registry is authoritative for that TLD (`.com` to Verisign, `.uk` to
Nominet, and so on). The SDK's IntegrationClient sets `follow_redirects=False` on purpose
— silently following a redirect would send the caller's headers to a host the transform
never chose, which is an SSRF vector — so the hop is followed here, once, explicitly, and
only to an https URL.

Doing that takes one non-obvious step: the client returns only 2xx responses and raises on
everything else, so the 302 arrives as a `MaltegoHTTPDataProviderInvalidResponse` rather
than as a response to inspect. The redirect target has to be read from the response
attached to that exception. A transform that simply catches and logs the error, which is
the natural thing to write, returns zero entities and still reports success.
"""

from typing import Any

from maltego.model.context import MaltegoContext
from maltego.model.exception import (
    MaltegoException,
    MaltegoHTTPDataProviderInvalidResponse,
    MaltegoHTTPDataProviderNotFound,
)
from maltego.util import IntegrationClient

BOOTSTRAP_URL = "https://rdap.org/domain/{domain}"

# Registries answer with this media type. Asking for it keeps a misconfigured host from
# returning an HTML error page that json() would then fail on.
RDAP_HEADERS = {"Accept": "application/rdap+json"}

client = IntegrationClient()


def validate_domain(value: str) -> str:
    """Return ``value`` as a bare domain name, or raise ``ValueError``.

    The name is interpolated into the upstream URL path, so it is validated rather than
    trusted. RDAP wants the registrable domain: no scheme, no path, no leading dot.

    Args:
        value: Raw entity value, e.g. ``"example.com"``.

    Returns:
        The normalised, lowercased domain.

    Raises:
        ValueError: If the value is empty, carries a scheme or path, or has no dot.
    """
    domain = value.strip().lower().rstrip(".")
    if not domain:
        raise ValueError("expected a domain name, got an empty value")
    if "/" in domain or ":" in domain:
        raise ValueError(f"expected a bare domain name, got {value!r}")
    if "." not in domain:
        raise ValueError(f"expected a domain with a TLD, got {domain!r}")
    return domain


REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


def _redirect_target(error: MaltegoHTTPDataProviderInvalidResponse) -> str | None:
    """Return the https redirect target carried by ``error``, or None.

    Returns None for anything that is not a redirect to an https URL, so the caller
    re-raises rather than following it. Restricting the scheme keeps a compromised or
    misconfigured bootstrap service from steering the lookup at an arbitrary host.
    """
    response = getattr(error, "response", None)
    if response is None or response.status_code not in REDIRECT_CODES:
        return None
    location = response.headers.get("location", "")
    return location if location.startswith("https://") else None


async def fetch_domain(domain: str, context: MaltegoContext) -> dict[str, Any] | None:
    """Return the RDAP record for ``domain``, or None when it is unavailable.

    Follows the bootstrap redirect once, by hand. See the module docstring for why the
    client does not do it automatically.

    Args:
        domain: A domain name already through ``validate_domain``.
        context: Transform context, used for logging and the outbound client.

    Returns:
        The parsed RDAP object, or None if the domain is unregistered or the lookup
        failed. Both cases are logged.
    """
    url = BOOTSTRAP_URL.format(domain=domain)
    try:
        try:
            response = await client.get(url, context=context, headers=RDAP_HEADERS)
        except MaltegoHTTPDataProviderInvalidResponse as redirect:
            # The client returns only 2xx and raises on everything else, so the bootstrap
            # 302 arrives as an exception rather than a response. The response is attached
            # to it, which is where the Location header has to be read from.
            location = _redirect_target(redirect)
            if location is None:
                raise
            response = await client.get(location, context=context, headers=RDAP_HEADERS)
    except MaltegoHTTPDataProviderNotFound:
        # A 404 from RDAP is a real answer: no registry holds this name.
        context.log.inform(f"No registration record for {domain} — the domain is unregistered")
        return None
    except MaltegoException as exc:
        context.log.fatal(f"RDAP lookup for {domain} failed: {exc.message}")
        return None

    try:
        record = response.json()
    except ValueError:
        context.log.fatal(f"RDAP returned a non-JSON response for {domain}")
        return None

    if not isinstance(record, dict):
        context.log.fatal(f"RDAP returned an unexpected payload for {domain}")
        return None
    return record


def vcard_field(entity: dict[str, Any], field: str) -> str | None:
    """Pull one field out of an RDAP entity's jCard.

    Contact details arrive as jCard (RFC 7095): ``vcardArray`` is the two-element list
    ``["vcard", [[name, params, type, value], ...]]``. The value is usually a string but
    is a list for structured fields such as ``adr``, so only strings are returned here.

    Args:
        entity: An RDAP entity object.
        field: The jCard property to read, e.g. ``"fn"`` or ``"email"``.

    Returns:
        The field value, or None if the entity carries no usable jCard entry for it.
    """
    vcard = entity.get("vcardArray")
    if not isinstance(vcard, list) or len(vcard) < 2 or not isinstance(vcard[1], list):
        return None
    for row in vcard[1]:
        if isinstance(row, list) and len(row) >= 4 and row[0] == field:
            value = row[3]
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None
