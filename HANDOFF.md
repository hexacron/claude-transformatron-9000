# Handoff — claude-transformatron-9000

Status as of 2026-08-13. The control plane is built and verified against a live server. Read
*Current state — uncommitted* before starting: the working tree has uncommitted changes, and the
server currently serves zero transforms.

## What this is

An MCP control plane for a single local Maltego v3 transform server.

The organising decision: **the `maltego-transforms` SDK already ships its own agent skills for
authoring transforms**, so this project does not duplicate them. It builds only the layer they
lack — server lifecycle and transform execution.

| Need | Use |
|------|-----|
| Write or change transform code | Official SDK skills in `server/.agents/skills/` |
| Run, reload, or inspect the server | The `transformatron` MCP tools |

To author a transform, load `server/.agents/skills/maltego-transform-skill-index/SKILL.md` first —
it routes to the one focused skill for the task. Ten skills ship with the SDK and are versioned
with it; re-deriving SDK APIs from memory is how you get hallucinated code.

## Layout

```
src/transformatron/
  config.py      TransformatronConfig — host, port, scheme, derived URLs and state paths
  client.py      async v3 protocol client; the run→poll→flatten state machine lives here
  lifecycle.py   start/stop/restart/status/logs over a detached subprocess
  certs.py       self-signed cert generation for HTTPS
  mcp.py         the 11 MCP tools
server/          SDK-generated (`maltego-transforms start server --with-skills`). Upstream-owned:
                 excluded from ruff so regeneration does not churn.
tests/           24 tests
.transformatron/ runtime state — PID, log, certs. Gitignored.
```

## Getting started

```bash
uv sync
uv run pytest -q
```

The MCP server is registered at project scope in `.mcp.json`. It needs a Claude Code session
restart to load, and will prompt once for project-scope approval.

Approval is recorded in `.claude/settings.local.json` under `enabledMcpjsonServers`. That file is
per-machine, so a fresh clone prompts again. As of this writing the entry lists `context7` and
`exa` but **not** `transformatron`, so the server still shows `⏸ Pending approval` in
`claude mcp list`. Until it is approved the tools can still be driven directly:

```bash
uv run python -c "import asyncio; from transformatron.mcp import server_status; print(server_status())"
```

## The 11 tools

`server_start(ssl=False)` · `server_stop()` · `server_restart(ssl=False)` · `server_status()` ·
`server_logs(lines=50)` · `list_transforms()` · `get_transform(id)` · `list_entities()` ·
`run_transform(transform_id, entity_type, entity_value, settings=None, timeout=60)` ·
`get_seed_url()` · `generate_certs(force=False)`

## The development loop

1. Add or edit a module under `server/transforms/`.
2. Import it in `server/project.py` — the server only discovers what `project.py` imports.
3. `server_restart`.
4. `list_transforms` to confirm registration, `run_transform` to exercise it.

A transform missing from `list_transforms`, or showing output type `NONE`, almost always means a
missing or untyped annotation. Input type comes from the parameter annotation, output from the
return annotation; a bare `-> list` advertises no output type and breaks client routing.
`server_logs` is the first place to look.

## What was verified end-to-end

Against a live server, not inferred from docs. These runs used the SDK's bundled example
transforms, which have since been deleted (see *Current state*) — the observations stand, but the
fixtures are gone, so reproducing them means writing a transform first.

- 53 transforms listed with populated input/output types
- `single_entity_demo` ran to `COMPLETED` — 1 entity (`"Processed: example.com"`), 1 link
- `maltego_exception_demo` ran to `FAILED` with its real error message surfaced
- Reload loop: new module → `server_restart` → 53→54 transforms → new transform ran
- HTTPS: `generate_certs`, restart with `ssl=True`, `/seed` served over TLS
- MCP: initialize + tools/list handshake over stdio, 11 tools advertised

Still true after the deletion: server starts healthy serving 0 transforms, `/seed` returns valid
JSON, and `list_transforms` reports an empty set rather than erroring.

Gates: 24 tests pass · `ruff check` and `format --check` clean · `ty check` clean.

Four critical behaviours were mutation-tested — dropping `FINISHED` from the success states,
removing event de-duplication, removing timeout-cancel, and removing zombie reaping. Each was
caught by exactly the intended test.

## Protocol facts that shaped the code

These are non-obvious and cost real debugging time:

- **Two success states.** A run ends `COMPLETED` *or* `FINISHED`; both mean success. The server
  reports `state: "COMPLETED"` while its own status text says `ExecutionState.FINISHED`. Handling
  only one hangs the poll loop.
