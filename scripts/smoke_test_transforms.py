"""Run every registered transform and fail on the silent-failure cases.

A transform can report ``COMPLETED (success)`` while producing nothing — returning a
``MaltegoGraph`` from an async transform does exactly that, because the SDK drops it.
Transport-level checks (HTTP 200, a run id) do not catch this. This script does, by
asserting that a successful run actually produced entities.

It also flags transforms whose declared output type is empty, which stops the Maltego
client from routing them onto result entities.

Usage:
    uv run python scripts/smoke_test_transforms.py
    uv run python scripts/smoke_test_transforms.py --value 1.1.1.1
    uv run python scripts/smoke_test_transforms.py --transform <fully.qualified.id>
    uv run python scripts/smoke_test_transforms.py --setting API_KEY=xxx

Transforms that need credentials take them through repeated ``--setting KEY=VALUE``,
the same form the CLI uses. A transform that reports a missing setting is recorded as
SKIP rather than FAIL, so an unconfigured credential is never mistaken for broken code
— pass its setting to actually exercise it.

The server must already be running. Transforms calling third-party APIs make live
network requests, so a failure here can mean an upstream outage rather than broken
transform code — check the reported message before assuming the latter.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Any

from transformatron import lifecycle
from transformatron.client import TransformClient, TransformServerError
from transformatron.config import load_config

# Sample inputs by entity type. Only types with an unambiguous, publicly routable
# sample belong here; anything else is skipped rather than guessed at, so a skip
# never masks itself as a pass.
SAMPLE_VALUES = {
    "maltego.IPv4Address": "8.8.8.8",
    "maltego.IPv6Address": "2001:4860:4860::8888",
    "maltego.Domain": "example.com",
    "maltego.DNSName": "dns.google",
    "maltego.EmailAddress": "test@example.com",
    "maltego.URL": "https://example.com",
    "maltego.Website": "example.com",
    "maltego.Phrase": "example",
    "maltego.AS": "15169",
    "maltego.Netblock": "8.8.8.0/24",
    # Threat-intelligence inputs. A ransomware group name is the closest thing to an
    # unambiguous Malware sample; "lockbit3" is long-established across data sources.
    "maltego.Malware": "lockbit3",
    "maltego.Company": "hospital",
    "maltego.Country": "US",
}

# Per-transform input overrides, matched against the end of the transform id.
#
# One sample per entity type cannot suit every transform: a `Company` sample that
# exercises a search is a substring, while a lookup needs a full organisation name,
# and not every ransomware group has every kind of intelligence attached. Without
# these, a transform that works correctly reports FAIL on an unsuitable sample —
# which trains people to ignore the gate.
TRANSFORM_SAMPLES = {
    # These need a group with published TTPs and CVEs; lockbit3 has neither.
    "group_to_cves": "akira",
    "group_to_ttps": "akira",
    # Matches group names as a substring, so the generic Phrase sample finds nothing.
    "list_groups": "lock",
}

# A transform reporting one of these is unconfigured, not broken. Matched
# case-insensitively against the run's status messages.
MISSING_SETTING_MARKERS = (
    "api key configured",
    "no api key",
    "api token configured",
    "missing setting",
)

# A transform reporting one of these ran correctly but had nothing to return for the
# sample input. Distinct from a silent failure, which produces no message at all.
NO_MATCH_MARKERS = (
    "no exact victim match",
    "no press coverage",
    "no leak site listing",
    # A private or reserved address has no public routing data to return.
    "bogon",
)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass
class Outcome:
    """Result of smoke-testing one transform."""

    transform_id: str
    status: str
    detail: str


def _type_ids(spec: Any) -> list[str]:
    """Return the entity type ids from a discovery input/output spec."""
    if not isinstance(spec, dict):
        return []
    type_ids = spec.get("typeIds")
    return [str(t) for t in type_ids] if isinstance(type_ids, list) else []


def _transform_sample(transform_id: str) -> str | None:
    """Return the per-transform input override for `transform_id`, if one is defined."""
    for suffix, value in TRANSFORM_SAMPLES.items():
        if transform_id.endswith(f".{suffix}") or transform_id == suffix:
            return value
    return None


def _pick_input(transform: dict[str, Any], override: str | None) -> tuple[str, str] | None:
    """Return the (entity_type, value) to exercise a transform, or None if unknown."""
    transform_id = str(transform.get("name", ""))
    for type_id in _type_ids(transform.get("input")):
        if override is not None:
            return type_id, override
        sample = _transform_sample(transform_id) or SAMPLE_VALUES.get(type_id)
        if sample is not None:
            return type_id, sample
    return None


def _matches(messages: list[str], markers: tuple[str, ...]) -> bool:
    """Return True when any marker appears in the run's status messages."""
    joined = " ".join(messages).lower()
    return any(marker in joined for marker in markers)


