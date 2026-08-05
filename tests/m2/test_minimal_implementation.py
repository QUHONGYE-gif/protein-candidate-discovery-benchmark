from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

import pcdb.m2.models as m2_models
from pcdb.cli import main
from pcdb.contracts.experiment import (
    ContractValidationError,
    canonical_json_bytes,
    load_experiment_config,
)
from pcdb.contracts.feature import M2_FEATURE_TASK_BINDINGS
from pcdb.contracts.feature_identity import (
    LoadedFeatureMatrix,
    encode_feature_matrix,
    load_feature_matrix,
    validate_feature_bundle_identity,
)
from pcdb.contracts.prediction import (
    M2StaticProducerInputs,
    load_prediction_artifact,
)
from pcdb.datasets.processing import ProcessingConfigLike
from pcdb.m2.features import (
    ALPHABET,
    FEATURE_CONFIG_SHA256,
    CandidateSequence,
    SequenceFeatureBundle,
    build_sequence_feature_bundle,
    build_sequence_feature_rows,
    sequence_feature_config_bytes,
    sequence_feature_definitions,
)
from pcdb.m2.models import (
    MODEL_CONFIG_SHA256,
    DevelopmentObservation,
    fit_regressor,
    load_model_config,
    model_config_bytes,
    model_effective_parameters,
    predict_regressor,
)
from pcdb.m2.preflight import run_frozen_preflight
from pcdb.m2.producer import (
    SYNTHETIC_ATTEMPT_ID,
    build_synthetic_run_spec,
    make_static_prediction_producer,
)
from pcdb.m2.projection import ProjectedInputs

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/contracts"


@dataclass(frozen=True)
class _ProcessingConfig:
    candidate_id_namespace: str = "public-synthetic-v1"


def _candidate_id(namespace: str, sequence: str) -> str:
    return hashlib.sha256(f"{namespace}\0{sequence}".encode()).hexdigest()


def _loaded_matrix(
    records: list[CandidateSequence],
) -> tuple[LoadedFeatureMatrix, tuple[dict[str, object], ...]]:
    rows = build_sequence_feature_rows(records)
    payload = encode_feature_matrix(rows, feature_definitions=sequence_feature_definitions())
    return (
        load_feature_matrix(payload, feature_definitions=sequence_feature_definitions()),
        rows,
    )


def test_sequence_feature_config_identity_and_acd_golden_vector() -> None:
    payload = sequence_feature_config_bytes()
    definitions = sequence_feature_definitions()
    candidate_id = "a" * 64
    rows = build_sequence_feature_rows([CandidateSequence(candidate_id, "ACD")])
    row = rows[0]

    assert ALPHABET == "ACDEFGHIKLMNPQRSTVWY"
    assert len(definitions) == 420
    assert [item["feature_id"] for item in definitions] == sorted(
        (cast(str, item["feature_id"]) for item in definitions),
        key=str.encode,
    )
    assert len(payload) == 281_064
    assert hashlib.sha256(payload).hexdigest() == FEATURE_CONFIG_SHA256
    assert row["aa-fraction-a"] == pytest.approx(1 / 3)
    assert row["aa-fraction-c"] == pytest.approx(1 / 3)
    assert row["aa-fraction-d"] == pytest.approx(1 / 3)
    assert row["dipeptide-fraction-ac"] == 0.5
    assert row["dipeptide-fraction-cd"] == 0.5
    assert row["aa-fraction-e"] == 0.0
    assert row["dipeptide-fraction-aa"] == 0.0


@pytest.mark.parametrize(
    ("candidate_id", "sequence", "message"),
    [
        ("not-a-sha", "ACD", "candidate ID"),
        ("a" * 64, "A", "at least 2"),
        ("a" * 64, "AcD", "uppercase"),
        ("a" * 64, "AXD", "alphabet"),
        ("a" * 64, " ACD", "alphabet"),
    ],
)
def test_sequence_feature_inputs_fail_closed(
    candidate_id: str,
    sequence: str,
    message: str,
) -> None:
    with pytest.raises(ContractValidationError, match=message):
        build_sequence_feature_rows([CandidateSequence(candidate_id, sequence)])


