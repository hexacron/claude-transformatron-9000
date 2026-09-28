# Agent guide

Instructions for coding agents working in this repository. This is the canonical file —
`CLAUDE.md` points here. It follows the [AGENTS.md](https://agents.md) convention, which Codex,
Gemini CLI, and other agents read directly.

Humans: `README.md` is the friendlier entry point, but nothing here is agent-only.

## What this project is

An MCP server **and CLI** for authoring Maltego v3 transforms against a single local transform
server. It scaffolds a transform module from a cURL command or an OpenAPI spec, then drives the
server — restart, run, read back the entities — so you can check what you wrote against the live
API. The scaffold is a first draft; the verify loop is what makes it correct.

## Before writing a transform

**Read `docs/transform-authoring.md` first.** It documents two SDK behaviours that fail silently
— code that looks right, runs green, and produces nothing. The vendored SDK guidance in
`server/.agents/skills/` teaches one of them.

Claude Code loads the same checklist automatically from
`.claude/skills/maltego-transform-author/SKILL.md`. That file is a thin pointer to
`docs/transform-authoring.md`, not a second copy, so nothing here is Claude-only. Working through
this file instead gets you the same result.

The short version, in the order these bite:

1. Return a `list`, never a `MaltegoGraph` — a returned graph is silently discarded.
2. Annotate both the first parameter and the return type; a bare `-> list` publishes output `NONE`.
3. Import the module in `server/project.py`, or it never registers.
4. Check the entity count. A success state is not proof of anything.
5. Credentials belong in `TransformSetting(auth=True, is_global=True)`, never hardcoded.
6. Validate `input_entity.value` before putting it in a URL or a subprocess call.

## The loop

1. **Scaffold** a module package from a spec, or hand-write a module under `server/transforms/`:

   ```bash
   # From a cURL command. Pass a real response so the outputs are inferred, not guessed.
   uv run python scripts/transformatron_cli.py scaffold \
     --curl 'curl https://internetdb.shodan.io/8.8.8.8' \
     --sample-response "$(curl -s https://internetdb.shodan.io/8.8.8.8)"

   # From an OpenAPI spec, by URL or path. POST operations are skipped unless named with
   # --operation (repeatable), because they may create or change data.
   uv run python scripts/transformatron_cli.py scaffold --service demo \
     --openapi ./demo-openapi.json --operation searchHosts
   ```

   An existing service is never overwritten unless you pass `--force`, which rewrites the
   generated files — including any corrections you made to them by hand.
2. **Import it in `server/project.py`** — the server only registers what `project.py` imports.
   `scaffold` writes the import for you; a hand-written module needs it added.
3. Restart the server — it only loads code at startup.
4. Confirm it appears in `list` with the input and output types you expect.
5. Run it against a real input and **check the entity count**, not the success state.
6. Delete the output fields nobody needs and fix the mapping wherever the live response disagrees
   with the spec. The scaffolder maps every field it can see.
7. Repeat 3–6 until the entities are the ones an investigator wants.
8. Run the smoke test (below) before calling it done.

## Driving the server

Two interchangeable front ends over the same `transformatron.operations` module. Use whichever
your tool supports.

**CLI** — works anywhere you can run a command:

```bash
uv run python scripts/transformatron_cli.py status
uv run python scripts/transformatron_cli.py restart
uv run python scripts/transformatron_cli.py list
uv run python scripts/transformatron_cli.py run <id> maltego.IPv4Address 8.8.8.8
uv run python scripts/transformatron_cli.py show <id>
uv run python scripts/transformatron_cli.py logs --lines 100
```

`--help` on any subcommand lists its options. The CLI exits 0 on success and 1 when the operation
failed — a start, stop, restart or certificate generation that failed, a server that could not be
reached (`status` included), a run that did not end in a success state, a refused scaffold, or an
unusable `transformatron.toml`. A run that completes with zero entities still exits 0: the count
in the output is the signal, not the exit code.

**MCP** — for agents that speak it (Claude Code is configured in `.mcp.json`): `server_start`,
`server_stop`, `server_restart`, `server_status`, `server_logs`, `list_transforms`,
`get_transform`, `list_entities`, `run_transform`, `get_seed_url`, `generate_certs`, `scaffold_transform`.

Both produce identical output. If you add an operation, put it in
`src/transformatron/operations.py` so both front ends get it.

### The MCP tools can hold stale code

The MCP server is one long-lived process. It imports `src/transformatron/` once at startup, so
**editing that code does not change what the MCP tools run** — they keep executing the version
loaded when the session began. The CLI is a fresh process each time and always runs current code.

This applies only to edits under `src/transformatron/`. Editing `server/transforms/` is unaffected:
`server_restart` restarts the transform server subprocess, which does reload your transform
modules. You do not need to relaunch a session after changing a transform.

`transformatron.toml` is not code: every tool reads it afresh on each call, so an edit applies on
the next call without relaunching. The transform server still takes its identity, host and port
at start, so restart it after changing them.

`/clear` does not help — it resets the conversation but keeps the same process, so the MCP server
keeps its loaded modules. Use `/exit` and relaunch.

**If MCP and the CLI disagree about the server, suspect this first and trust the CLI.** That
disagreement is the signature; the stale tools otherwise look like they are working.

## Verifying your work

**A success state is not proof a transform works.** It can report `COMPLETED (success)` while
returning nothing. Always check the entity count.

```bash
uv run python scripts/smoke_test_transforms.py
uv run python scripts/smoke_test_transforms.py --setting API_KEY=xxx
```

Runs every registered transform and fails on zero entities or an output type of `NONE`. Run it
after any change under `server/transforms/`.

Pass credentials with repeated `--setting KEY=VALUE`, or write them to `.env` (copy
`.env.example`), which the server and the smoke test both read. An exported shell variable
overrides `.env`, and `--setting` overrides both. A transform that reports a missing setting is
recorded SKIP rather than FAIL, so an unconfigured key is never mistaken for broken code — but it
also is not evidence the transform works. Supply the setting to actually exercise it. When the
per-entity-type sample does not suit a transform, add a `TRANSFORM_SAMPLES` entry keyed by the
transform id suffix.

Project gates:

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run ty check src tests scripts
```

## Constraints

- **Do not edit anything under `server/.agents/skills/` or `.venv/`.** Both are upstream-owned and
  regenerate; corrections belong in `docs/transform-authoring.md`. See `CONTRIBUTING.md`.
- `server/.agents/`, `server/project.py` and `server/__init__.py` are SDK-generated and excluded
  from linting. The rest of `server/` — `transforms/` (scaffolded code included), `middleware.py`,
  `discovery.py` — is linted like the rest of the project.
- The server runs on port **3000** unless `transformatron.toml` sets another. The SDK's own skill
  scripts default to 8080.
- macOS and Linux only. The lifecycle relies on POSIX signals; on Windows `os.kill(pid, 0)`
  terminates the process it is meant to probe.
- The Maltego desktop client requires HTTPS — plain HTTP fails inside the client with nothing in
  the server log.

## Layout

```
src/transformatron/
  operations.py  shared server operations — add new ones here
  config.py      host, port, scheme, derived URLs and state paths; reads transformatron.toml
  client.py      async v3 protocol client
  lifecycle.py   start/stop/restart/status/logs
  certs.py       self-signed certificate generation
  envfile.py     reads .env so headless runs can reach credentials
  scaffold/      spec → module package: parser.py, generator.py, schema.py
  mcp.py         MCP front end
scripts/
  transformatron_cli.py      CLI front end
  smoke_test_transforms.py   zero-entity gate
server/          SDK-generated; project.py imports decide what registers
  transforms/    your transform modules, scaffolded or hand-written
    local/       gitignored; discovered at startup, for integrations you do not publish
docs/            transform-authoring.md — read before writing transforms
transformatron.toml  server name, namespace, author, host, port. Gitignored; copy the .example.
.env             API keys for headless runs. Gitignored; copy .env.example.
