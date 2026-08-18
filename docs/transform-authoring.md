# Writing a Maltego transform

Read this before writing or editing a transform. It applies to humans and to coding agents alike;
nothing in it is specific to one tool.

This repository vendors the official Maltego SDK guidance under `server/.agents/skills/`. Those
files are the source of truth for SDK API surface — entity selection, settings, pagination, input
constraints, TRX migration.
See `docs/upstream-sdk-comparison.md` for the latest comparison against a freshly generated
official SDK starter.

**One of their examples is wrong in a way that fails silently** — Correction 1 below, verified
against SDK 1.0.1 at the upstream HEAD of 2026-08-10. Correction 2 is not a divergence: the SDK
teaches it correctly, and it is repeated here because it is the most common way a transform lands
in the client unusable. Read both, then use the SDK guidance for everything else.

## Correction 1: never return a `MaltegoGraph`

A `MaltegoGraph` returned from an `async` transform is **silently discarded**. You get zero
entities and the run still reports `COMPLETED (success)`, so nothing surfaces the mistake.

```python
# WRONG — returns nothing, reports success
async def domain_profile(input_entity: Domain, context: MaltegoContext) -> MaltegoGraph:
    graph = MaltegoGraph()
    graph.add_entity(EmailAddress(value="admin@example.com"))
    return graph


# RIGHT — return a list; the union annotation still publishes every output type
async def domain_profile(
    input_entity: Domain, context: MaltegoContext
) -> list[EmailAddress | Person]:
    return [EmailAddress(value="admin@example.com"), Person(value="Domain Admin")]
```

Mechanism, so you can check it yourself: in `MaltegoTransform.__handle_async_result`
(`maltego/model/transform/__init__.py:651`), `MaltegoGraph` sits in the first `isinstance` tuple
so the first branch is skipped, and the `elif not isinstance(async_res, MaltegoGraph)` excludes it
again. A returned graph falls through both and is dropped. The generator branch in
`__add_async_results` skips graphs too, so this affects **every** async path.

Only a **freshly constructed** graph loses entities. Adding to the context graph and returning it
still publishes them, because they were registered on the context before the return value was
discarded:

```python
# Entities survive — but only by accident; the return value is still dropped
context.graph.add_entity(EmailAddress(value="admin@example.com"))
return context.graph
```

That variant passes `scripts/smoke_test_transforms.py`, so a green smoke test does not prove a
transform avoids this trap. Return a list and the question does not arise.

**These SDK skill sections teach the broken pattern — do not follow them:**

- `maltego-transform-basics/references/transform-authoring-patterns.md` §3 "Returning a Graph
  (Multiple Entity Types)"
- `maltego-transform-build/SKILL.md` §5 "Return Entities"

Use `-> list[A | B | C]` for multiple output types. It is published to `/api/v3/transforms`
identically and it actually works.

## Correction 2: both annotations are load-bearing

Not a divergence from the SDK — it teaches this correctly — but it is the most common way a
transform ends up registered yet unusable, so it is worth repeating.

Input type comes from the **first parameter's** annotation; output type comes from the **return**
annotation. Both are read at registration and published to discovery.

A bare `-> list`, or no return annotation, advertises no output type. The transform still appears
in `list_transforms` but shows output `NONE`, and the Maltego client cannot route it onto result
entities.

```python
async def my_transform(input_entity: IPv4Address, context: MaltegoContext) -> list[Domain]:
```

## The loop

1. Add or edit a module under `server/transforms/`.
   - For new APIs, use `scaffold_transform` (MCP) or `uv run python scripts/transformatron_cli.py scaffold --curl "..."`
     to automatically generate the validated `api.py` and transform modules.
2. **Import it in `server/project.py`** — `from transforms.my_module import *`, alongside the
   existing imports at the top of the file. (The scaffolder does this automatically).
   The server only registers what `project.py` imports.
3. Restart the server — this is the reload path.
4. Confirm it registered with the right input/output types.
5. Run it against a real input.

Steps 1 and 3–5 work two ways. With an agent that has the `transformatron` MCP server configured
(currently Claude Code — see `AGENTS.md`), use the `scaffold_transform`, `server_restart`, `list_transforms`, and
`run_transform` tools. Otherwise use the CLI, which is what those tools call:

```bash
uv run python scripts/transformatron_cli.py restart
uv run python scripts/transformatron_cli.py list
uv run python scripts/transformatron_cli.py run <transform-id> maltego.IPv4Address 8.8.8.8
```

## Verification gate

**A success state is not evidence that a transform works.** The failure mode above produces
`State: COMPLETED (success)` with nothing in the graph.

`run_transform` prints `Entities (N)`. **Check that N > 0.** If a transform legitimately returns
nothing for a given input, verify with an input that should produce results.

Run the whole suite at once:

```bash
uv run python scripts/smoke_test_transforms.py
uv run python scripts/smoke_test_transforms.py --setting RANSOMWARE_LIVE_API_KEY=xxx
```

It runs every registered transform against a sample input and fails on zero entities or an output
type of `NONE`. Use it after any change to `server/transforms/`.

### Testing transforms that need credentials

Pass each one with a repeated `--setting KEY=VALUE`, the same form the CLI uses. Two outcomes are
deliberately reported as SKIP rather than FAIL, because treating them as failures teaches people
to ignore the gate:

