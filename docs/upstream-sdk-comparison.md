# Upstream Maltego SDK comparison

Last checked: 2026-08-17 against `maltego-transforms==1.0.1`.

Canonical upstream references:

- Package: `maltego-transforms==1.0.1`
- Repository: <https://github.com/MaltegoTech/maltego-transforms>
- Documentation: <https://docs.maltego.com/en/support/solutions/articles/15000062349-maltego-transforms-sdk-overview>

Comparison method:

```bash
uv run --project . maltego-transforms start /tmp/official_ref --with-skills
diff -qr /tmp/official_ref/.agents server/.agents
```

## Findings

The vendored SDK agent skills in `server/.agents/skills` are byte-for-byte identical to a freshly
generated official SDK starter project. This project is therefore on the correct path for
agent-facing SDK guidance: agents should route through the official skill index, then apply this
repo's corrections in `docs/transform-authoring.md` for the known silent-failure cases.

The generated official `project.py` and this repo intentionally differ:

- Official starter uses HTTP on port 8080 for quick local examples.
- This repo uses the control plane to pin host, port, identity, and scheme through
  `MALTEGO_SERVER_*` environment variables.
- This repo defaults the checked-in entrypoint to HTTPS because Maltego Desktop rejects plain HTTP
  transform servers.
- This repo supports gitignored `server/transforms/local/` packages for private integrations.

## Code adopted

The official starter includes an SDK-native transform middleware example for authorization and
auditing. This repo now carries that pattern in `server/middleware.py` and wires it into
`server/project.py` with no-op adapters:

- `PolicyChecker` / `AuthorizationMiddleware`
- `AuditWriter` / `AuditMiddleware`

That gives future agents a correct extension point for policy and audit behavior without putting
that concern inside individual transforms. The adapters are inert until replaced with real
service clients.

## Code not adopted

The official starter also includes demo transforms for quickstart behavior, prompts, pagination,
settings, input constraints, logging, and entity features. They are useful as SDK documentation,
but importing them into this repo's active server would add synthetic transforms to the Maltego
client and to the smoke-test gate. Keep using the vendored skills as the reference for those
patterns unless a real transform needs one of them.
