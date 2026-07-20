# Domain Docs

This repository uses a single domain context.

## Before working

Read:

- `CONTEXT.md` at the repository root;
- relevant ADRs under `docs/adr/`;
- the benchmark contract and privacy boundary when they exist.

If a document does not exist, continue without inventing its decisions. Use `grill-with-docs`
when domain terminology or an architectural decision needs to be resolved.

## Layout

```text
/
├── CONTEXT.md
├── docs/
│   ├── adr/
│   └── agents/
└── src/
```

Use the exact terminology defined in `CONTEXT.md` in Issues, PRs, tests, configuration, and
reports. If proposed work contradicts an accepted ADR, identify the conflict explicitly and use a
new Issue and superseding ADR instead of silently overriding the decision.
