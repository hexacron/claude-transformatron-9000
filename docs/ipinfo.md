# IPinfo transforms

Three transforms over the [IPinfo Lite API](https://ipinfo.io/developers/lite-api), covering ASN,
network operator, and country-level geolocation for IPv4 and IPv6 addresses.

The code lives in `server/transforms/ipinfo/`:

| Module | Contents |
|--------|----------|
| `api.py` | Shared client: token setting, input validation, error handling, bogon detection |
| `lookup.py` | IP to Network, IPv6 to Network, IP to Location |

## Getting a token

IPinfo Lite is free. Sign up at [ipinfo.io](https://ipinfo.io/signup) and copy the token from
[your account's token page](https://ipinfo.io/account/token). Lite has no daily or monthly request
limit.

## Configuring the token in Maltego Graph Desktop

**This is the intended way to supply the token.** Nothing is configured on the server, and no
token is stored in this repository.

1. Start the server and import the seed URL into Desktop, if you have not already —
   see the README's setup section.
2. In Desktop, open the **Transforms** tab and find the **Transform Manager**.
3. Select any transform in the **IPinfo** set (for example *IPinfo: IP to Network*).
4. In its settings, fill in **IPinfo API Token** and apply.

The setting is declared `is_global=True`, so Desktop stores one value for the whole namespace:
enter it once and all three transforms pick it up. With no token the transforms return nothing and
log `No IPinfo API token configured` rather than failing opaquely.

> **If the field looks filled but the transform still reports no token,** clear it, apply, and
> re-enter it. Re-importing the seed — which happens when the server switches between HTTP and
> HTTPS — can orphan the stored global value. See the re-import trap in
> `docs/transform-authoring.md`.

### Headless testing

For CLI and smoke-test runs with no desktop client attached, pass the token per invocation:

```bash
uv run python scripts/transformatron_cli.py run \
  acme.new_maltego_integration.ipinfo_ip_to_network maltego.IPv4Address 8.8.8.8 \
  --setting IPINFO_API_TOKEN=<your-token>
```

`api.py` also falls back to the `IPINFO_API_TOKEN` environment variable when the client setting is
empty; the server subprocess inherits the parent shell, so a token exported before starting the
server survives restarts. The client setting always wins. **This is for local testing only** — it
puts the token in the process environment, where anyone who can read `ps` can see it.

The token is sent as an `Authorization: Bearer` header, never as the `?token=` query parameter
IPinfo also accepts: the server logs every outbound request URL, so a query-string token would be
written to `server.log`.

## Transforms

| Transform | Input | Output |
|-----------|-------|--------|
| IPinfo: IP to Network | `IPv4Address` | `AS`, `ISP`, `Domain` |
| IPinfo: IPv6 to Network | `IPv6Address` | `AS`, `ISP`, `Domain` |
| IPinfo: IP to Location | `IPv4Address` | `Country`, `Location` |

The ASN arrives as `AS15169`; the `AS` entity carries the bare number (`15169`). `as_name` becomes
an `ISP` and `as_domain` a `Domain`, which is the useful pivot — it takes you from an address to
the operator's domain and onward through the standard DNS transforms.

## What Lite does not return

Lite is country-level only. There is **no city, no coordinates, and no VPN/proxy/hosting
detection** — those are IPinfo's paid tiers. The `Location` entity therefore carries the continent
rather than a place. Every field the API returns is already mapped, so more detail means a
different endpoint, not a change to this module.

## Two undocumented API behaviours

Both were found by probing the live API, and both are handled in `api.py`:

- **Private and reserved addresses return `{"bogon": true}`** with none of the usual fields. Left
  unhandled this reads as a successful-but-empty lookup — the silent failure mode this project's
  authoring guide warns about. The transforms log that the address is a bogon and return nothing.
- **An invalid or revoked token returns HTTP 403, not 401.**

## Scope

This is an illustrative example, like `transforms/examples/ffraud.py` and
`transforms/ransomwarelive/` — it shows an authenticated API with a credential entered in the
Maltego client. IPinfo is a third-party service this project has no affiliation with. Adapt it or
delete it, along with its import in `server/project.py`.
