# transformatron

An MCP control plane for a local [Maltego](https://www.maltego.com/) v3 transform server.

Maltego transforms are small functions that take one entity (an IP address, a domain, a person)
and return related entities, building up a graph. The `maltego-transforms` SDK gives you a server
to host them. This project gives an AI coding agent — or you, at a terminal — the ability to
**start that server, reload it after an edit, and run a transform to see what it returns**,
without leaving the editor.

It does not help you *write* transforms. The SDK already ships agent skills for that, and this
project deliberately does not duplicate them. See [Division of labour](#division-of-labour).

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

## Division of labour

Two layers that do not overlap:

| Need | Use |
|------|-----|
| **Write or change transform code** | The official SDK agent skills in `server/.agents/skills/` |
| **Run, reload, or inspect the server** | The `transformatron` MCP tools |

To author a transform, load `server/.agents/skills/maltego-transform-skill-index/SKILL.md` first.
It is a routing table pointing to one focused skill per task (`-build`, `-design`, `-test`,
`-discover`, `-docs`, or the TRX migration pair). Load one at a time.

These skills ship with `maltego-transforms` and are versioned with the SDK, which is exactly why
this project does not restate their contents — a copy would go stale against the package.

## The loop

1. Add or edit a module under `server/transforms/`.
2. Import it in `server/project.py` (`from transforms.my_module import *`). **The server only
   discovers what `project.py` imports** — this is the most common reason a new transform never
   shows up.
3. `server_restart` — this is the reload path.
4. `list_transforms` to confirm it registered, then `run_transform` to exercise it.

## MCP tools

| Tool | Purpose |
|------|---------|
| `server_start(ssl=False)` | Start the server |
| `server_stop()` | Stop it |
| `server_restart(ssl=False)` | Reload after a code change |
| `server_status()` | Running, healthy, transform count |
| `server_logs(lines=50)` | First place to look when a transform fails to register |
| `list_transforms()` | Every transform the server advertises, with input/output types |
| `get_transform(id)` | Full detail document for one transform |
| `list_entities()` | Entity types the server advertises |
| `run_transform(...)` | Execute one transform against one input entity |
| `get_seed_url()` | The URL to register in the Maltego client |
| `generate_certs(force=False)` | Self-signed cert for HTTPS |

`run_transform` takes `(transform_id, entity_type, entity_value, settings=None, timeout=60)`.

## Using it with Claude Code

The MCP server is registered at project scope in `.mcp.json`, so a fresh clone picks it up
automatically. It requires a Claude Code session restart to load, and prompts once for
project-scope approval.

That approval is recorded in `.claude/settings.local.json`, which is per-machine and gitignored —
so every fresh clone prompts again. This is expected, not a bug.

## Using it without an agent

Nothing here is agent-only. The same operations work from Python:

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

`server/transforms/examples/ffraud.py` is an **illustrative sample, not a maintained
integration**. It shows the shape of a working transform against
[ffraud.com](https://ffraud.com/docs), a third-party IP reputation API this project has no
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
  config.py      TransformatronConfig — host, port, scheme, derived URLs and state paths
  client.py      async v3 protocol client; the run→poll→flatten state machine
  lifecycle.py   start/stop/restart/status/logs over a detached subprocess
  certs.py       self-signed certificate generation
  mcp.py         the 11 MCP tools
server/          SDK-generated (`maltego-transforms start server --with-skills`).
                 Upstream-owned: excluded from ruff so regeneration does not churn.
  project.py     entrypoint; imports decide what gets registered
  transforms/    your transform modules go here
tests/           28 tests
.transformatron/ runtime state — PID, log, certs, recorded scheme. Gitignored.
```

## Gotchas

Things that cost real debugging time, recorded so they cost you less:

- **A transform missing from `list_transforms`, or showing output type `NONE`,** almost always
  means a missing or untyped annotation. Input type comes from the parameter annotation, output
  from the return annotation. A bare `-> list` advertises no output type and breaks client routing.

- **Returning a `MaltegoGraph` from a plain `async` transform silently yields zero entities.** The
  run still reports success. In the SDK's `__handle_async_result`, a returned graph fails both
  `isinstance` branches and is dropped. Return a list instead — `-> list[AS | ISP]` still publishes
  every output type to discovery. Note this contradicts the SDK's own `maltego-transform-build`
  skill, whose `return graph` example only works for async *generators*.

- **The desktop client requires HTTPS** (see [above](#connecting-to-the-maltego-desktop-client)).

- **The server runs on port 3000.** The SDK's own skill scripts default to 8080 — pass
  `--port 3000` if you invoke them directly.

- **`MALTEGO_SERVER_*` environment variables take precedence** over the values hardcoded in
  `project.py`. That is how the lifecycle tools set host, port, and scheme without editing the
  generated file.

- **The scheme is runtime state,** recorded in `.transformatron/server.scheme`. `project.py`
  hardcodes `http`, so that file is the only way the control plane knows to address an
  `ssl=True` server over HTTPS.

## Development

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run ty check src tests
```

`server/` is excluded from linting because it is SDK-generated and regenerating it would otherwise
produce churn.

## Naming

The generated server still identifies itself with the SDK's placeholders —
`server_name="New Maltego Integration"`, `ns="acme.new_maltego_integration"`, `author="Acme Corp"`
in `server/project.py`. Change these before publishing anything; the namespace becomes part of
every transform's fully qualified ID.

## License

MIT — see [LICENSE](LICENSE).

Maltego is a trademark of Maltego Technologies GmbH. This is an independent project, not affiliated
with or endorsed by Maltego.
