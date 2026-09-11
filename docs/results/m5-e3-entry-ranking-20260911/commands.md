# Reproduction entry

Symbolic paths only. Actual argv/environment/stdout/stderr remain server-local. These commands do
not grant permission to rerun, overwrite successful attempts, or expand data/label/evidence scope.

Reuse the original Python3.11 runtime, NumPy2.4.6, scikit-learn1.9.0 and xgboost-cpu3.2.0.
Single worker/native thread; no fit. PYTHONPATH in order: current source, label-balanced-support-source-v1,
site-support-source-v2, ridge-strength-source-v1, hxk4-followup-source-v1, generalization-source-v1,
existing benign run-source-v3/src and scripts, and original E3 source. Helper SHAs are in execution.json.

```sh
python analyze_m5_e3_entry_ranking.py --study "$BENIGN_STUDY" --previous "$E3_STUDY" --output "$NEW_ATTEMPT" --plan "$FIXED_NOTE" --mode smoke --wall-seconds 45
python analyze_m5_e3_entry_ranking.py --study "$BENIGN_STUDY" --previous "$E3_STUDY" --output "$NEW_ATTEMPT" --plan "$FIXED_NOTE" --mode full --wall-seconds 100
```

Full uses all40 original states and requires smoke success. Each attempt locks all choices before
constructing the evaluator; the public evaluator scores only batches of16. No private truth access
or revealed labels enter the worker. Existing full result and each prediction/selection cache are
checked by identity. Every output is new; cumulative300s/1.5GiB RSS/64MiB perattempt/5MiB compact caps.

Independent compact verification with local NumPy2.4.2, no raw prediction/label access:

```sh
PYTHONDONTWRITEBYTECODE=1 python scripts/verify_m5_e3_entry_ranking.py --directory docs/results/m5-e3-entry-ranking-20260911 --output "$NEW_VERIFICATION_JSON"
```

The verifier imports only the pinned previous analysis verifier and uses independent count-weighted
resampling. It does not import the experiment entry or reconstruct raw candidate-level selections.
