# Ransomware.live transforms

Sixteen transforms over the [ransomware.live PRO API](https://api-pro.ransomware.live), covering
ransomware group intelligence, victim listings, indicators of compromise, and national incident
response contacts.

The code lives in `server/transforms/ransomwarelive/`:

| Module | Contents |
|--------|----------|
| `api.py` | Shared client: auth header, input validation, error handling, field normalisation |
| `groups.py` | Group profile, leak sites, CVEs, TTPs, tools, victims, group discovery |
| `victims.py` | Victim search, domain-to-breach, victim details, press coverage |
| `intel.py` | IOCs, YARA rules, ransom notes, negotiations, CSIRT contacts |

## API key

Every transform declares one global, credential-marked setting: **`RANSOMWARE_LIVE_API_KEY`**.
Enter it once in the Maltego client and the whole set picks it up. With no key the transforms
return nothing and log `No ransomware.live API key configured` rather than failing opaquely.

`api.py` falls back to the `RANSOMWARE_LIVE_API_KEY` environment variable when the client setting
is empty. The server subprocess inherits the parent shell, so a key exported once survives
restarts and seed re-imports — the latter can orphan the client's stored value and leave it
resolving empty while the settings field still looks filled. The client setting still takes
precedence. This is a development convenience; see `docs/transform-authoring.md`.

For CLI runs, pass it per invocation:

```bash
uv run python scripts/transformatron_cli.py run \
  acme.new_maltego_integration.group_profile maltego.Malware akira \
  --setting RANSOMWARE_LIVE_API_KEY=<your-key>
```

## Entity conventions

**Groups are `Malware` entities.** The value is the ransomware.live slug — lowercase, sometimes
with digits or spaces (`lockbit3`, `akira`, `alphv`, `0day syndicate`). Slugs are not always
guessable from a group's public name, so start from **List Groups**, which takes a `Phrase` and
returns matching groups as `Malware`. An empty or `*` phrase returns every group with at least one
victim.

**Victims are `Company` entities**, and their websites come back as `Website`. `Domain to Breach`
takes a `Domain` and is the reverse pivot: given a company domain, it reports whether that domain
appears in a leak site listing and which group claimed it.

## Transform reference

| Transform | Input | Output |
|-----------|-------|--------|
| List Groups | `Phrase` | `Malware` |
| Group Profile | `Malware` | `Phrase` |
| Group to Leak Sites | `Malware` | `URL` |
| Group to CVEs | `Malware` | `CVE` |
| Group to TTPs | `Malware` | `TTP` |
| Group to Tools | `Malware` | `Phrase` |
| Group to Victims | `Malware` | `Company` |
| Group to IOCs | `Malware` | `IPv4Address`, `Domain`, `Hash`, `EmailAddress`, `BTCAddress`, `URL` |
| Group to YARA Rules | `Malware` | `MalwareSignature` |
| Group to Ransom Notes | `Malware` | `Phrase` |
| Group to Negotiations | `Malware` | `Phrase` |
| Country to CSIRT Contacts | `Country` | `EmailAddress`, `Website` |
| Search Victims | `Company` | `Company` |
| Victim Details | `Company` | `Malware`, `Website`, `Location`, `Phrase`, `URL` |
| Domain to Breach | `Domain` | `Company`, `Malware` |
| Victim to Press | `Company` | `URL` |

YARA rule text and negotiation details are attached as entity notes rather than separate entities,
so they are readable in the client without cluttering the graph.

## Two upstream behaviours worth knowing

**The victim schema is mid-rename.** `/victims/recent` and `/victims/` return `victim`, `group` and
`attackdate`; `/victims/search` still returns the legacy `post_title`, `group_name` and `published`
for the same records. `api.normalise_victim` reads the new name first and falls back to the old
one. Read a victim field through it rather than indexing the raw response.

**`/group/<name>` and `/groups/<name>` are aliases** returning identical payloads, despite the
swagger listing them separately. The code uses `/groups/`.

## Result caps

Large groups would otherwise flood the graph — LockBit has over 2000 victims. Victim listings and
search results cap at 100 (`VICTIM_LIMIT`, `SEARCH_LIMIT`), indicators at 200 (`IOC_LIMIT`). Each
transform logs the true upstream total, so a truncated result is visible rather than silent.

## Verification

The project smoke test covers this set:

```bash
uv run python scripts/smoke_test_transforms.py \
  --setting RANSOMWARE_LIVE_API_KEY=<your-key>
```

Exporting `RANSOMWARE_LIVE_API_KEY` before starting the server works too — `api.py` falls back to
the environment when the client setting is empty.

Expect **17 passed, 2 skipped** across both example sets. The two skips are `victim_details` and
`victim_to_press`: both need an exact victim organisation name, while the shared `Company` sample
(`hospital`) is a search substring. They report SKIP rather than FAIL because the transform ran
correctly and said it had no match. To exercise them, add a `TRANSFORM_SAMPLES` entry in
`scripts/smoke_test_transforms.py` naming a victim currently listed in `/victims/recent` — that
value goes stale as listings are taken down, which is why none is committed.

`group_to_cves`, `group_to_ttps` and `list_groups` have `TRANSFORM_SAMPLES` entries already: the
default `lockbit3` sample has empty `ttps` and `vulnerabilities`, so those two use `akira`, and
`list_groups` matches group names as a substring so it uses `lock`.