def test_sequence_feature_rejects_duplicate_and_processing_identity_mismatch() -> None:
    record = CandidateSequence("a" * 64, "ACD")
    with pytest.raises(ContractValidationError, match="duplicate"):
        build_sequence_feature_rows([record, record])

    config = _ProcessingConfig()
    with pytest.raises(ContractValidationError, match="processing"):
        build_sequence_feature_rows([record], processing_config=config)

    matching = CandidateSequence(_candidate_id(config.candidate_id_namespace, "ACD"), "ACD")
    assert len(build_sequence_feature_rows([matching], processing_config=config)) == 1


@pytest.mark.parametrize(
    ("model_id", "size_bytes"),
    [
        ("ridge-sklearn-v1", 1_243),
        ("random-forest-sklearn-v1", 2_575),
        ("xgboost-cpu-v1", 5_693),
    ],
)
def test_model_config_exact_identity(model_id: str, size_bytes: int) -> None:
    payload = model_config_bytes(model_id)
    loaded = load_model_config(payload)

    assert len(payload) == size_bytes
    assert hashlib.sha256(payload).hexdigest() == MODEL_CONFIG_SHA256[model_id]
    assert loaded.model_id == model_id
    assert loaded.canonical_bytes == payload
    with pytest.raises(ContractValidationError):
        load_model_config(payload[:-1] + b" ")


def test_model_config_effective_parameters_are_fully_explicit() -> None:
    ridge = model_effective_parameters("ridge-sklearn-v1")
    forest = model_effective_parameters("random-forest-sklearn-v1")
    xgboost = model_effective_parameters("xgboost-cpu-v1")

    assert ridge == {
        "alpha": 1.0,
        "copy_X": True,
        "fit_intercept": True,
        "max_iter": None,
        "positive": False,
        "random_state": None,
        "solver": "svd",
        "tol": 0.0001,
    }
    assert forest["n_estimators"] == 64
    assert forest["max_depth"] == 8
    assert forest["n_jobs"] == 1
    assert forest["random_state"] == 1729
    assert forest["monotonic_cst"] is None
    assert len(forest) == 18
    assert xgboost["booster"] == "gbtree"
    assert xgboost["device"] == "cpu"
    assert xgboost["objective"] == "reg:squarederror"
    assert xgboost["tree_method"] == "hist"
    assert xgboost["n_jobs"] == 1
    assert xgboost["random_state"] == 1729
    assert xgboost["seed_per_iteration"] is False
    assert xgboost["early_stopping_rounds"] is None
    assert xgboost["callbacks"] is None
    assert len(xgboost) == 41


def test_xgboost_model_config_identity_drift_fails_closed() -> None:
    payload = model_config_bytes("xgboost-cpu-v1")
    unknown = payload.replace(b"xgboost-cpu-v1", b"xgboost-gpu-v1")
    missing_value = json.loads(payload)
    missing_value["parameters"] = missing_value["parameters"][:-1]
    missing = canonical_json_bytes(missing_value)
    duplicate = payload.replace(
        b'  "model_id": "xgboost-cpu-v1",',
        b'  "model_id": "xgboost-cpu-v1",\n  "model_id": "xgboost-cpu-v1",',
        1,
    )

    for drifted in (unknown, missing, duplicate, payload[:-1] + b" "):
        with pytest.raises(ContractValidationError):
            load_model_config(drifted)


@pytest.mark.parametrize(
    "model_id",
    ["ridge-sklearn-v1", "random-forest-sklearn-v1", "xgboost-cpu-v1"],
)
def test_regressors_fit_predict_finite_deterministically(model_id: str) -> None:
    namespace = "public-synthetic-v1"
    development = [
        CandidateSequence(_candidate_id(namespace, sequence), sequence)
        for sequence in ("AA", "AC", "AD", "CA", "CC", "CD", "DA", "DC")
    ]
    candidates = [
        CandidateSequence(_candidate_id(namespace, sequence), sequence)
        for sequence in ("DD", "AE", "EA")
    ]
    loaded_development, _ = _loaded_matrix(development)
    loaded_candidates, _ = _loaded_matrix(candidates)
    observations = [
        DevelopmentObservation(record.candidate_id, float(index))
        for index, record in enumerate(development)
    ]

    first = predict_regressor(
        fit_regressor(
            model_config_bytes(model_id),
            loaded_development,
            observations,
            seed=1729,
        ),
        loaded_candidates,
    )
    second = predict_regressor(
        fit_regressor(
            model_config_bytes(model_id),
            loaded_development,
            observations,
            seed=1729,
        ),
        loaded_candidates,
    )

    assert first == second
    assert [candidate_id for candidate_id, _ in first] == sorted(
        record.candidate_id for record in candidates
    )
    assert all(math.isfinite(prediction) for _, prediction in first)


