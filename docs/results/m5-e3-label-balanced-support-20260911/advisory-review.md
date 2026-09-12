# Independent scientific advisory review

2026-09-11. Two separate fresh contexts; read-only, no model execution, server/raw-truth access,
file mutation or GitHub posting by the reviewers. Advisory advice is not formal approval.

## Reviewed identities

- Note: `50895a0e0af18aa5486ea16845c3fb89d67312e518874092d615e84737ab613c`.
- Executed source: `239485d48468bcfc467fb6f464a81e23469b3797d7830a706c0f48c1323af080`.
- Full result: `3dd011bd63f5d9c844d3678a4368c8830a4af17add3158a1a227ea2517a88b95`.
- First README: `cf41ff05e85afe8be41366d264735bfa9efa39d24666fff1ec72d50ab8561d91`.
- Revised README, second fresh review: `be92b7d36a36f07e0c676e81d29a3cf424e1263a69dd332f32b424bd4338bf3c`.
- Evidence map, both reviews: `338d15727c5044a35361cffad707f926bf2f8cd6f82103714329826a06409bda`.

Both reviews inspected the fixed note, actual source, eight pinned helpers, design/smoke/full
artifacts and event order, attempt inclusion, and proposed claims. The first independently checked
481 compact means/definedness/identities and 20 bootstrap intervals; these were consistent.
The second checked the revised claims against actual worker dataflow, all 40 states, unchanged
designs, smoke/full consistency and primary means. These reviews do not replace or expand the
13,548 checks in [independent-verification.json](independent-verification.json).

## First recommendation: NARROW

Two MATERIAL findings required wording changes, not a rerun:

1. Calling the auxiliary fixed-pool response "confirmed" exceeded the exploratory evidence.
   The README now says it was observed, retaining the primary intervals and correlated-endpoint limits.
2. The worker received the complete previous result row, including historical aggregate metrics;
   `prepare()` also read the previous diagnostic result containing aggregate groups. The original
   minimal-input wording was therefore inaccurate. Only identity fields were used from these objects
   in subset construction/fitting/selection; historical scores did not enter those numerical paths.
   The actual deviation is now disclosed. The prospective note, executed source and successful
   results remain unchanged; a rerun would not erase the deviation or create independent evidence.

## Second fresh recommendation: ACCEPT the narrowed claim

No FATAL finding or mandatory rerun. Three MATERIAL limitations remain part of the accepted wording:

- **Input boundary:** disclosure now distinguishes receiving fields from using them. Previous
  metrics are used for historical score consistency only after workers exit. Future code changes
  should pass only necessary identity fields; this is not a requirement to run another experiment.
- **Residual confounding:** A has a limited support increment and changing sample identities;
  B only partly matches labels and fixes support within the reference64, not the complete pool.
  Stronger support, remaining label differences and other inputs are not excluded.
- **Inference scope:** all four primary intervals contain zero. Auxiliary fixed-pool responses are
  not full-pool benefits or independent mediators. The 40 seeds describe one seen assay, and exact
  historical reconstruction is not replication or independent biological confirmation.

Allowed claim: two complementary comparisons reduced part of the joint variation. The specified
composition adjustment showed exploratory, conditional fixed-pool responses, without establishing
full-pool primary discovery benefit or a unique cause.

Prohibited claims: zero effect/equivalence, pure support or label causal effects, confirmed mediation,
exploration advantage, cross-assay generalization, 32-label acquisition efficiency, or formal replication.

Confidence is high for the inspected source dataflow and consistency of the narrowed claim with
compact artifacts. Raw sample/label arrays were not independently reconstructed; their binding still
depends on the server runner's checks. No OS-level isolation guarantee is given.

Evidence remains **ITERATION/L1 exploratory**. The designated non-author **QUHONGYE-gif** formal
scientific review is pending. Neither review grants promotion, another experiment, Ready or merge.
