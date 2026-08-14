# transformatron

Build [Maltego](https://www.maltego.com/) transforms with a coding agent, against a real server.

Maltego transforms are small functions that take one entity (an IP address, a domain, a person)
and return related entities, building up a graph. This project gives an agent the two halves it
needs to build them: a **local transform server it can drive** — start, reload after an edit, run
a transform and read back what it returned — and the **authoring guidance and scaffolding** to
write the transform in the first place.

That combination is the point. An agent that can only write code guesses at what the API returns;
an agent that can only run a server has nothing to run. Together they close the loop: scaffold from
a spec, restart, run it, see the entities, fix what the spec got wrong.

Everything works two ways: an **MCP server** for agents that speak it, and a **CLI** for humans and
any agent that can run commands. Both call the same code, so they behave identically.

> **Status:** early. Built and verified against a live server on macOS, but not yet exercised on
> Linux or Windows, and not published to PyPI. Expect rough edges.

## Requirements

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** for dependency management
- **A Maltego client** — [Maltego Desktop](https://www.maltego.com/downloads/) to actually use the
  transforms. Not needed to run the tests.
- **OpenSSL** on `PATH` (ships with macOS and most Linux distributions) — only for HTTPS certs.

## Quick start

```bash
git clone <your-fork-url> claude-transformatron-9000
cd claude-transformatron-9000
uv sync
uv run pytest -q
```

That verifies the control plane. Then go to [Build your first
transform](#build-your-first-transform) — with an agent or by hand — and, if you have API keys to
test against, [Credentials](#credentials).

### Connecting to the Maltego desktop client

The desktop client **refuses plain-HTTP transform servers**, so HTTPS is not optional:

```
Only HTTPS (SSL/TLS) Transform Servers are allowed, but found: http://127.0.0.1:3000
```

Note that this failure happens *inside the client* — the request never reaches the server, so
nothing appears in the server log. An empty log alongside a client-side error is the signature of
this problem, not a sign the server is broken.

1. Generate a self-signed certificate (`generate_certs`).
2. Trust it. This changes system trust, so the tool prints the command rather than running it:
   ```bash
   sudo security add-trusted-cert -d -r trustRoot \
     -k /Library/Keychains/System.keychain .transformatron/certs/cert.pem
   ```
   That command is macOS-specific; on Linux, install the cert into your distribution's CA store.
3. Start the server with SSL (`server_restart(ssl=True)`).
4. In Maltego: **Transforms → Transform Hub → add a local hub item**, and paste the seed URL
   `https://127.0.0.1:3000/seed`.
5. Install the hub item, then right-click a matching entity to run a transform.

The server binds to `127.0.0.1` only. That is fine for a client on the same machine, but a client
on another host will not reach it.

## Build your first transform

Assumes `uv sync` has run.

### With an agent

Open a coding agent in the clone and start the server:

```bash
uv run python scripts/transformatron_cli.py start
```

Claude Code picks up `.claude/skills/maltego-transform-author/` from the clone; other agents read
[`AGENTS.md`](AGENTS.md). Nothing to invoke — the guidance is already loaded. Then describe what you
want:

> Build a transform for urlscan.io's search endpoint, then run it and show me the entities:
> `curl "https://urlscan.io/api/v1/search/?q=domain:example.com"`

Here is the whole session, and what the agent is doing at each step.

**1. It reads [`docs/transform-authoring.md`](docs/transform-authoring.md) first.** Two SDK
behaviours fail silently — see [Writing transforms](#writing-transforms) — and the skill treats
reading those corrections as a precondition for writing code.

**2. It scaffolds:**

```
Scaffolded 'Urlscan' (urlscan):

Created files:
  - server/transforms/urlscan/__init__.py
  - server/transforms/urlscan/api.py
  - server/transforms/urlscan/lookup.py

Transforms:
  - Urlscan: Lookup (IPv6Address -> Phrase)

Import added to server/project.py.
```

Note `IPv6Address -> Phrase`, which is wrong on both sides: the endpoint takes a search query and
returns a list of scans. The scaffolder had a URL and nothing else, so it guessed from the
parameter name and guessed badly. Correcting that is the next three steps, and it is the normal
case rather than a mishap — pass `--sample-response` with real JSON and the output side improves,
but only a live run settles it.

**3. It restarts and confirms registration** — `restart`, then `list`. The server only loads code
at startup, so an edit without a restart changes nothing. A transform missing from `list` almost
always means a missing import in `project.py`, which `scaffold` writes for you.

**4. It runs the transform and reads the output.** The generated code maps one `Phrase` out of the
response, so the first run reports something like:

```
State: COMPLETED (success)
Entities (1):
  {"type": "maltego.Phrase", ...}
```

**This is the step that makes the difference.** `COMPLETED (success)` is not the answer — the
entity count is. One `Phrase` holding a blob of text is not a useful transform: urlscan returns a
list of scans, each with a page URL, an IP and an ASN. The agent now knows the real response shape,
which the cURL command never told it.

**5. It corrects the mapping and goes round again.** Take the input as a search `Phrase`, walk
`results[]`, map `page.url` to a `URL`, `page.ip` to an `IPv4Address`, `page.domain` to a `Domain`
and `page.asn` to an `AS`. Restart, run, read the entities again — the finished transform returns
25 for this query rather than 1. Two or three passes is normal.

**6. It gates the result:**

```bash
uv run python scripts/smoke_test_transforms.py
```

Runs every registered transform and fails on zero entities or an output type of `NONE`.

#### What you decide

The agent handles the mechanics. The judgement calls are yours:

- **Which API, and which endpoints deserve transforms.**
- **How responses map to entities.** Is `page.asn` an `AS` entity or a `Phrase`? That choice
  decides whether the graph can pivot on it.
- **When it is actually done.** The agent may accept one entity; you know the query should have
  returned twenty.

One habit is worth more than the rest: **when an agent says a transform works, ask what the entity
count was.** That single question catches the failure mode this project exists to prevent.

#### If the MCP tools are missing

A fresh clone prompts once for approval of the `transformatron` MCP server and needs a session
restart to load it. Until then the agent falls back to the CLI, which does exactly the same things.
Seeing it shell out to `transformatron_cli.py` instead of calling `run_transform` is expected, not
a fault.

### By hand

The same loop, driven yourself. Scaffold from a spec:

```bash
uv run python scripts/transformatron_cli.py scaffold --service ipinfo \
  --curl 'curl -H "Authorization: Bearer TOKEN" https://ipinfo.io/8.8.8.8/json'
```

Or write a module under `server/transforms/` directly — this is the minimal shape:

```python
# server/transforms/hello.py
from maltego.entities import Domain, IPv4Address
from maltego.model.context import MaltegoContext
from maltego.server import register_transform


@register_transform(display_name="Hello: IP to Domain", transform_set="hello")
async def ip_to_domain(input_entity: IPv4Address, context: MaltegoContext) -> list[Domain]:
    """Return a fixed domain, to prove the loop works."""
    return [Domain(value="example.com")]
```

Both annotations matter: `IPv4Address` declares the input type, `list[Domain]` the output. A bare
`-> list` registers the transform with output `NONE` and the client cannot route it.

A hand-written module needs its import in `server/project.py`, next to the existing ones
(`scaffold` does this for you):

```python
from transforms.hello import *  # noqa: F401,F403
```

The server only loads what `project.py` imports, and the import must sit with the others at the
top — appending it to the end of the file puts it after the `if __name__ == "__main__"` block,
where it still runs but registers nothing you can see. A transform that never appears in `list` is
almost always this.

```bash
# Reload and confirm.
uv run python scripts/transformatron_cli.py restart
uv run python scripts/transformatron_cli.py list

# Run it, and check the entity count — not just the success state.
uv run python scripts/transformatron_cli.py run \
  acme.new_maltego_integration.ip_to_domain maltego.IPv4Address 8.8.8.8

# Gate the whole set.
uv run python scripts/smoke_test_transforms.py
```

Then [connect the desktop client](#connecting-to-the-maltego-desktop-client) and run it on a real
graph.

## Scaffolding from a spec

`scaffold` turns a cURL command or an OpenAPI document into a working module package: an `api.py`
client with the auth and validation wired up, one module per transform, and the import added to
`server/project.py`.

```bash
# From a cURL command, with a sample response to infer output entities from.
uv run python scripts/transformatron_cli.py scaffold --service demo \
  --curl 'curl -H "X-API-KEY: k" https://api.demo.com/v1/ip/8.8.8.8'

# From an OpenAPI spec — a path or the document itself.
uv run python scripts/transformatron_cli.py scaffold --service demo --openapi ./demo-openapi.json
```

It infers input and output entity types from parameter names and the sample response, picks the
right validator for the input type, and handles keys sent as a header, a bearer token, or a query
parameter.

**Treat the result as a first draft.** The generator works from the spec, and specs routinely
disagree with the live API about which fields are present, what a 404 means, and how errors are
shaped. Run the transform, read the entities, and correct the mapping — the loop above exists for
exactly this. An existing service is never overwritten: scaffolding onto one raises rather than
replacing hand-written code that has already absorbed those corrections.

## Writing transforms

**Start with [`docs/transform-authoring.md`](docs/transform-authoring.md).**

The SDK ships its own authoring guidance in `server/.agents/skills/`, versioned with the package,
and this project does not restate it — a copy would go stale. But two of its examples are wrong in
ways that fail silently: the code looks right, the run reports success, and no entities come back.
`docs/transform-authoring.md` corrects those, then routes to the SDK guidance for everything else.

| Need | Use |
|------|-----|
| **Generate a starting point from an API spec** | `scaffold`, then correct it against a live run |
| **Write or change transform code** | `docs/transform-authoring.md`, then the SDK skills |
| **Run, reload, or inspect the server** | The CLI or the MCP tools |

## The loop

1. Add or edit a module under `server/transforms/`.
2. Import it in `server/project.py` (`from transforms.my_module import *`). **The server only
   discovers what `project.py` imports** — this is the most common reason a new transform never
   shows up.
3. `server_restart` — this is the reload path. It keeps the scheme the server is already
   running under, so an HTTPS server stays on HTTPS.
4. `list_transforms` to confirm it registered, then `run_transform` to exercise it.

## Commands

Every operation is available as a CLI command and as an MCP tool. Both call the same
`transformatron.operations` module, so output is identical.

| Purpose | CLI | MCP tool |
|---|---|---|
| Start the server | `start [--ssl]` | `server_start(ssl=False)` |
| Stop it | `stop` | `server_stop()` |
| Reload after a code change | `restart [--ssl\|--no-ssl]` | `server_restart()` |
| Running, healthy, transform count | `status` | `server_status()` |
| Recent log output | `logs [--lines N]` | `server_logs(lines=50)` |
| Advertised transforms and their types | `list` | `list_transforms()` |
| Detail document for one transform | `show <id>` | `get_transform(id)` |
| Advertised entity types | `entities` | `list_entities()` |
| Run one transform | `run <id> <type> <value>` | `run_transform(...)` |
| Seed URL and registration steps | `seed-url` | `get_seed_url()` |
| Self-signed cert for HTTPS | `certs [--force]` | `generate_certs(force=False)` |
| Scaffold from cURL / OpenAPI | `scaffold [--curl ...]` | `scaffold_transform(...)` |

CLI commands are prefixed `uv run python scripts/transformatron_cli.py`:

```bash
uv run python scripts/transformatron_cli.py restart
uv run python scripts/transformatron_cli.py list
uv run python scripts/transformatron_cli.py run <id> maltego.IPv4Address 8.8.8.8
```

Pass settings with repeated `--setting KEY=VALUE`. `--help` works on any subcommand.

Adding an operation? Put it in `src/transformatron/operations.py` and both front ends get it.

## Credentials

In real use, an API key belongs in the Maltego client's transform settings — declared with
`TransformSetting(auth=True, is_global=True)`, entered once, reused by every transform in the set.
Nothing needs configuring on the server for that path to work.

For headless runs there is no client to enter it into, so transforms fall back to the process
environment. Copy the template and fill in what you have:

```bash
cp .env.example .env
```

`.env` is read when the server starts and merged into its environment, so a key written once
survives restarts. The smoke test reads it too, which is the difference between a credential-gated
transform being exercised and being reported SKIP. An exported shell variable beats the file, and
an explicit `--setting` beats both.

`.env` is gitignored. `.env.example` is the committed template and holds no values. This is a
development convenience: it puts keys in the server process's environment, visible to anyone who
can read `ps`. Restart after editing it — the environment is read once, at start.

## Transforms you do not want to publish

Anything under `server/transforms/local/` is gitignored and discovered automatically when the
server starts. Use it for integrations that should not be committed — an internal API, a
client-specific lookup, work in progress.

It is discovered rather than imported by name, because a fresh clone does not have the directory
and a static import of a missing module stops the server booting. The committed examples alongside
it keep their explicit imports in `project.py`.

## Using it with a coding agent

An agent working in this repository can scaffold a transform from an API spec, restart the server
to load it, run it against a real input, read back the entities it produced, and gate the whole set
with the smoke test. That is the loop — and because every step reports what actually happened, the
agent can tell a working transform from one that reports success and returns nothing.

What it reads:

| Agent | Entry point |
|---|---|
| **Claude Code** | `.claude/skills/maltego-transform-author/`, which ships with the clone |
| **Codex, Gemini CLI, others** | [`AGENTS.md`](AGENTS.md), per the [AGENTS.md](https://agents.md) convention |

`CLAUDE.md` points at `AGENTS.md` so the two cannot drift. Both route to
`docs/transform-authoring.md` for the SDK corrections.

Any agent that can run shell commands can drive the server through the CLI — no MCP required.

For **Claude Code**, the MCP server is registered at project scope in `.mcp.json`, so a fresh clone
picks it up automatically. It needs a session restart to load and prompts once for approval. That
approval is recorded in `.claude/settings.local.json`, which is per-machine and gitignored, so a
fresh clone prompts again — expected, not a bug. If the tools are unavailable, the CLI does
everything they do.

If you are modifying this project's own code under `src/transformatron/`, note that the MCP tools
run the version loaded when the session started, so your edits will not show up there until you
relaunch. The CLI always runs current code. `AGENTS.md` has the details — this does not affect
editing transforms under `server/transforms/`.

## Using it from Python

The same operations work directly:

```python
import asyncio
from transformatron import lifecycle
from transformatron.client import TransformClient
from transformatron.config import load_config

config = load_config()
lifecycle.start(config, ssl=True)


async def main() -> None:
    client = TransformClient(lifecycle.resolve_config(config))
    for transform in await client.list_transforms():
        print(transform["name"])


asyncio.run(main())
```

Run it with `uv run python your_script.py`.

## The sample transforms

Several worked examples ship with the project. All are **illustrative samples, not maintained
integrations** — delete whichever you do not need, along with its import in `server/project.py`.

| Example | Shows | Key |
|---|---|---|
| `server/transforms/examples/ffraud.py` | Single module, no API key — the minimal shape | no |
| `server/transforms/ransomwarelive/` | Multi-module, shared client, upstream field drift | yes |
| `server/transforms/greynoise/` | 404 as a verdict, silent key acceptance, tight quota | yes |
| `server/transforms/ipinfo/` | Bearer auth, one response fanned out to several entities | yes |
| `server/transforms/crowdsec/` | Minimal authenticated lookup | yes |

Start from `ffraud.py` if you are learning the shape. Start from `ransomwarelive/` if your API
needs a key — it is documented in [`docs/ransomware-live.md`](docs/ransomware-live.md) and shows
the parts the simple example cannot: declaring one credential across a whole transform set,
keeping validation and error handling in a shared `api.py`, capping result sizes so a large
upstream response does not flood the graph, and normalising a schema whose field names differ
between endpoints. It needs a [ransomware.live](https://www.ransomware.live/) API key to run.

`greynoise/` is worth reading for what a spec cannot tell you: an unrecognised key is accepted
silently, so a successful lookup is not evidence the key is valid; HTTP 404 is a real verdict
("never observed scanning") rather than a failure; and the free tier allows roughly 25 lookups a
week, which the smoke test can spend in one pass. Every one of those was found by running it, not
by reading the documentation.

### ffraud

Against [ffraud.com](https://ffraud.com/docs), a third-party IP reputation API this project has no
affiliation with. It needs no API key, so it runs on a fresh clone.

Three transforms, all taking `maltego.IPv4Address`:

| Transform | Returns |
|---|---|
| ffraud: IP Reputation | `Phrase` — fraud score, risk band, detection flags, threat tags |
| ffraud: IP to Network Details | `AS`, `ISP`, `DNSName`, `Location` |
| ffraud: IP to Abuse Contact | `EmailAddress` from WHOIS |

Worth reading for the patterns it demonstrates: validating `input_entity.value` before
interpolating it into a URL, catching `MaltegoException` so an upstream outage degrades to an
empty result instead of a crash, and using `.get()` throughout because the API omits fields rather
than nulling them.

**Caveat on the data:** upstream coverage is uneven. A known Tor exit node returned
`fraud_score: 0, risk: none` with `tor: false` during testing, and sample responses carried
`data_completeness: 0.5`. Treat the scores as illustrative. To delete the sample, remove the
directory and its import in `server/project.py`.

## Layout

```
src/transformatron/
  operations.py  server operations shared by both front ends — add new ones here
  config.py      TransformatronConfig — host, port, scheme, derived URLs and state paths
  client.py      async v3 protocol client; the run→poll→flatten state machine
  lifecycle.py   start/stop/restart/status/logs over a detached subprocess
  certs.py       self-signed certificate generation
  envfile.py     reads .env so headless runs can reach credentials
  scaffold/      spec → module package: parser.py, generator.py, schema.py
  mcp.py         MCP front end
scripts/
  transformatron_cli.py     CLI front end
  smoke_test_transforms.py  runs every transform, fails on zero entities
server/          SDK-generated (`maltego-transforms start server --with-skills`).
                 Upstream-owned except transforms/: .agents/ and project.py are
                 excluded from ruff so regeneration does not churn.
  project.py     entrypoint; imports decide what gets registered
  transforms/    your transform modules go here — linted like the rest of the project
    local/       gitignored; discovered at startup, for integrations you do not publish
.claude/skills/
  maltego-transform-author/  authoring checklist; points at docs/, not a second copy
docs/
  transform-authoring.md   read before writing a transform
  ransomware-live.md       the authenticated worked example
  ipinfo.md                bearer auth, one response to several entities
  upstream-sdk-issue.md    draft bug report, not yet filed
tests/           unit tests for the control plane and the scaffolder
.env             API keys for headless runs. Gitignored; .env.example is the template.
.transformatron/ runtime state — PID, log, certs, recorded scheme. Gitignored.
```

## Gotchas

Things that cost real debugging time, recorded so they cost you less:

- **A transform missing from `list_transforms`, or showing output type `NONE`,** almost always
  means a missing or untyped annotation. Input type comes from the parameter annotation, output
  from the return annotation. A bare `-> list` advertises no output type and breaks client routing.

- **Returning a `MaltegoGraph` from an `async` transform silently yields zero entities.** The run
  still reports success. In the SDK's `__handle_async_result`, a returned graph fails both
  `isinstance` branches and is dropped; the generator branch skips graphs too, so this affects
  every async path. Return a list — `-> list[AS | ISP]` still publishes every output type to
  discovery. This contradicts the SDK's own shipped guidance, which teaches the broken pattern;
  see `docs/transform-authoring.md` and the draft report in `docs/upstream-sdk-issue.md`.

- **A success state is not proof a transform works.** Check the entity count, or run
  `scripts/smoke_test_transforms.py`.

- **An API key entered in the client can stop reaching the transform after a seed re-import.**
  The symptom is a transform reporting the key as missing while the client's settings field still
  looks populated. Re-importing after the seed URL changes — switching the server between HTTP and
  HTTPS does this — rewrites the transform definitions and orphans the stored global value. Clear
  the field, apply, then re-enter the key. See
  [`docs/transform-authoring.md`](docs/transform-authoring.md) for the environment-variable
  fallback that avoids this during development.

- **A successful lookup is not proof the API key is valid.** Some upstreams accept an unrecognised
  key and answer normally — GreyNoise does — so there is no auth-failure path to catch and no
  signal that the key is wrong until a quota or a permission boundary exposes it. Verify a key
  against something that requires it, not against a call that happens to succeed.

- **The desktop client requires HTTPS** (see [above](#connecting-to-the-maltego-desktop-client)).

- **The server runs on port 3000.** The SDK's own skill scripts default to 8080 — pass
  `--port 3000` if you invoke them directly.

- **`MALTEGO_SERVER_*` environment variables take precedence** over the values hardcoded in
  `project.py`. That is how the lifecycle tools set host, port, and scheme without editing the
  generated file.

- **The scheme is runtime state,** recorded in `.transformatron/server.scheme`. `project.py`
  defaults to `https`, but the lifecycle tools override the scheme per start, so that file is the
  only way the control plane knows which scheme to address a running server over.

## Development

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run ty check src tests scripts
```

`server/.agents/` and `server/project.py` are excluded from linting: the SDK owns them, and
regenerating would churn against upstream. `server/transforms/` is your code and is linted like the
rest of the project — including anything `scaffold` generates.

### Smoke-testing transforms

```bash
uv run python scripts/smoke_test_transforms.py
uv run python scripts/smoke_test_transforms.py --setting API_KEY=xxx
```

Runs every registered transform against a sample input and **fails on zero entities** or an output
type of `NONE` — the failure modes that otherwise report success. Exits non-zero, so it works as a
gate. Run it after any change under `server/transforms/`.

Transforms needing credentials take them through repeated `--setting KEY=VALUE`, or from `.env`
(see [Credentials](#credentials)). Four outcomes report SKIP rather than FAIL — a missing
credential, an upstream rate limit, a sample input the transform's own validation rejects, and an
input the upstream has no match for — so neither an unconfigured key nor a spent quota is mistaken
for broken code. A SKIP is not evidence a transform works; it means the gate could not judge it.
Add a `TRANSFORM_SAMPLES` entry when the per-entity-type sample does not suit a transform.

Transforms calling third-party APIs make live network requests, so a failure can mean an upstream
outage rather than broken code; check the reported message. Override the input with `--value`, or
check one transform with `--transform <id>`.

## Naming

The generated server still identifies itself with the SDK's placeholders —
`server_name="New Maltego Integration"`, `ns="acme.new_maltego_integration"`, `author="Acme Corp"`
in `server/project.py`. Change these before publishing anything; the namespace becomes part of
every transform's fully qualified ID.

## License

MIT — see [LICENSE](LICENSE).

Maltego is a trademark of Maltego Technologies GmbH. This is an independent project, not affiliated
with or endorsed by Maltego.