def test_xgboost_profile_produces_distinct_public_predictions() -> None:
    namespace = "public-synthetic-v1"
    development = [
        CandidateSequence(_candidate_id(namespace, sequence), sequence)
        for sequence in ("AA", "AC", "AD", "CA", "CC", "CD", "DA", "DC")
    ]
    candidates = [
        CandidateSequence(_candidate_id(namespace, sequence), sequence)
        for sequence in ("DD", "AE", "EA")
    ]
    loaded_development, _ = _loaded_matrix(development)
    loaded_candidates, _ = _loaded_matrix(candidates)
    observations = [
        DevelopmentObservation(record.candidate_id, float(index))
        for index, record in enumerate(development)
    ]

    predictions = {
        model_id: predict_regressor(
            fit_regressor(
                model_config_bytes(model_id),
                loaded_development,
                observations,
                seed=1729,
            ),
            loaded_candidates,
        )
        for model_id in ("random-forest-sklearn-v1", "xgboost-cpu-v1")
    }

    assert predictions["xgboost-cpu-v1"] != predictions["random-forest-sklearn-v1"]


def test_prediction_call_policy_is_explicit_only_for_xgboost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        CandidateSequence(hashlib.sha256(str(index).encode()).hexdigest(), sequence)
        for index, sequence in enumerate(("AA", "AC"))
    ]
    loaded, _ = _loaded_matrix(records)

    class XGBoostProbe:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        def predict(self, features: object, **kwargs: object) -> list[float]:
            self.kwargs = kwargs
            return [0.25, 0.75]

    class GenericProbe:
        def __init__(self) -> None:
            self.called = False

        def predict(self, features: object) -> list[float]:
            self.called = True
            return [0.5, 1.0]

    monkeypatch.setattr(m2_models, "XGBRegressor", XGBoostProbe)
    xgboost_probe = XGBoostProbe()
    generic_probe = GenericProbe()

    assert predict_regressor(cast(Any, xgboost_probe), loaded)
    assert xgboost_probe.kwargs == {
        "base_margin": None,
        "iteration_range": None,
        "output_margin": False,
        "validate_features": True,
    }
    assert predict_regressor(cast(Any, generic_probe), loaded)
    assert generic_probe.called


def test_trainer_observations_are_development_only_and_exact_cover() -> None:
    records = [
        CandidateSequence(hashlib.sha256(str(index).encode()).hexdigest(), sequence)
        for index, sequence in enumerate(("AA", "AC", "AD"))
    ]
    loaded, _ = _loaded_matrix(records)
    observations = [
        DevelopmentObservation(record.candidate_id, float(index))
        for index, record in enumerate(records)
    ]
    fit_parameters = set(inspect.signature(fit_regressor).parameters)
    predict_parameters = set(inspect.signature(predict_regressor).parameters)

    assert fit_parameters == {
        "model_config",
        "development_features",
        "development_observations",
        "seed",
    }
    assert predict_parameters == {"fitted_estimator", "candidate_features"}
    assert not any(
        forbidden in " ".join((*fit_parameters, *predict_parameters))
        for forbidden in ("truth", "held", "validation", "evaluator", "oracle")
    )

    with pytest.raises(ContractValidationError, match="exact-cover"):
        fit_regressor(
            model_config_bytes("ridge-sklearn-v1"),
            loaded,
            observations[:-1],
            seed=1729,
        )
    with pytest.raises(ContractValidationError, match="finite"):
        fit_regressor(
            model_config_bytes("ridge-sklearn-v1"),
            loaded,
            [*observations[:-1], DevelopmentObservation(records[-1].candidate_id, math.nan)],
            seed=1729,
        )
    with pytest.raises(ContractValidationError, match="XGBoost random_state"):
        fit_regressor(
            model_config_bytes("xgboost-cpu-v1"),
            loaded,
            observations,
            seed=1728,
        )


