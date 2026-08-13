"""Shared client helpers for the CrowdSec CTI API.

This package is an illustrative example, not a maintained integration. CrowdSec is a
third-party service this project has no affiliation with.

Built against the v2 spec at https://crowdsecurity.github.io/cti-api/. Only
``/smoke/{ip}`` is used — the full CTI record for one address.

``/smoke/search`` and ``/fire`` are deliberately not wired up. Both are Partner-tier:
a community key gets HTTP 403 with ``{"message": "Forbidden"}``, verified against the
live API. Shipping them would give most users transforms that cannot work.

**Configuring the key.** Enter it in Maltego Graph Desktop, in the transform's settings,
under "CrowdSec CTI API Key". It is stored once for the whole namespace and reused by
every transform in this set — see :func:`api_key_setting`. The server needs no key of
its own, and none is stored in this repository.

Community keys are rate limited.
"""

import ipaddress
import os
from typing import Any

from maltego.model.context import MaltegoContext
from maltego.model.exception import MaltegoException, MaltegoHTTPDataProviderNotFound
from maltego.server import TransformSetting
from maltego.util import IntegrationClient

BASE_URL = "https://cti.api.crowdsec.net/v2"
TRANSFORM_SET = "CrowdSec"
API_KEY = "CROWDSEC_CTI_API_KEY"


# Caps on the list fields of a single CTI record. A busy address can carry dozens of
# scenarios and target countries; past the first handful they stop informing a graph.
MAX_ITEMS = 20

client = IntegrationClient()


def api_key_setting() -> TransformSetting:
    """Return the shared API key setting declaration.

    This is how the key is meant to be supplied. Declaring it here publishes a
    "CrowdSec CTI API Key" field to transform discovery, which Maltego Graph Desktop
    renders in the transform's settings; ``is_global=True`` stores one value for the
    whole namespace, so a user enters it once and every transform in the set reuses
    it. Nothing needs to be configured on the server for this to work.
    """
    return TransformSetting(
        name=API_KEY,
        display_name="CrowdSec CTI API Key",
        auth=True,
        is_global=True,
    )


def validate_ip(value: str) -> str:
    """Return `value` as a plain IP address string, or raise `ValueError`.

    Accepts IPv4 and IPv6. The address is interpolated into the upstream URL path,
    so it is validated rather than trusted.
    """
    return str(ipaddress.ip_address(value.strip()))


async def fetch(
    path: str,
    settings: dict[str, Any],
    context: MaltegoContext,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Fetch `path` from the CTI API, or None when it is unavailable.

    Returns None rather than raising so a missing key, an unknown address or a rate
    limit degrades to an empty graph instead of a failed transform.
    """
    # The Maltego client setting is the intended source and always wins. The
    # environment is a fallback for headless local testing only — it lets the smoke
    # test and the CLI run without a desktop client attached. The server subprocess
    # inherits the parent shell (lifecycle.build_server_env), so a key exported
    # before starting the server survives restarts. Do not rely on it for a shared
    # deployment: it puts the key in the process environment, visible to anyone who
    # can read `ps`.
    api_key = settings.get(API_KEY, "") or os.environ.get(API_KEY, "")
    if not api_key:
        context.log.fatal(
            "No CrowdSec CTI API key configured. Enter it in Maltego Graph Desktop under "
            f"the transform's settings ('CrowdSec CTI API Key'), or export {API_KEY} "
            "before starting the server for headless testing."
        )
        return None

    try:
        response = await client.get(
            f"{BASE_URL}{path}",
            context=context,
            headers={"x-api-key": api_key},
            params=params,
        )
    except MaltegoHTTPDataProviderNotFound:
        # /smoke/{ip} returns 404 for an address CrowdSec has never seen reported,
        # which is a legitimate answer rather than an error.
        context.log.inform("CrowdSec has no CTI record for the requested input")
        return None
    except MaltegoException as exc:
        # 403 covers both an invalid key and a community key reaching a Partner-tier
        # endpoint; 429 is the rate limit.
        context.log.fatal(f"CrowdSec CTI lookup failed: {exc.message}")
        return None

    data = response.json()
    if not isinstance(data, dict):
        context.log.fatal("CrowdSec CTI returned an unexpected response shape")
        return None
    return data


def named_labels(items: Any, limit: int = MAX_ITEMS) -> list[str]:
    """Return the human-readable labels from a CTI list-of-objects field.

    ``behaviors``, ``classifications``, ``attack_details``, ``mitre_techniques`` and
    ``references`` all share a ``{name, label, description}`` shape. The label is the
    display text; it falls back to the name, which is always present.
    """
    if not isinstance(items, list):
        return []

    labels: list[str] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        label = item.get("label") or item.get("name")
        if label:
            labels.append(str(label))
    return labels
