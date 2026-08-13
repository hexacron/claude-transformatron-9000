# Writing a Maltego transform

Read this before writing or editing a transform. It applies to humans and to coding agents alike;
nothing in it is specific to one tool.

This repository vendors the official Maltego SDK guidance under `server/.agents/skills/`. Those
files are the source of truth for SDK API surface — entity selection, settings, pagination, input
constraints, TRX migration.

**Two of their examples are wrong in ways that fail silently.** Read the corrections below first,
then use the SDK guidance for everything else.

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

**These SDK skill sections teach the broken pattern — do not follow them:**

- `maltego-transform-basics/references/transform-authoring-patterns.md` §3 "Returning a Graph
  (Multiple Entity Types)"
- `maltego-transform-build/SKILL.md` §5 "Return Entities"

Use `-> list[A | B | C]` for multiple output types. It is published to `/api/v3/transforms`
identically and it actually works.

## Correction 2: both annotations are load-bearing

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
2. **Import it in `server/project.py`** — `from transforms.my_module import *`. The server only
   registers what `project.py` imports. This is the most common reason a new transform never
   appears.
3. Restart the server — this is the reload path.
4. Confirm it registered with the right input/output types.
5. Run it against a real input.

Steps 3–5 work two ways. With an agent that has the `transformatron` MCP server configured
(currently Claude Code — see `AGENTS.md`), use the `server_restart`, `list_transforms`, and
`run_transform` tools. Otherwise use the CLI, which is what those tools call:

```bash
uv run python scripts/transformatron_cli.py restart --ssl
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
```

It runs every registered transform against a sample input and fails on zero entities or an output
type of `NONE`. Use it after any change to `server/transforms/`.

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

## Security

- Do not log entity values that may contain PII — `context.log.*` messages surface in the Maltego
  client.
- Keep API keys in `TransformSetting(auth=True, is_global=True)`; never hardcode them.
- Validate `input_entity.value` before forwarding it into URLs, query parameters, or subprocess
  calls. It is user-controlled.
