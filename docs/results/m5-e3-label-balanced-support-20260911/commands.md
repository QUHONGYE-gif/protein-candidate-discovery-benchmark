# Reproduction entry

Symbolic paths only; actual argv/environment/stdout/stderr remain server-local. No permission to rerun,
overwrite an old attempt, or extend data/label scope is implied. Reuse the approved existing runtime:
Python3.11, NumPy2.4.6, scikit-learn1.9.0, xgboost-cpu3.2.0. Single worker/native thread.
PYTHONPATH: current source, site-support-source-v2, ridge-strength-source-v1,
hxk4-followup-source-v1, generalization-source-v1, existing benign run-source-v3/src and scripts,
and original E3 source. Full helper identities are in execution.json.

```sh
python run_m5_e3_label_balanced_support.py --study "$BENIGN_STUDY" --previous "$E3_STUDY" --output "$NEW_ATTEMPT" --plan "$FIXED_NOTE" --mode design --wall-seconds 90
python run_m5_e3_label_balanced_support.py --study "$BENIGN_STUDY" --previous "$E3_STUDY" --output "$NEW_ATTEMPT" --plan "$FIXED_NOTE" --mode smoke --wall-seconds 90 --design "$LOCKED_DESIGN" --design-sha "$DESIGN_SHA"
python run_m5_e3_label_balanced_support.py --study "$BENIGN_STUDY" --previous "$E3_STUDY" --output "$NEW_ATTEMPT" --plan "$FIXED_NOTE" --mode full --wall-seconds 700 --design "$LOCKED_DESIGN" --design-sha "$DESIGN_SHA"
```

Every output is new. Design locks all40 states before any model fit or scoring. Smoke is seed3000,
full is seeds3000–3039; full consumes the exact design result. No outcome-selected subset or tolerance.
Original support is refitted exactly; original random predictions are reused by identity.
Resource cap900 cumulative seconds,1.5GiB RSS,256MiB perattempt outputs,5MiB compact transfer.

Independent compact verification, without server/candidate/truth access:

```sh
PYTHONDONTWRITEBYTECODE=1 python scripts/verify_m5_e3_label_balanced_support.py --directory docs/results/m5-e3-label-balanced-support-20260911 --output "$NEW_VERIFICATION_JSON"
```

The verifier imports only the prior analysis verifier pinned by hash; local NumPy2.4.2.
It uses independent count-weighted resampling and does not rerun training or reconstruct raw labels.
