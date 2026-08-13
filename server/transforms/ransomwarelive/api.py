"""Shared client helpers for the ransomware.live PRO API.

This package is an illustrative example, not a maintained integration. It is the
authenticated counterpart to ``transforms/examples/ffraud.py``: where that one shows
the minimal shape of a transform, this shows what a real API integration needs — one
credential shared across a transform set, validation and error handling in a single
place, result caps, and a schema whose field names differ between endpoints. Adapt it
or delete it, along with its imports in ``server/project.py``. See
``docs/ransomware-live.md``. ransomware.live is a third-party service this project has
no affiliation with.

Every transform in this package goes through :func:`fetch`, which attaches the
API key header, converts upstream failures into an empty result, and unwraps the
JSON envelope. Response shapes were verified against the live API rather than
taken from the swagger description, which omits per-endpoint field lists.
"""

import os
import re
from typing import Any

from maltego.model.context import MaltegoContext
from maltego.model.exception import MaltegoException, MaltegoHTTPDataProviderNotFound
from maltego.server import TransformSetting
from maltego.util import IntegrationClient

BASE_URL = "https://api-pro.ransomware.live"
TRANSFORM_SET = "ransomware.live"
API_KEY = "RANSOMWARE_LIVE_API_KEY"

client = IntegrationClient()

# Group names are interpolated into the URL path. The API uses lowercase slugs that
# may contain spaces, dots, underscores and hyphens (e.g. "0day syndicate", "alp-001").
_GROUP_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,64}$")

# ISO 3166-1 alpha-2 or alpha-3; the /csirt endpoint accepts either.
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2,3}$")


def api_key_setting() -> TransformSetting:
    """Return the shared API key setting declaration.

    Declared on every transform in this set so the key is entered once in the
    Maltego client and reused across the whole set.
    """
    return TransformSetting(
        name=API_KEY,
        display_name="Ransomware.live API Key",
        auth=True,
        is_global=True,
    )


def validate_group(value: str) -> str:
    """Return `value` as a safe group slug, or raise `ValueError`.

    The slug is interpolated into the upstream URL path, so it is validated
    rather than trusted.
    """
    group = value.strip()
    if not _GROUP_RE.match(group):
        raise ValueError("expected a ransomware group name")
    return group


def validate_country(value: str) -> str:
    """Return `value` as an ISO 3166-1 alpha-2/alpha-3 code, or raise `ValueError`."""
    country = value.strip()
    if not _COUNTRY_RE.match(country):
        raise ValueError("expected an ISO 3166-1 country code, e.g. US or USA")
    return country.upper()


async def fetch(
    path: str,
    settings: dict[str, Any],
    context: MaltegoContext,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Fetch `path` from the API, or None when it is unavailable.

    Returns None rather than raising so an upstream outage or a missing record
    degrades to an empty graph instead of a failed transform.
    """
    # The client setting wins. The environment is a development fallback: the server
    # subprocess inherits the parent shell (lifecycle.build_server_env), so a key
    # exported once survives restarts and seed re-imports, which otherwise orphan the
    # value stored in the Maltego client and leave it resolving empty.
    api_key = settings.get(API_KEY, "") or os.environ.get(API_KEY, "")
    if not api_key:
        context.log.fatal(
            "No ransomware.live API key configured. Set it in the transform settings, "
            f"or export {API_KEY} before starting the server."
        )
        return None

    try:
        response = await client.get(
            f"{BASE_URL}{path}",
            context=context,
            headers={"X-API-KEY": api_key},
            params=params,
        )
    except MaltegoHTTPDataProviderNotFound:
        context.log.inform("No ransomware.live record for the requested input")
        return None
    except MaltegoException as exc:
        context.log.fatal(f"ransomware.live lookup failed: {exc.message}")
        return None

    data = response.json()
    if not isinstance(data, dict):
        context.log.fatal("ransomware.live returned an unexpected response shape")
        return None
    return data


def normalise_victim(victim: dict[str, Any]) -> dict[str, Any]:
    """Return a victim record with consistent field names.

    The API is mid-rename: `/victims/recent` and `/victims/` return `victim`,
    `group` and `attackdate`, while `/victims/search` still returns the legacy
    `post_title`, `group_name` and `published`. Both shapes appear in live
    responses, so read the new name first and fall back to the old one.
    """
    return {
        **victim,
        "victim": victim.get("victim") or victim.get("post_title") or "",
        "group": victim.get("group") or victim.get("group_name") or "",
        "attackdate": victim.get("attackdate") or victim.get("published") or "",
    }
