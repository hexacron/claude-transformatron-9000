# Agent guide

Instructions for coding agents working in this repository. This is the canonical file —
`CLAUDE.md` points here. It follows the [AGENTS.md](https://agents.md) convention, which Codex,
Gemini CLI, and other agents read directly.

Humans: `README.md` is the friendlier entry point, but nothing here is agent-only.

## What this project is

An MCP control plane **and CLI** for a single local Maltego v3 transform server. It handles the
server lifecycle and transform execution. It does not help you write transform code.

## Before writing a transform

**Read `docs/transform-authoring.md` first.** It documents two SDK behaviours that fail silently
— code that looks right, runs green, and produces nothing. The vendored SDK guidance in
`server/.agents/skills/` teaches one of them.

## The loop

1. Add or edit a module under `server/transforms/`.
2. **Import it in `server/project.py`** — the server only registers what `project.py` imports.
3. Restart the server.
4. Confirm it registered, then run it.

## Driving the server

Two interchangeable front ends over the same `transformatron.operations` module. Use whichever
your tool supports.

**CLI** — works anywhere you can run a command:

```bash
uv run python scripts/transformatron_cli.py status
uv run python scripts/transformatron_cli.py restart
uv run python scripts/transformatron_cli.py list
uv run python scripts/transformatron_cli.py run <id> maltego.IPv4Address 8.8.8.8
uv run python scripts/transformatron_cli.py logs --lines 100
```

`--help` on any subcommand lists its options.

**MCP** — for agents that speak it (Claude Code is configured in `.mcp.json`): `server_start`,
`server_stop`, `server_restart`, `server_status`, `server_logs`, `list_transforms`,
`get_transform`, `list_entities`, `run_transform`, `get_seed_url`, `generate_certs`.

Both produce identical output. If you add an operation, put it in
`src/transformatron/operations.py` so both front ends get it.

## Verifying your work

**A success state is not proof a transform works.** It can report `COMPLETED (success)` while
returning nothing. Always check the entity count.

```bash
uv run python scripts/smoke_test_transforms.py
```

Runs every registered transform and fails on zero entities or an output type of `NONE`. Run it
after any change under `server/transforms/`.

Project gates:

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run ty check src tests scripts
```

## Constraints

- **Do not edit anything under `server/.agents/skills/` or `.venv/`.** Both are upstream-owned and
  regenerate; corrections belong in `docs/transform-authoring.md`. See `CONTRIBUTING.md`.
- `server/` is SDK-generated and excluded from linting.
- The server runs on port **3000**. The SDK's own skill scripts default to 8080.
- The Maltego desktop client requires HTTPS — plain HTTP fails inside the client with nothing in
  the server log.

## Layout

```
src/transformatron/
  operations.py  shared server operations — add new ones here
  config.py      host, port, scheme, derived URLs and state paths
  client.py      async v3 protocol client
  lifecycle.py   start/stop/restart/status/logs
  certs.py       self-signed certificate generation
  mcp.py         MCP front end
scripts/
  transformatron_cli.py      CLI front end
  smoke_test_transforms.py   zero-entity gate
server/          SDK-generated; project.py imports decide what registers
docs/            transform-authoring.md — read before writing transforms
```
