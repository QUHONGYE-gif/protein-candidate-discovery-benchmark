# Reproduction and identities

All paths below are symbolic. Actual argv/environment/stdout/stderr remain server-local;
source/config/data hashes are in execution.json. Reuse only the approved existing cache and evaluator.
No new package installation, dataset, truth permission or storage upload is implied.

Runtime: Python 3.11; NumPy 2.4.6; scikit-learn 1.9.0; xgboost-cpu 3.2.0.
One worker, OPENBLAS_NUM_THREADS=OMP_NUM_THREADS=MKL_NUM_THREADS=1.
PYTHONPATH uses the staged current source, ridge-strength-source-v1, hxk4-followup-source-v1,
generalization-source-v1, existing benign run-source-v3/src and scripts, and original E3 source.
Do not change these existing helper versions. Every output directory must be new.

```sh
python run_m5_e3_site_support.py --study "$BENIGN_STUDY" --previous "$E3_STUDY"   --output "$NEW_ATTEMPT" --plan "$FIXED_NOTE" --mode "$MODE" --wall-seconds "$CAP"
```

Original diagnostic used source v1 (recoverable by reversing source-correction.patch from v2),
mode diagnostic, states3000–3039, cap90. It produced the locked design result SHA
adb22c0eadfb813651fc05d3b0cbd17c81891a8118dee363e195e3089483cf5d.
The failed smoke001 also used v1. Corrected smoke003/full001 use v2 and consume that exact locked
artifact, at the historical site-support-diagnostic-001 directory under E3_STUDY.
Mode smoke selects3000/cap90; mode full uses all40/cap700. Cumulative wall cap900seconds,
RSS1.5GiB, perattempt output256MiB, compact5MiB. This is a provenance recipe, not permission
to rerun or overwrite diagnostic/source directories. A new independent run needs its own identity binding.
The launcher-only smoke002 failure, one extra startup beyond the literal retry count, is retained.

Run compact arithmetic without candidate/truth/server access:

```sh
PYTHONDONTWRITEBYTECODE=1 python scripts/verify_m5_e3_site_support.py   --directory docs/results/m5-e3-site-support-20260911   --output "$NEW_VERIFICATION_JSON"
```

The verifier imports only the earlier analysis verifier, pinned by hash, and uses count-weighted
resampling to independently reconstruct intervals. Local NumPy2.4.2. It does not reconstruct raw
candidate-level labels or redo model training. The exact source diff leaves prepare/fit unchanged.
