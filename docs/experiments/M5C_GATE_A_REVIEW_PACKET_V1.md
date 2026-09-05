# M5C Gate A review packet v1

## Status and authority

This packet materializes the smallest reviewable M5C Governance v2 contract for
[MIRA-13](https://multica-server.tail8b7421.ts.net/issues/MIRA-13), under parent
[MIRA-10](https://multica-server.tail8b7421.ts.net/issues/MIRA-10). Multica is the sole tracking
authority; no GitHub parent Issue is created.

The candidate manifest is
`manifests/experiments/m5c/gate-a-proposal-v1/milestone-contract-manifest.json`. Its
`manifest_status: example` and the referenced Gate A record's `record_status: example` mean the
packet is schema-testable but **does not authorize execution**. After technical review and the
confirmations below, the final decision record must be regenerated, bound to fresh authenticated
GitHub comments from both scientific authorities, and only then may the manifest become
`authorized`.

Contract identity:

- ID/version: `m5c-proteingym-prospective-v1` / `1`;
- scientific-contract digest: `c8ed6e2af08c2afa3c3316f650124835f01be510a41826db55daba13bc3a5730`;
- base: `origin/main@9f332c2935b53ab1697688d0024944bc12a99161`;
- PR class: `Major / semantic PR`;
- authorities: `github:QUHONGYE-gif`, `github:willzhang`.

## Existing decisions used directly

- The revised M4–M7 plan fixes the complete four-assay cohort, result-independent eligibility,
  assay-level inference, single-step G/E/Matched confirmation and the prohibition on treating seeds
  as independent assays: `docs/M4_TO_M7_REVISED_EXPERIMENT_PLAN.md`.
- Issue #11 fixes the cohort purpose and exclusion boundary in
  [comment 5336871661](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/11#issuecomment-5336871661),
  and its completed immutable readiness identity is independently accepted in
  [comment 5357910563](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/11#issuecomment-5357910563).
- M5B's final scientific review fixes the one-step contrast meanings, preserves `INCONCLUSIVE`,
  prohibits a behavioral-redundancy claim and defers aggregate taxonomy inputs to M5C:
  [comment 5509182540](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/issues/91#issuecomment-5509182540).
- Governance v2 and ADR-0005 make M5C server-first, append-only and zero-COS-write during ordinary
  runs; Gate A must bind all four major boundaries and both configured authorities.

## Frozen proposal

The candidate manifest is normative for review; this section is a navigation summary.

1. **Question and cohort.** Test whether the frozen same-state one-step intervention has immediate
   discovery value relative to Greedy and overlap-matched Random, and whether mechanism labels
   replicate, reverse or differ across all four independent assays. M5B is motivation only.
2. **Data and eligibility.** Bind ProteinGym v1.3 readiness config
   `ee631056…` and unified terminal `5dad6220…`. Exclusion is permitted only before any outcome read
   for a retained failure of the six frozen readiness/leakage checks.
3. **Split and features.** Each assay's complete candidate-pool is its independent replay universe.
   Initial 32/64 observations are nested truth-blind SHA-256 samples for seeds 1729–1738. RF and
   XGBoost use only stateless `sequence-composition-420-v1`; there is no cross-assay fitting.
4. **Static proxy baseline.** Proposed proxy is the exact ProteinGym column
   `esm1v_t33_650M_UR90S_1`, direction +1, requiring 100% finite coverage with no imputation. It is
   evaluated separately and never enters the RF/XGBoost one-step arms.
5. **One-step arms.** Reuse the exact frozen RF/uncertainty and XGBoost/UCB pairs, each with
   model-matched Greedy and 64 overlap-matched Random replicates; batch size is 16.
6. **Primary endpoint.** Per-assay immediate one-batch upper-1%-recall increments E-G and E-M.
   Upper 0.5%/2% are sensitivity only; one nominal hit (`1/K`) is the equivalence margin.
7. **Uncertainty and classification.** Forty paired states per assay, 20,000 deterministic
   seed-cluster/matched-replicate bootstrap draws, 99.375% type-7 intervals for the eight primary
   tests, frozen M5B contrast/taxonomy precedence, and a prospective replicated/heterogeneous/
   inconclusive cohort roll-up. No pooled-seed inference is allowed.
8. **Auditability.** Aggregate output must expose every non-sensitive classifier input requested by
   the M5B review: counts, 40 divergence counts, cell estimates, interval/status, sensitivity inputs,
   validity flags and attempt selection. Candidate identities and truth remain private.
9. **Execution slice.** One sealed slice contains the four-assay M2-static baseline and one-step
   comparison. No conditional multi-round run is authorized by v1.

## Outcome-free server observation

Read-only observation on 2026-09-05 found:

- `protein-6000` resolves to an authenticated host whose kernel hostname is `ps`;
- 192 logical CPUs; 263,713,240 kB RAM total; 203,107,364 kB RAM available;
- 122,686,394,368 bytes currently free on the candidate filesystem;
- `/srv/pcdb` and Unix group `pcdb` are absent;
- `protein-6001` was not accessible with the available key.

Therefore no target is currently Governance v2-ready. No directory, group, account, data, process,
credential or infrastructure was created or changed during this check.

## One confirmation list

These are the only unresolved items. The recommendation is part of the proposal, not an
authorization.

1. **Proxy.** Confirm `esm1v_t33_650M_UR90S_1`, +1 direction and 100% finite per-assay coverage as
   the outcome-independent proxy rule. Recommendation: accept because it is a single-sequence,
   no-retrieval, one-column baseline with no imputation; require fresh score-coverage readback before
   contract authorization. The drafting-context exposure disclosed in `known_risks` must be
   considered explicitly.
2. **Scientific contract.** Confirm the complete cohort, nested 32/64 initial samples, ten seeds,
   sequence-composition-only surrogate features, two primary contrasts, 99.375% intervals, `1/K`
   margins, assay/cohort taxonomy and no multi-round coverage. Recommendation: accept as the closest
   outcome-independent continuation of M5B with assay as the unit.
3. **Server and provisioning.** Confirm `protein-6000`/`ps` as the target and authorize an operator
   to provision the exact configured `/srv/pcdb` namespace, `pcdb` group, writer membership,
   service accounts and backup/RPO. Recommendation: use this host only after
   `pcdb governance-check server` passes with the estimated peak and its current free space still
   satisfies the 50% target/70% hard cap.
4. **Main and reviewer caps.** Confirm the cumulative main caps (24 h wall across at most two
   attempts, 384 CPU h, 48 GB RSS, 30 GiB scratch, 4 GiB network, 100 CNY) and Mika's aggregate-only
   reproduction caps (1 h wall, 2 CPU h, 4 GB RSS, 2 GiB scratch, 1 GiB network, 5 CNY).
   Recommendation: accept as ceilings, not allocations; stop rather than expand if the outcome-free
   peak estimate fails.
5. **Operations.** Confirm read-only staging of exactly Issue #11's 45-object legacy prefix into the
   server-first plane; evaluator-only DMS truth use; GitHub branch/PR/CI work; zero ordinary-run COS
   writes; and, only after a valid Gate B, an immutable compact archive under
   `milestone-archives/v2` with no overwrite/delete and `_SUCCESS` last. Recommendation: authorize
   these exact operations, but withhold M5D/M6, pool-external truth, multi-round, new paid resources,
   raw-data publication and evidence promotion.
6. **Mika reproduction.** Confirm aggregate-only recomputation of exact hashes, 20,000-draw
   intervals, taxonomy and cohort roll-up from sealed aggregate inputs; no candidate-level truth or
   second scientific result generation. Recommendation: accept; expand only if Mika identifies a
   concrete conclusion-validity gap before Gate A.

## Gate sequence

1. Mika reviews this exact contract from fresh context and records evidence/findings in MIRA-13.
2. Moka resolves necessary findings in this PR; optional engineering improvements remain deferred.
3. Generate the final Gate A JSON and exact decision marker with
   `pcdb governance-decision-digest`.
4. Both `github:QUHONGYE-gif` and `github:willzhang` post fresh authenticated comments containing
   the exact marker and explicit approval of all four major boundaries. GitHub review or CI alone is
   insufficient.
5. Bind both attestations, change the manifest to `authorized`, run the final production contract
   check, and update `docs/STATUS.md` for the Gate A scientific transition. Only then may Stage 2 be
   promoted.

No formal experiment, held-out access, label reveal, COS write, server provisioning, M5D/M6 start or
evidence promotion is performed by this packet.