- **A missing credential.** The transform says so explicitly; supply `--setting` to exercise it.
- **An input with no upstream match.** One sample per entity type cannot suit every transform — a
  `Company` sample that exercises a search is a substring, while a lookup needs a full
  organisation name. Add a `TRANSFORM_SAMPLES` entry, keyed by transform id suffix, to give a
  specific transform a better input.

A SKIP is not evidence a transform works. It means the gate could not judge it.

## Routing to the SDK skills

For anything not covered above, load
`server/.agents/skills/maltego-transform-skill-index/SKILL.md` — a routing table pointing to one
focused skill per task. Load one at a time; load its `references/` only when needed.

| Task | Skill |
|------|-------|
| Entity modeling, transform sets, pagination design | `maltego-transform-design` |
| Writing `@register_transform` functions, HTTP calls, settings | `maltego-transform-build` |
| Parameters, context logging, input constraints | `maltego-transform-basics` |
| Testing a running server | `maltego-transform-test` |
| Inspecting what a server exposes | `maltego-transform-discover` |
| SDK API docs and entity schemas | `maltego-transform-docs` |
| TRX migration | `maltego-trx-migration-planner` → `-implementer` |

**Port correction:** those skills default to port **8080** in their curl examples and scripts. This
project runs on **3000**. Pass `--port 3000`, or use the `transformatron` MCP tools, which already
target the right port and scheme.

## Worked example

`server/transforms/examples/ffraud.py` is a working reference. It demonstrates the correct
union-list return, validating `input_entity.value` before interpolating it into a URL, catching
`MaltegoException` so an upstream outage degrades to an empty result rather than a crash, and
using `.get()` throughout because the upstream omits fields rather than nulling them.

`server/transforms/ransomwarelive/` is a larger, multi-module set built on an authenticated API —
see `docs/ransomware-live.md`. It shows a shared `api.py` holding the API key setting, input
validation and error handling, so the transform modules stay declarative, plus result caps and a
schema whose field names differ between endpoints. Verify it with the project smoke test and
`--setting RANSOMWARE_LIVE_API_KEY=<key>`.

## API keys and the client re-import trap

Declare a credential as `TransformSetting(auth=True, is_global=True)` and read it with
`settings.get(NAME, "")`. `is_global=True` makes the Maltego client store one value for the whole
namespace, so it is entered once. The SDK publishes the setting to discovery under a namespaced
name (`global#<ns>.<NAME>`) and strips that prefix again before your transform sees it — look it
up by the bare name.

**Re-importing the seed can orphan the stored value.** Symptom: the transform reports the
credential as missing while the client's settings field still looks populated. Re-importing after
the seed URL changes — switching the server between HTTP and HTTPS does this — rewrites the
transform definitions, and the previously stored global value no longer resolves against the new
registration. Clear the field, apply, and re-enter the key.

Be aware this trigger was inferred from the client's on-disk timestamps, not confirmed by
capturing the client's request body; re-entering the key resolves several possible causes. If it
recurs without a scheme change, capture the raw `POST /run` body and check what the
`transformSettings` array actually contains.

For local development, a transform can fall back to an environment variable when the client
setting is empty, as `server/transforms/ransomwarelive/api.py` does:

```python
api_key = settings.get(API_KEY, "") or os.environ.get(API_KEY, "")
```

The server subprocess inherits the parent shell's environment (`lifecycle.build_server_env`), so a
key exported once survives restarts and re-imports. The client setting still wins. This is a
development convenience — it puts the key in the process environment, which is fine for a local
server and not appropriate for a shared deployment.

### Writing keys down: `.env`

Exporting works but is easy to get wrong — an export does not survive between shells, and a key
set after the server started never reaches it. Write the keys to `.env` at the repository root
instead:

```bash
cp .env.example .env   # then fill in the keys you have
```

`build_server_env` merges that file into the server subprocess on every start, and
`scripts/smoke_test_transforms.py` reads it too, so credential-gated transforms are exercised
rather than reported SKIP. **Restart the server after editing it** — the environment is read once
at start. An exported shell variable still overrides the file, and an explicit `--setting` still
overrides both.

`.env` is gitignored; `.env.example` is the committed template and must never hold a real key.

**A wrong key can be worse than no key.** urlscan answers HTTP 400 for an `api-key` header it does
not recognise, including on endpoints that work fine anonymously — so a placeholder left in `.env`
breaks transforms that would otherwise pass. Leave a key blank rather than filling it with
something fake.

## Keeping an integration out of the repository

Anything under `server/transforms/local/` is gitignored and discovered at server start by
`_register_local_transforms` in `server/project.py`. Use it for integrations that should not be
published; the committed packages alongside it are reference examples and stay imported by name.

```
server/transforms/local/<service>/{__init__,api,<transform>}.py
```

Discovery imports every module in each package except `api.py`, so no `project.py` edit is needed
— which also means the usual "did you add the import?" failure does not apply there. A clone
without the directory still boots, and only `local/__init__.py` is tracked.

## Security

- Do not log entity values that may contain PII — `context.log.*` messages surface in the Maltego
  client.
- Keep API keys in `TransformSetting(auth=True, is_global=True)`; never hardcode them.
- Validate `input_entity.value` before forwarding it into URLs, query parameters, or subprocess
  calls. It is user-controlled.