async def check_transform(
    client: TransformClient,
    transform: dict[str, Any],
    override: str | None,
    timeout: float,
    settings: dict[str, str] | None = None,
) -> Outcome:
    """Run one transform and classify the result."""
    transform_id = str(transform.get("name", "<unnamed>"))

    if not _type_ids(transform.get("output")):
        return Outcome(
            transform_id,
            FAIL,
            "declares no output type — add a typed return annotation (a bare '-> list' "
            "advertises nothing and breaks client routing)",
        )

    chosen = _pick_input(transform, override)
    if chosen is None:
        inputs = ", ".join(_type_ids(transform.get("input"))) or "none declared"
        return Outcome(transform_id, SKIP, f"no sample value for input type ({inputs})")

    entity_type, value = chosen
    try:
        result = await client.run_transform(transform_id, entity_type, value, settings, timeout)
    except TransformServerError as exc:
        return Outcome(transform_id, FAIL, f"run failed: {exc}")

    messages = "; ".join(result.messages) if result.messages else "no messages"
    if not result.succeeded:
        return Outcome(transform_id, FAIL, f"state {result.state} ({messages})")
    if not result.entities:
        # An unconfigured credential is a gap in this run, not a defect in the
        # transform. Reporting it as FAIL would train people to ignore failures.
        if _matches(result.messages, MISSING_SETTING_MARKERS):
            return Outcome(
                transform_id,
                SKIP,
                f"needs a credential — pass it with --setting to exercise this ({messages})",
            )
        # The transform ran and said, explicitly, that this input has no results.
        # That is honest behaviour, not the silent empty return this gate hunts for.
        if _matches(result.messages, NO_MATCH_MARKERS):
            return Outcome(
                transform_id,
                SKIP,
                f"no upstream match for the sample input ({messages}) — "
                f"add a TRANSFORM_SAMPLES entry to exercise it",
            )
        return Outcome(
            transform_id,
            FAIL,
            f"reported {result.state} but returned no entities for {entity_type}={value} "
            f"({messages}) — if it returns a MaltegoGraph, return a list instead",
        )
    return Outcome(transform_id, PASS, f"{len(result.entities)} entities from {value}")


def parse_settings(pairs: list[str] | None) -> dict[str, str] | None:
    """Parse repeated ``KEY=VALUE`` arguments into a settings mapping."""
    if not pairs:
        return None
    settings: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator:
            raise SystemExit(f"Invalid --setting {pair!r}: expected KEY=VALUE")
        settings[key] = value
    return settings


async def run(
    override: str | None,
    only: str | None,
    timeout: float,
    settings: dict[str, str] | None = None,
) -> int:
    """Smoke-test the registered transforms and return a process exit code."""
    config = lifecycle.resolve_config(load_config())
    client = TransformClient(config)

    try:
        transforms = await client.list_transforms()
    except TransformServerError as exc:
        print(f"Could not reach the server at {config.base_url}: {exc}", file=sys.stderr)
        print("Start it first (server_start), then re-run.", file=sys.stderr)
        return 2

    if only is not None:
        transforms = [t for t in transforms if t.get("name") == only]
        if not transforms:
            print(f"No transform named {only}.", file=sys.stderr)
            return 2

    if not transforms:
        print("The server advertises no transforms — nothing to check.")
        return 0

    print(f"Checking {len(transforms)} transforms against {config.base_url}\n")
    outcomes = [await check_transform(client, t, override, timeout, settings) for t in transforms]

    for outcome in outcomes:
        print(f"  {outcome.status:<4} {outcome.transform_id}\n       {outcome.detail}")

    failed = [o for o in outcomes if o.status == FAIL]
    skipped = [o for o in outcomes if o.status == SKIP]
    passed = [o for o in outcomes if o.status == PASS]
    print(f"\n{len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--value", help="Input value to use instead of the built-in samples")
    parser.add_argument("--transform", help="Check only this fully qualified transform id")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-run timeout in seconds")
    parser.add_argument(
        "--setting",
        action="append",
        metavar="KEY=VALUE",
        help="Transform setting, e.g. an API key. Repeatable.",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.value, args.transform, args.timeout, parse_settings(args.setting)))


if __name__ == "__main__":
    raise SystemExit(main())
