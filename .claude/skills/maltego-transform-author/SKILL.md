---
name: maltego-transform-author
description: "Write or modify a Maltego transform in this repository. Use when adding a transform, integrating an API as transforms, or debugging a transform that returns zero entities, does not appear in list_transforms, or shows output type NONE. Covers two SDK behaviours that fail silently."
---

# Authoring Maltego transforms

**Read `docs/transform-authoring.md` before writing code.** It is the canonical guidance and this
skill does not restate it — a second copy would drift. This file exists to make sure the checklist
below is applied every time, because two of the failure modes report success while producing
nothing.

## Non-negotiables

1. **Return a `list`, never a `MaltegoGraph`.** A graph returned from an `async` transform is
   silently discarded: zero entities, run still reports `COMPLETED (success)`. Use
   `-> list[A | B | C]`; the union still publishes every output type to discovery.
2. **Annotate both sides.** Input type comes from the first parameter's annotation, output from
   the return annotation. A bare `-> list` advertises no output type, so the transform appears
   with output `NONE` and the Maltego client cannot route it.
3. **Import the module in `server/project.py`**, with the other imports at the top of the file.
   The server registers only what `project.py` imports — the most common reason a new transform
   never appears. Do not append the import to the end of the file: it lands after the
   `if __name__ == "__main__"` block and registers nothing.
4. **Check the entity count, not the success state.** `run_transform` prints `Entities (N)`.
   Confirm N > 0.
5. **Credentials go in `TransformSetting(auth=True, is_global=True)`**, read at runtime via
   `settings.get(NAME, "")`. Never hardcode a key. Declare the setting name once as a module
   constant and use it on both sides — a literal mistyped on one side silently reads back as the
   default. Pass the list to `register_transform` as `settings=[...]` — **not**
   `transform_settings=`, which raises at import and stops the server booting.

   **Confirm the field actually reaches the client**, or a user cannot enter the credential at
   all. Restart, then check discovery publishes a `transformSettings` array:

   ```bash
   curl -sk https://127.0.0.1:3000/api/v3/transforms | grep -o '"transformSettings":[^]]*]'
   ```

   A `null` here means Desktop renders no field. Restart before believing it — a stale server
   serves the old registration and makes correct code look broken.
6. **Validate `input_entity.value` before interpolating it** into a URL path, query parameter, or
   subprocess call. It is user-controlled.

## The loop

```bash
# 1. scaffold or edit a module under server/transforms/
#    - To scaffold a new API: uv run python scripts/transformatron_cli.py scaffold --curl "..."
#    - Or use the scaffold_transform MCP tool
# 2. confirm import is in server/project.py (scaffold does this automatically)
uv run python scripts/transformatron_cli.py restart          # 3. reload
uv run python scripts/transformatron_cli.py list             # 4. confirm types
uv run python scripts/transformatron_cli.py run <id> <type> <value> --setting KEY=VALUE
```

With the `transformatron` MCP server available, `scaffold_transform`, `server_restart`,
`list_transforms` and `run_transform` do the same thing. Both front ends call `src/transformatron/operations.py`.

## Verify before saying it works

```bash
uv run python scripts/smoke_test_transforms.py --setting KEY=VALUE
```

Runs every registered transform and fails on zero entities or an output type of `NONE`. Pass each
credential with `--setting`; a transform that reports a missing setting is recorded SKIP, so an
unconfigured key is never mistaken for working code. Add a `TRANSFORM_SAMPLES` entry when the
per-entity-type sample does not suit a transform.

**A green smoke test does not prove Correction 1 is avoided.** Adding to `context.graph` and
returning it passes the gate while still being the wrong pattern. Return a list.

Project gates:

```bash
uv run ruff check . && uv run ruff format --check .
uv run ty check src tests scripts
uv run pytest -q
```

`server/transforms/` is linted; the rest of `server/` is SDK-generated and excluded.

## Routing to the SDK skills

The vendored SDK guidance in `server/.agents/skills/` is the source of truth for API surface —
entity selection, pagination, input constraints, TRX migration. Load one at a time.

| Task | Skill |
|------|-------|
| Entity modeling, transform sets, pagination design | `maltego-transform-design` |
| `@register_transform` functions, HTTP calls, settings | `maltego-transform-build` |
| Parameters, context logging, input constraints | `maltego-transform-basics` |
| Testing a running server | `maltego-transform-test` |
| Inspecting what a server exposes | `maltego-transform-discover` |
| SDK API docs and entity schemas | `maltego-transform-docs` |
| TRX migration | `maltego-trx-migration-planner` → `-implementer` |

**Two sections of that guidance teach the broken graph return — do not follow them:**

- `maltego-transform-build/SKILL.md` §5 "Return Entities"
- `maltego-transform-basics/references/transform-authoring-patterns.md` §3 "Returning a Graph"

**Port correction:** those skills default to port 8080. This project runs on **3000**.

## Worked examples

| Example | Shows |
|---------|-------|
| `server/transforms/examples/ffraud.py` | Single module, no auth — the minimal shape |
| `server/transforms/ransomwarelive/` | Multi-module, API key, shared client, upstream field drift |

Do not edit anything under `server/.agents/skills/` or `.venv/` — both are upstream-owned and
regenerate. Corrections belong in `docs/transform-authoring.md`.
