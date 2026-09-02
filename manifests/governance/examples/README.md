# Governance v2 examples

`m5-example/` demonstrates the exact `Milestone Contract Manifest`, Gate A, Gate B and server-first
run identity shapes. Every decision has `record_status: example`; it is test data, not scientific
authorization, evidence or a historical milestone. Production checks reject example records unless
the explicit developer-only `--allow-example` flag is used.

The manifest binds the canonical SHA-256 of its `scientific_contract` object and the exact bytes of
each decision/input. A terminal revision adds the Gate B reference without rewriting the frozen
scientific contract digest. Production revisions are append-only artifacts; the example file is
only maintained alongside code and schemas.
