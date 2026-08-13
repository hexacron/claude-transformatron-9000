"""Shared client helpers for the IPinfo Lite API.

This package is an illustrative example, not a maintained integration. IPinfo is a
third-party service this project has no affiliation with.

Lite is the free tier: it returns ASN, organisation and country-level geolocation
only. There is no city, no coordinates and no privacy/VPN detection — those live in
IPinfo's paid tiers. Every field the API can return is mapped here, so a transform
that wants more detail needs a different endpoint, not a change to this module.

**Configuring the token.** Enter it in Maltego Graph Desktop, in the transform's
settings, under "IPinfo API Token". It is stored once for the whole namespace and
reused by every transform in this set — see :func:`api_token_setting`. The server
needs no token of its own, and none is stored in this repository.

Response shapes were verified against the live API. Two behaviours are not in the
published documentation and are handled in :func:`fetch`:

- A private, reserved or otherwise non-routable address returns ``{"bogon": true}``
  with none of the usual fields present.
- An invalid token returns HTTP 403 rather than 401.
"""

import ipaddress
import os
from typing import Any

from maltego.model.context import MaltegoContext
from maltego.model.exception import MaltegoException, MaltegoHTTPDataProviderNotFound
from maltego.server import TransformSetting
from maltego.util import IntegrationClient

BASE_URL = "https://api.ipinfo.io/lite"
TRANSFORM_SET = "IPinfo"
API_TOKEN = "IPINFO_API_TOKEN"

client = IntegrationClient()


def api_token_setting() -> TransformSetting:
    """Return the shared API token setting declaration.

    This is how the token is meant to be supplied. Declaring it here publishes an
    "IPinfo API Token" field to transform discovery, which Maltego Graph Desktop
    renders in the transform's settings; ``is_global=True`` stores one value for the
    whole namespace, so a user enters it once and every transform in the set reuses
    it. Nothing needs to be configured on the server for this to work.
    """
    return TransformSetting(
        name=API_TOKEN,
        display_name="IPinfo API Token",
        auth=True,
        is_global=True,
    )


def validate_ip(value: str) -> str:
    """Return `value` as a plain IP address string, or raise `ValueError`.

    Accepts IPv4 and IPv6, both of which the Lite API serves. The address is
    interpolated into the upstream URL path, so it is validated rather than trusted.
    """
    return str(ipaddress.ip_address(value.strip()))


async def fetch(
    ip: str, settings: dict[str, Any], context: MaltegoContext
) -> dict[str, Any] | None:
    """Fetch the IPinfo Lite record for `ip`, or None when it is unavailable.

    Returns None rather than raising so a missing token, an upstream outage or a
    non-routable address degrades to an empty graph instead of a failed transform.
    """
    # The Maltego client setting is the intended source and always wins. The
    # environment is a fallback for headless local testing only — it lets the smoke
    # test and the CLI run without a desktop client attached. The server subprocess
    # inherits the parent shell (lifecycle.build_server_env), so a token exported
    # before starting the server survives restarts. Do not rely on it for a shared
    # deployment: it puts the token in the process environment, visible to anyone
    # who can read `ps`.
    token = settings.get(API_TOKEN, "") or os.environ.get(API_TOKEN, "")
    if not token:
        context.log.fatal(
            "No IPinfo API token configured. Enter it in Maltego Graph Desktop under "
            f"the transform's settings ('IPinfo API Token'), or export {API_TOKEN} "
            "before starting the server for headless testing."
        )
        return None

    try:
        response = await client.get(
            f"{BASE_URL}/{ip}",
            context=context,
            params={"token": token},
        )
    except MaltegoHTTPDataProviderNotFound:
        context.log.inform("No IPinfo record for the requested address")
        return None
    except MaltegoException as exc:
        # An invalid or revoked token surfaces here as 403, not 401.
        context.log.fatal(f"IPinfo lookup failed: {exc.message}")
        return None

    data = response.json()
    if not isinstance(data, dict):
        context.log.fatal("IPinfo returned an unexpected response shape")
        return None

    # Private, reserved and other non-routable addresses come back as {"bogon": true}
    # with no ASN or country, which would otherwise read as an empty-but-successful
    # lookup.
    if data.get("bogon"):
        context.log.inform("IPinfo reports this address as a bogon (private or reserved)")
        return None

    return data