def _synthetic_sequence(index: int) -> str:
    return "".join(
        (
            ALPHABET[index % len(ALPHABET)],
            ALPHABET[(index // len(ALPHABET)) % len(ALPHABET)],
            ALPHABET[(index // (len(ALPHABET) ** 2)) % len(ALPHABET)],
        )
    )


def _alpha_role_candidates() -> dict[str, tuple[CandidateSequence, ...]]:
    role_counts = M2_FEATURE_TASK_BINDINGS["alpha-amylase"]["role_counts"]
    result: dict[str, tuple[CandidateSequence, ...]] = {}
    offset = 0
    for role in ("development", "validation", "candidate-pool"):
        count = cast(int, role_counts[role])
        result[role] = tuple(
            CandidateSequence(
                hashlib.sha256(f"{role}:{index}".encode()).hexdigest(),
                _synthetic_sequence(offset + index),
            )
            for index in range(count)
        )
        offset += count
    return result


def _static_experiment_config_bytes() -> bytes:
    config = json.loads((FIXTURES / "experiment-v2-alpha.json").read_text(encoding="utf-8"))
    config["acquisitions"] = ["m2-static-full-pool-v1"]
    config["batch_sizes"] = [1]
    config["budgets"] = [1]
    config["experiment_id"] = "issue-55-synthetic-alpha-v1"
    config["initial_sample_sizes"] = [1]
    config["models"] = [
        "random-forest-sklearn-v1",
        "ridge-sklearn-v1",
        "xgboost-cpu-v1",
    ]
    config["seeds"] = [1729]
    return canonical_json_bytes(config)


@pytest.fixture(scope="module")
def alpha_bundle() -> tuple[
    SequenceFeatureBundle,
    dict[str, tuple[CandidateSequence, ...]],
]:
    roles = _alpha_role_candidates()
    expected_memberships = {
        role: tuple(record.candidate_id for record in records) for role, records in roles.items()
    }
    return (
        build_sequence_feature_bundle(
            benchmark_task_id="alpha-amylase",
            role_candidates=roles,
            git_commit="a" * 40,
            environment_digest="b" * 64,
            expected_role_memberships=expected_memberships,
        ),
        roles,
    )


def _development_observations(
    roles: dict[str, tuple[CandidateSequence, ...]],
) -> tuple[DevelopmentObservation, ...]:
    return tuple(
        DevelopmentObservation(record.candidate_id, float(index % 29))
        for index, record in enumerate(roles["development"])
    )


def test_three_role_bundle_passes_issue_44_and_47(
    alpha_bundle: tuple[
        SequenceFeatureBundle,
        dict[str, tuple[CandidateSequence, ...]],
    ],
) -> None:
    bundle, roles = alpha_bundle
    validated = validate_feature_bundle_identity(
        bundle.logical_artifact,
        descriptor_bytes=bundle.descriptor_bytes,
        member_bytes=bundle.member_bytes,
        expected_role_memberships={
            role: [record.candidate_id for record in records] for role, records in roles.items()
        },
    )

    assert validated.sha256 == bundle.descriptor.sha256
    assert bundle.descriptor.value["fitted_states"] == ()
    assert len(bundle.descriptor.value["feature_definitions"]) == 420


def test_bundle_rejects_membership_not_independently_reconstructed_from_split() -> None:
    roles = _alpha_role_candidates()
    expected = {
        role: [record.candidate_id for record in records] for role, records in roles.items()
    }
    expected["validation"][0] = "f" * 64

    with pytest.raises(ContractValidationError, match="independent role membership"):
        build_sequence_feature_bundle(
            benchmark_task_id="alpha-amylase",
            role_candidates=roles,
            git_commit="a" * 40,
            environment_digest="b" * 64,
            expected_role_memberships=expected,
        )


@pytest.mark.parametrize(
    "model_id",
    ["ridge-sklearn-v1", "random-forest-sklearn-v1", "xgboost-cpu-v1"],
)
def test_prediction_producer_is_canonical_exact_cover_and_deterministic(
    model_id: str,
    alpha_bundle: tuple[
        SequenceFeatureBundle,
        dict[str, tuple[CandidateSequence, ...]],
    ],
) -> None:
    bundle, roles = alpha_bundle
    experiment_config = load_experiment_config(_static_experiment_config_bytes())
    run_spec = build_synthetic_run_spec(
        bundle=bundle,
        experiment_config=experiment_config,
        model_id=model_id,
        git_commit="a" * 40,
        environment_digest="b" * 64,
    )
    inputs = M2StaticProducerInputs(
        run_spec=run_spec,
        experiment_config=experiment_config,
        feature_descriptor=bundle.descriptor,
        feature_member_bytes=bundle.member_bytes,
        candidate_ids=tuple(sorted(bundle.memberships["candidate-pool"])),
        attempt_id=SYNTHETIC_ATTEMPT_ID,
        model_config_bytes=model_config_bytes(model_id),
    )
    producer = make_static_prediction_producer(_development_observations(roles))

    first = producer(inputs)
    second = producer(inputs)
    loaded = load_prediction_artifact(first)

    assert first == second
    assert loaded.candidate_ids == inputs.candidate_ids
    assert loaded.value["feature_sha256"] == bundle.descriptor.sha256
    assert loaded.value["model_config_sha256"] == run_spec.model_config_sha256
    assert all(math.isfinite(value) for value in loaded.predictions)


def _write_synthetic_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    roles = _alpha_role_candidates()
    role_path = tmp_path / "roles.json"
    observation_path = tmp_path / "development-observations.json"
    experiment_path = tmp_path / "experiment.json"
    role_path.write_text(
        json.dumps(
            {
                "benchmark_task_id": "alpha-amylase",
                "generator": {
                    "environment_digest": "b" * 64,
                    "git_commit": "a" * 40,
                },
                "roles": {
                    role: [
                        {
                            "candidate_id": record.candidate_id,
                            "sequence": record.sequence,
                        }
                        for record in records
                    ]
                    for role, records in roles.items()
                },
            }
        ),
        encoding="utf-8",
    )
    observation_path.write_text(
        json.dumps(
            {
                "development": [
                    {
                        "candidate_id": item.candidate_id,
                        "observation": item.observation,
                    }
                    for item in _development_observations(roles)
                ]
            }
        ),
        encoding="utf-8",
    )
    experiment_path.write_bytes(_static_experiment_config_bytes())
    return role_path, observation_path, experiment_path


def test_synthetic_cli_writes_only_local_scratch_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    role_path, observation_path, experiment_path = _write_synthetic_inputs(tmp_path)
    scratch = tmp_path / "scratch"

    assert (
        main(
            [
                "m2-synthetic",
                "--roles",
                str(role_path),
                "--development-observations",
                str(observation_path),
                "--experiment-config",
                str(experiment_path),
                "--model-id",
                "ridge-sklearn-v1",
                "--scratch-root",
                str(scratch),
            ]
        )
        == 0
    )

    summary = cast(dict[str, Any], json.loads(capsys.readouterr().out))
    assert summary["role_counts"] == {
        "candidate-pool": 2262,
        "development": 722,
        "validation": 361,
    }
    assert summary["model_id"] == "ridge-sklearn-v1"
    prediction = load_prediction_artifact((scratch / "m2-synthetic/prediction.json").read_bytes())
    assert len(prediction.candidate_ids) == 2262
    stored_files = [path.name for path in scratch.rglob("*") if path.is_file()]
    assert not any(
        forbidden in name
        for name in stored_files
        for forbidden in ("truth", "held", "oracle", "_SUCCESS")
    )


def test_frozen_preflight_entry_consumes_projection_and_emits_three_safe_predictions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _ProcessingConfig()
    source_roles = _alpha_role_candidates()
    roles = {
        role: tuple(
            CandidateSequence(
                _candidate_id(config.candidate_id_namespace, record.sequence),
                record.sequence,
            )
            for record in records
        )
        for role, records in source_roles.items()
    }
    projected = ProjectedInputs(
        role_candidates=roles,
        development_observations=_development_observations(roles),
        expected_role_memberships={
            role: tuple(record.candidate_id for record in records)
            for role, records in roles.items()
        },
    )
    calls: list[dict[str, object]] = []

    def fake_projection(**kwargs: object) -> ProjectedInputs:
        calls.append(kwargs)
        return projected

    monkeypatch.setattr("pcdb.m2.preflight.project_frozen_m2_inputs", fake_projection)
    scratch = tmp_path / "preflight"
    summary = run_frozen_preflight(
        benchmark_task_id="alpha-amylase",
        processing_config=cast(ProcessingConfigLike, config),
        processing_config_bytes=b"config",
        processing_manifest_bytes=b"processing-manifest",
        split_manifest_bytes=b"split-manifest",
        membership_bytes=b"membership",
        role_input_bytes=b"roles",
        hidden_truth_bytes=b"hidden-truth",
        experiment_config_bytes=_static_experiment_config_bytes(),
        git_commit="a" * 40,
        environment_digest="b" * 64,
        scratch_root=scratch,
    )

    assert len(calls) == 1
    assert calls[0]["hidden_truth_bytes"] == b"hidden-truth"
    assert [item["model_id"] for item in summary["predictions"]] == [
        "ridge-sklearn-v1",
        "random-forest-sklearn-v1",
        "xgboost-cpu-v1",
    ]
    assert len(list(scratch.glob("m2-preflight/alpha-amylase/*/prediction.json"))) == 3
    stored_names = [path.name for path in scratch.rglob("*") if path.is_file()]
    assert not any(
        forbidden in name
        for name in stored_names
        for forbidden in ("truth", "held", "oracle", "_SUCCESS", "run-attempt")
    )


def test_xgboost_two_processes_emit_identical_canonical_prediction_bytes(
    tmp_path: Path,
) -> None:
    role_path, observation_path, experiment_path = _write_synthetic_inputs(tmp_path)
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    prediction_paths: list[Path] = []

    for run_number in (1, 2):
        scratch = tmp_path / f"scratch-{run_number}"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pcdb.cli",
                "m2-synthetic",
                "--roles",
                str(role_path),
                "--development-observations",
                str(observation_path),
                "--experiment-config",
                str(experiment_path),
                "--model-id",
                "xgboost-cpu-v1",
                "--scratch-root",
                str(scratch),
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        prediction_paths.append(scratch / "m2-synthetic/prediction.json")

    first = prediction_paths[0].read_bytes()
    second = prediction_paths[1].read_bytes()
    assert first == second
    assert load_prediction_artifact(first).candidate_ids


def test_synthetic_cli_rejects_repo_relative_unignored_scratch_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    with pytest.raises(ContractValidationError, match="ignored \\.scratch namespace"):
        main(
            [
                "m2-synthetic",
                "--roles",
                "unused-roles.json",
                "--development-observations",
                "unused-observations.json",
                "--experiment-config",
                "unused-experiment.json",
                "--model-id",
                "ridge-sklearn-v1",
                "--scratch-root",
                "scratch/m2-synthetic",
            ]
        )


def test_synthetic_cli_accepts_xgboost_model_id(tmp_path: Path) -> None:
    with pytest.raises(ContractValidationError, match="role input"):
        main(
            [
                "m2-synthetic",
                "--roles",
                str(tmp_path / "missing-roles.json"),
                "--development-observations",
                str(tmp_path / "missing-observations.json"),
                "--experiment-config",
                str(tmp_path / "missing-experiment.json"),
                "--model-id",
                "xgboost-cpu-v1",
                "--scratch-root",
                str(tmp_path / "scratch"),
            ]
        )


@pytest.mark.parametrize("forbidden_field", ["target", "truth", "high_value"])
def test_synthetic_cli_rejects_truth_derived_role_fields(
    tmp_path: Path,
    forbidden_field: str,
) -> None:
    role_path = tmp_path / "roles.json"
    record = {
        "candidate_id": "a" * 64,
        "sequence": "ACD",
        forbidden_field: 1.0,
    }
    role_path.write_text(
        json.dumps(
            {
                "benchmark_task_id": "alpha-amylase",
                "generator": {
                    "environment_digest": "b" * 64,
                    "git_commit": "a" * 40,
                },
                "roles": {
                    "candidate-pool": [record],
                    "development": [record],
                    "validation": [record],
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="fields drifted"):
        main(
            [
                "m2-synthetic",
                "--roles",
                str(role_path),
                "--development-observations",
                str(tmp_path / "unused-observations.json"),
                "--experiment-config",
                str(tmp_path / "unused-experiment.json"),
                "--model-id",
                "ridge-sklearn-v1",
                "--scratch-root",
                str(tmp_path / "unused-scratch"),
            ]
        )
