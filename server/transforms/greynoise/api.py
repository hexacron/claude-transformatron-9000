"""Shared client helpers for the GreyNoise Community API.

This package is an illustrative example, not a maintained integration. GreyNoise is a
third-party service this project has no affiliation with.

Only ``/v3/community/{ip}`` is used — the free tier's single-address lookup. It answers
two separate questions about an address:

- **noise**: has this address been observed opportunistically scanning the internet?
- **riot**: is it a known benign service (a CDN edge, a public resolver, a SaaS range)
  that an analyst can rule out?

The paid Enterprise endpoints (``/v2/experimental/gnql``, ``/v2/noise/context``) carry
the scan detail — ports, tags, JA3 fingerprints, raw scan data. None of it is available
on a Community key, so it is deliberately not wired up here.

**Configuring the key.** Enter it in Maltego Graph Desktop, in the transform's settings,
under "GreyNoise API Key". It is stored once for the whole namespace and reused by every
transform in this set — see :func:`api_key_setting`. The server needs no key of its own,
and none is stored in this repository.

Response shapes were verified against the live API on 2026-08-13. Three behaviours are
not obvious from the published documentation and are handled in :func:`fetch`:

- **HTTP 404 is a legitimate answer, not an error.** An address GreyNoise has never
  observed returns 404 with ``{"noise": false, "riot": false, "message": "IP not
  observed scanning the internet."}``. That is a real verdict — "this address is not a
  known scanner" — so it is returned as data rather than swallowed as a miss.
- **The enriched fields appear only on a hit.** ``classification``, ``name``, ``link``
  and ``last_seen`` are present when ``noise`` or ``riot`` is true and absent otherwise,
  so every read of them goes through ``.get()``.
- **IPv4 only.** IPv6 and non-routable addresses return HTTP 400. The input is
  restricted to IPv4 rather than letting the upstream reject it.

**The key header is not enforced on this endpoint.** Verified 2026-08-13: requests with
no key, and with a deliberately malformed one, both return the same 200 or 404 as a
valid key. The setting is still declared and sent — it is what the tier is documented to
want, and it is how quota is attributed — but do not read a successful lookup as proof
that a configured key is valid. The RIOT branch has not been observed firing on this
tier; every known benign provider tried (8.8.8.8, 1.1.1.1, 8.8.4.4, OpenDNS) answered
404 "not observed". If RIOT data never materialises with a valid key, that branch is
dead code on Community and belongs in an Enterprise-tier transform instead.
"""

import ipaddress
import os
from typing import Any

from maltego.model.context import MaltegoContext
from maltego.model.exception import MaltegoException, MaltegoHTTPDataProviderNotFound
from maltego.server import TransformSetting
from maltego.util import IntegrationClient

BASE_URL = "https://api.greynoise.io/v3/community"
TRANSFORM_SET = "GreyNoise"
API_KEY = "GREYNOISE_API_KEY"

client = IntegrationClient()


def api_key_setting() -> TransformSetting:
    """Return the shared API key setting declaration.

    This is how the key is meant to be supplied. Declaring it here publishes a
    "GreyNoise API Key" field to transform discovery, which Maltego Graph Desktop
    renders in the transform's settings; ``is_global=True`` stores one value for the
    whole namespace, so a user enters it once and every transform in the set reuses it.
    Nothing needs to be configured on the server for this to work.
    """
    return TransformSetting(
        name=API_KEY,
        display_name="GreyNoise API Key",
        auth=True,
        is_global=True,
    )


def validate_ipv4(value: str) -> str:
    """Return `value` as a plain IPv4 address string, or raise `ValueError`.

    The Community endpoint serves IPv4 only — IPv6 is rejected upstream with HTTP 400 —
    so the address is checked here rather than spending a request to be told no. It is
    also interpolated into the upstream URL path, so it is validated rather than trusted.
    """
    address = ipaddress.ip_address(value.strip())
    if not isinstance(address, ipaddress.IPv4Address):
        raise ValueError(f"GreyNoise Community serves IPv4 only, got {address}")
    return str(address)


async def fetch(
    ip: str, settings: dict[str, Any], context: MaltegoContext
) -> dict[str, Any] | None:
    """Fetch the GreyNoise Community record for `ip`, or None when it is unavailable.

    Returns None rather than raising so a missing key, an upstream outage or a rate
    limit degrades to an empty graph instead of a failed transform. An address GreyNoise
    has not observed is **not** one of those cases — it comes back as a populated record
    with ``noise`` and ``riot`` both false, because "not a known scanner" is an answer
    worth putting on the graph.
    """
    # The Maltego client setting is the intended source and always wins. The environment
    # is a fallback for headless local testing only — it lets the smoke test and the CLI
    # run without a desktop client attached. The server subprocess inherits the parent
    # shell (lifecycle.build_server_env), so a key exported before starting the server
    # survives restarts. Do not rely on it for a shared deployment: it puts the key in
    # the process environment, visible to anyone who can read `ps`.
    api_key = settings.get(API_KEY, "") or os.environ.get(API_KEY, "")
    if not api_key:
        context.log.fatal(
            "No GreyNoise API key configured. Enter it in Maltego Graph Desktop under "
            f"the transform's settings ('GreyNoise API Key'), or export {API_KEY} "
            "before starting the server for headless testing."
        )
        return None

    try:
        response = await client.get(
            f"{BASE_URL}/{ip}",
            context=context,
            headers={"key": api_key},
        )
    except MaltegoHTTPDataProviderNotFound:
        # 404 means "never observed scanning", which is a verdict rather than a failure.
        # The body carries the same {noise, riot, message} shape as a hit, but the SDK
        # raises before it reaches us, so the equivalent record is reconstructed here.
        return {
            "ip": ip,
            "noise": False,
            "riot": False,
            "message": "IP not observed scanning the internet.",
        }
    except MaltegoException as exc:
        # Mainly the Community-tier rate limit (429). Verified on 2026-08-13: an invalid
        # key does *not* land here — the endpoint ignores the key header on this tier and
        # answers 200 regardless, so there is no auth-failure path to catch. A revoked
        # key is untested and may behave differently.
        context.log.fatal(f"GreyNoise lookup failed: {exc.message}")
        return None

    data = response.json()
    if not isinstance(data, dict):
        context.log.fatal("GreyNoise returned an unexpected response shape")
        return None
    return data
