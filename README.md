# transformatron

An MCP control plane for a local [Maltego](https://www.maltego.com/) v3 transform server.

Maltego transforms are small functions that take one entity (an IP address, a domain, a person)
and return related entities, building up a graph. The `maltego-transforms` SDK gives you a server
to host them. This project gives you — or a coding agent — the ability to **start that server,
reload it after an edit, and run a transform to see what it returns**, without leaving the editor.

Everything works two ways: a **CLI** for humans and any agent that can run commands, and an **MCP
server** for agents that speak it. Both call the same code, so they behave identically.

It does not help you *write* transforms — the SDK ships guidance for that. But it does correct two
SDK behaviours that fail silently; see [Writing transforms](#writing-transforms).

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

That verifies the control plane. To drive it from an agent, see [Using it with Claude
Code](#using-it-with-claude-code); to drive it yourself, see [Using it without an
agent](#using-it-without-an-agent).

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

Assumes `uv sync` has run. This is the whole loop, start to finish:

```bash
# 1. Certificates, once — the desktop client refuses plain HTTP.
uv run python scripts/transformatron_cli.py certs

# 2. Start the server.
uv run python scripts/transformatron_cli.py start --ssl
```

Write a module under `server/transforms/`:

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

Register it in `server/project.py`, next to the existing transform imports:

```python
from transforms.hello import *  # noqa: F401,F403
```

The server only loads what `project.py` imports, and the import must sit with the others at the
top — appending it to the end of the file puts it after the `if __name__ == "__main__"` block,
where it still runs but registers nothing you can see. A transform that never appears in `list` is
almost always this.

```bash
# 4. Reload and confirm.
uv run python scripts/transformatron_cli.py restart
uv run python scripts/transformatron_cli.py list

# 5. Run it, and check the entity count — not just the success state.
uv run python scripts/transformatron_cli.py run \
  acme.new_maltego_integration.ip_to_domain maltego.IPv4Address 8.8.8.8

# 6. Gate the whole set.
uv run python scripts/smoke_test_transforms.py
```

Then [connect the desktop client](#connecting-to-the-maltego-desktop-client) and run it on a real
graph.

Coding agents get this same loop automatically: Claude Code from
`.claude/skills/maltego-transform-author/`, other agents from
[`AGENTS.md`](AGENTS.md). Both point at `docs/transform-authoring.md` rather than restating it.

## Writing transforms

**Start with [`docs/transform-authoring.md`](docs/transform-authoring.md).**

The SDK ships its own authoring guidance in `server/.agents/skills/`, versioned with the package,
and this project does not restate it — a copy would go stale. But two of its examples are wrong in
ways that fail silently: the code looks right, the run reports success, and no entities come back.
`docs/transform-authoring.md` corrects those, then routes to the SDK guidance for everything else.

| Need | Use |
|------|-----|
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

CLI commands are prefixed `uv run python scripts/transformatron_cli.py`:

```bash
uv run python scripts/transformatron_cli.py restart
uv run python scripts/transformatron_cli.py list
uv run python scripts/transformatron_cli.py run <id> maltego.IPv4Address 8.8.8.8
```

Pass settings with repeated `--setting KEY=VALUE`. `--help` works on any subcommand.

Adding an operation? Put it in `src/transformatron/operations.py` and both front ends get it.

## Using it with a coding agent

`AGENTS.md` is the entry point, following the [AGENTS.md](https://agents.md) convention that Codex,
Gemini CLI, and others read directly. `CLAUDE.md` points at the same file so the guidance cannot
drift.

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

Two worked examples ship with the project. Both are **illustrative samples, not maintained
integrations** — delete whichever you do not need, along with its import in `server/project.py`.

| Example | Shows |
|---|---|
| `server/transforms/examples/ffraud.py` | Single module, no API key — the minimal shape |
| `server/transforms/ransomwarelive/` | Multi-module, API key, shared client layer |

Start from `ffraud.py` if you are learning the shape. Start from `ransomwarelive/` if your API
needs a key — it is documented in [`docs/ransomware-live.md`](docs/ransomware-live.md) and shows
the parts the simple example cannot: declaring one credential across a whole transform set,
keeping validation and error handling in a shared `api.py`, capping result sizes so a large
upstream response does not flood the graph, and normalising a schema whose field names differ
between endpoints. It needs a [ransomware.live](https://www.ransomware.live/) API key to run.

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
  mcp.py         MCP front end
scripts/
  transformatron_cli.py     CLI front end
  smoke_test_transforms.py  runs every transform, fails on zero entities
server/          SDK-generated (`maltego-transforms start server --with-skills`).
                 Upstream-owned except transforms/: .agents/ and project.py are
                 excluded from ruff so regeneration does not churn.
  project.py     entrypoint; imports decide what gets registered
  transforms/    your transform modules go here — linted like the rest of the project
.claude/skills/
  maltego-transform-author/  authoring checklist; points at docs/, not a second copy
docs/
  transform-authoring.md   read before writing a transform
  ransomware-live.md       the authenticated worked example
  upstream-sdk-issue.md    draft bug report, not yet filed
tests/           37 tests
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

`server/` is excluded from linting because it is SDK-generated and regenerating it would otherwise
produce churn.

### Smoke-testing transforms

```bash
uv run python scripts/smoke_test_transforms.py
uv run python scripts/smoke_test_transforms.py --setting API_KEY=xxx
```

Runs every registered transform against a sample input and **fails on zero entities** or an output
type of `NONE` — the failure modes that otherwise report success. Exits non-zero, so it works as a
gate. Run it after any change under `server/transforms/`.

Transforms needing credentials take them through repeated `--setting KEY=VALUE`. Two outcomes
report SKIP rather than FAIL — a missing credential, and an input the upstream has no match for —
so an unconfigured key is never mistaken for broken code. A SKIP is not evidence a transform
works; it means the gate could not judge it. Add a `TRANSFORM_SAMPLES` entry when the
per-entity-type sample does not suit a transform.

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