- **Results are events, not entities.** `result.events[].data` carries `inputType` of
  `ENTITY`/`LINK`/`STATUS_MESSAGE`. The full list is **replayed on every poll**, so the client
  tracks an offset or entities double-count.
- **`status` is an object**, not a string. The run id is at `result.runId`.
- **Port 3000**, per the docs, README, and `ServerHTTPSettings`. The SDK's own shipped skill
  scripts default to 8080 — pass `--port 3000` if invoking them directly.
- **Env vars beat explicit kwargs.** `ServerHTTPSettings` is a pydantic `BaseSettings`; the
  `MALTEGO_SERVER_*` variables override values hardcoded in `project.py`. That is how the lifecycle
  tools set host/port/scheme without editing the user's project file. `project.py` parses no CLI
  args, so flags passed to it are silently ignored.
- **Transform ids are fully qualified** — `acme.new_maltego_integration.single_entity_demo`, the
  `name` field from `GET /api/v3/transforms`, not the Python function name.

## Design notes for whoever picks this up

- **Zombie reaping in `lifecycle.py`.** Stops originally took 10.2s, always falling through to
  SIGKILL. The cause was ours: the `Popen` child was never reaped, so it lingered as a zombie and
  `os.kill(pid, 0)` kept reporting it alive. `_OWNED` tracks handles so liveness comes from
  `poll()`. Shutdown is now 0.4s. Don't remove that dict without re-testing stop timing.
- **`generate_certs` prints the trust command rather than running it.** Trusting a cert changes
  system-wide trust, so that is the user's call. Change only if you want that decision made for you.
- **`.mcp.json` passes no `--directory` at all.** It previously used `--directory "${PWD}"`, on the
  assumption Claude Code expands `${PWD}` at config-load time. It does expand `${VAR}` from the
  environment, but `PWD` is not reliably set for the spawned server process, so the argument could
  reach `uv` literally and fail on a directory named `${PWD}`. The bug was masked because the server
  had never been approved and so had never launched. The flag turned out to be unnecessary: `uv run`
  walks up from the working directory to find the project root, verified to resolve correctly even
  when launched from `src/`. Do not reintroduce it.

## Current state — uncommitted

The working tree has changes that are **not committed**. `git status`:

```
 M .mcp.json
 M server/project.py
 D server/transforms/*_example.py   (9 files)
```

**The 53 bundled example transforms were deleted.** They were SDK scaffolding, not real work —
every module was named `*_example.py`, all were namespaced `acme.new_maltego_integration.*`, and
they demonstrated SDK features against dummy data. `server/transforms/` now holds only
`__init__.py`. Recoverable from Trash and from git (they were committed clean before deletion).

Two consequences worth knowing:

- **The middleware wiring in `project.py` had to go with them.**
  `AuthorizationMiddleware`, `PolicyChecker`, and `AuditWriter` were defined *only* in
  `middleware_example.py` and are not exported by `maltego.server` — both verified. Deleting the
  module while keeping `transform_middlewares=[...]` crashes the server with `NameError` at startup.
  `run_server(settings=settings)` is now bare. Re-add middleware by defining those classes yourself.
- **`quickstart_example.py` also defined the two custom entities** (`Number`, `StaffMember`), so
  `list_entities` no longer reports them.

`server/README.md` still documents the deleted modules in roughly seven places. It is SDK-generated
and ruff-excluded, so it was left alone rather than diverging from upstream.

Gates after the change: 24 tests pass · `ruff check` and `format --check` clean · `ty check` clean.

## Not done

- **Registering the seed URL in the Maltego desktop client.** `get_seed_url()` returns
  `http://127.0.0.1:3000/seed` and the registration steps, but pasting it into the client and
  confirming the transforms install and run is a manual step that could not be done headlessly.
  This is the one unverified link in the chain.
- Cert trust has not been exercised against the Graph Browser, only that HTTPS serves.
- **No transforms exist yet.** The examples are gone and nothing has replaced them, so the server
  serves an empty set. The reload loop above is the path to the first real one.
- **`project.py` still carries the scaffold placeholders** — `server_name="New Maltego Integration"`,
  `ns="acme.new_maltego_integration"`, `author="Acme Corp"`. The namespace prefixes every transform
  id, and the server name is the label shown in the Transform Hub, so both are worth changing before
  building anything real.

## Deliberately out of scope

No custom authoring skills (the SDK ships them) · no web dashboard or database · no multi-server
registry — one server, many transform modules, by decision · no deployment tooling, local dev only.
