from __future__ import annotations

import copy
import hashlib
import json
import struct
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from pcdb.contracts.experiment import (
    ContractValidationError,
    LoadedContract,
    canonical_json_bytes,
    load_experiment_config,
)
from pcdb.contracts.feature import load_feature_artifact
from pcdb.contracts.feature_identity import (
    build_feature_bundle_descriptor,
    encode_feature_config,
    encode_feature_fitted_state,
    encode_feature_matrix,
    load_feature_bundle_descriptor,
)
from pcdb.contracts.prediction import (
    M2_STATIC_ACQUISITION_CONFIG_SHA256,
    M2StaticEvaluatorInputs,
    M2StaticProducerInputs,
    load_run_attempt_v1,
    load_run_spec_v1,
    orchestrate_m2_static_prediction,
    validate_m2_static_prediction_evidence,
    validate_m2_static_profile,
)
from pcdb.contracts.run import RunSpec
from pcdb.evaluation.high_value import HighValueEvaluator
from pcdb.m2.gate_b import load_gate_b_packet
from pcdb.m2.gate_b_runtime import GATE_B_APPROVAL, FrozenGateBRuntime

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/contracts"
MODEL_CONFIG_BYTES = b"public synthetic model config v1\n"


def _task_bound_bundle() -> tuple[
    LoadedContract,
    bytes,
    dict[str, bytes],
    dict[str, list[str]],
]:
    logical = json.loads(
        (FIXTURES / "m2-feature-artifact-v1-valid.json").read_text(encoding="utf-8")
    )
    config = {
        "schema_version": "m2-feature-config-v1",
        "feature_config_id": logical["feature_config"]["feature_config_id"],
        "derivations": [
            {
                "feature_id": definition["feature_id"],
                "derivation_id": definition["derivation"]["derivation_id"],
                "derivation_version": definition["derivation"]["derivation_version"],
                "parameters": [],
            }
            for definition in logical["feature_definitions"]
        ],
    }
    config_bytes = encode_feature_config(
        config,
        feature_definitions=logical["feature_definitions"],
    )
    config_key = "features/alpha-amylase/config.json"
    logical["feature_config"]["feature_config_sha256"] = hashlib.sha256(config_bytes).hexdigest()

    tensor_definitions = {
        "sequence-length-standardizer": [{"name": "mean", "dtype": "float64", "shape": []}]
    }
    state_metadata = logical["fitted_states"][0]
    state = {
        "schema_version": "m2-feature-fitted-state-json-v1",
        "fit_state_id": state_metadata["fit_state_id"],
        "state_version": state_metadata["state_version"],
        "derivation_id": state_metadata["derivation_id"],
        "fit_role": state_metadata["fit_role"],
        "input_identity": state_metadata["input_identity"],
        "tensors": [
            {
                "name": "mean",
                "dtype": "float64",
                "shape": [],
                "values": ["0000000000000000"],
            }
        ],
    }
    state_bytes = encode_feature_fitted_state(
        state,
        tensor_definitions=tensor_definitions[state_metadata["fit_state_id"]],
    )
    state_sha256 = hashlib.sha256(state_bytes).hexdigest()
    state_metadata["artifact"]["sha256"] = state_sha256
    state_metadata["artifact"]["size_bytes"] = len(state_bytes)
    logical["feature_definitions"][1]["derivation"]["fit_state"]["sha256"] = state_sha256
    for matrix in logical["matrices"]:
        matrix["fit_state_refs"][0]["sha256"] = state_sha256

    member_bytes = {
        config_key: config_bytes,
        state_metadata["artifact"]["object_key"]: state_bytes,
    }
    memberships: dict[str, list[str]] = {}
    for role_index, matrix in enumerate(logical["matrices"]):
        candidate_ids = [
            hashlib.sha256(f"{role_index}:{index}".encode()).hexdigest()
            for index in range(matrix["row_count"])
        ]
        rows = [
            {
                "candidate_id": candidate_id,
                "sequence_length": index,
                "sequence_length_zscore": float(index),
            }
            for index, candidate_id in enumerate(candidate_ids)
        ]
        matrix_bytes = encode_feature_matrix(
            rows,
            feature_definitions=logical["feature_definitions"],
        )
        matrix["artifact"]["object_key"] = f"features/alpha-amylase/{matrix['role']}.csv"
        matrix["artifact"]["sha256"] = hashlib.sha256(matrix_bytes).hexdigest()
        matrix["artifact"]["size_bytes"] = len(matrix_bytes)
        member_bytes[matrix["artifact"]["object_key"]] = matrix_bytes
        memberships[matrix["role"]] = candidate_ids

    logical_artifact = load_feature_artifact(canonical_json_bytes(logical))
    descriptor_bytes = build_feature_bundle_descriptor(
        logical_artifact,
        feature_config_object_key=config_key,
        member_bytes=member_bytes,
        tensor_definitions=tensor_definitions,
        expected_role_memberships=memberships,
    )
    return logical_artifact, descriptor_bytes, member_bytes, memberships


def _static_experiment_config_bytes() -> bytes:
    config = json.loads((FIXTURES / "experiment-v2-alpha.json").read_text(encoding="utf-8"))
    config["acquisitions"] = ["m2-static-full-pool-v1"]
    config["initial_sample_sizes"] = [1]
    config["batch_sizes"] = [1]
    config["budgets"] = [1]
    config["seeds"] = [0]
    return canonical_json_bytes(config)


def _metric_result(
    *,
    run_spec: RunSpec,
    experiment_config_sha256: str,
    prediction_sha256: str,
    attempt_id: str,
    metric_id: str,
    definition_id: str | None,
    nominal_k: int | None,
) -> bytes:
    support: dict[str, Any] = {"evaluated_candidate_count": 2262}
    if definition_id is not None:
        assert nominal_k is not None
        support.update(
            {
                "actual_high_value_count": nominal_k,
                "boundary_score": 0.5,
                "boundary_tie_count": 1,
                "frozen_candidate_count": 2262,
                "nominal_k": nominal_k,
            }
        )
    return canonical_json_bytes(
        {
            "schema_version": "high-value-metric-result-v1",
            "experiment_config_sha256": experiment_config_sha256,
            "dataset_manifest_sha256": run_spec.dataset_manifest_sha256,
            "processing_manifest_sha256": run_spec.processing_manifest_sha256,
            "split_sha256": run_spec.split_sha256,
            "run_id": run_spec.run_id,
            "attempt_id": attempt_id,
            "input_artifact_sha256": prediction_sha256,
            "task_id": "alpha-amylase",
            "model_id": run_spec.model_id,
            "model_config_sha256": run_spec.model_config_sha256,
            "acquisition_id": run_spec.acquisition_id,
            "acquisition_config_sha256": run_spec.acquisition_config_sha256,
            "seed": run_spec.seed,
            "candidate_role": "candidate-pool",
            "definition_id": definition_id,
            "metric_id": metric_id,
            "evaluation_scope": "m2-static",
            "round": None,
            "budget": None,
            "status": "defined",
            "value": 0.5,
            "undefined_reason": None,
            "support": support,
        }
    )


def _run_attempt_bytes(
    *,
    run_spec: RunSpec,
    prediction_object_key: str,
    prediction_bytes: bytes,
    metric_result_bytes: dict[str, bytes],
) -> bytes:
    outputs = [
        {
            "object_key": prediction_object_key,
            "sha256": hashlib.sha256(prediction_bytes).hexdigest(),
            "size_bytes": len(prediction_bytes),
        }
    ]
    outputs.extend(
        {
            "object_key": object_key,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
        for object_key, payload in metric_result_bytes.items()
    )
    return canonical_json_bytes(
        {
            "schema_version": "1",
            "run_id": run_spec.run_id,
            "attempt_id": "attempt-001",
            "status": "succeeded",
            "started_at": "2026-08-04T00:00:00Z",
            "finished_at": "2026-08-04T00:01:00Z",
            "executed_git_commit": run_spec.git_commit,
            "executed_environment_digest": run_spec.environment_digest,
            "compute": {
                "executor_type": "local",
                "runtime_version": "python-3.14",
            },
            "outputs": outputs,
            "completion_marker": {
                "object_key": f"runs/{run_spec.run_id}/attempt-001/_SUCCESS",
                "sha256": ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
                "size_bytes": 0,
            },
        }
    )


@pytest.fixture(scope="module")
def evidence_inputs() -> dict[str, Any]:
    logical, descriptor_bytes, member_bytes, memberships = _task_bound_bundle()
    descriptor = load_feature_bundle_descriptor(descriptor_bytes)
    experiment_config_bytes = _static_experiment_config_bytes()
    experiment_config_sha256 = hashlib.sha256(experiment_config_bytes).hexdigest()
    run_spec = RunSpec(
        schema_version="1",
        protocol_version="m2-alpha-ired-static-v1",
        git_commit=logical.value["generator"]["git_commit"],
        environment_digest=logical.value["generator"]["environment_digest"],
        dataset_manifest_sha256=logical.value["dataset_manifest_sha256"],
        processing_manifest_sha256=logical.value["processing_manifest_sha256"],
        split_sha256=logical.value["split_sha256"],
        feature_sha256=descriptor.sha256,
        experiment_config_sha256=experiment_config_sha256,
        model_id="synthetic-model",
        model_config_sha256=hashlib.sha256(MODEL_CONFIG_BYTES).hexdigest(),
        acquisition_id="m2-static-full-pool-v1",
        acquisition_config_sha256=M2_STATIC_ACQUISITION_CONFIG_SHA256,
        seed=0,
        initial_sample_size=1,
        batch_size=1,
        budget=1,
    )
    prediction_object_key = f"runs/{run_spec.run_id}/attempt-001/predictions.json"
    prediction_bytes = canonical_json_bytes(
        {
            "schema_version": "m2-prediction-artifact-v1",
            "protocol_version": run_spec.protocol_version,
            "experiment_config_sha256": experiment_config_sha256,
            "dataset_manifest_sha256": run_spec.dataset_manifest_sha256,
            "processing_manifest_sha256": run_spec.processing_manifest_sha256,
            "split_sha256": run_spec.split_sha256,
            "feature_sha256": run_spec.feature_sha256,
            "run_id": run_spec.run_id,
            "attempt_id": "attempt-001",
            "task_id": "alpha-amylase",
            "candidate_role": "candidate-pool",
            "candidate_count": 2262,
            "model_id": run_spec.model_id,
            "model_config_sha256": run_spec.model_config_sha256,
            "acquisition_id": run_spec.acquisition_id,
            "acquisition_config_sha256": run_spec.acquisition_config_sha256,
            "seed": run_spec.seed,
            "predictions": [
                {
                    "candidate_id": candidate_id,
                    "prediction": struct.pack(">d", float(index)).hex(),
                }
                for index, candidate_id in enumerate(sorted(memberships["candidate-pool"]))
            ],
        }
    )
    prediction_sha256 = hashlib.sha256(prediction_bytes).hexdigest()
    identities = [
        ("spearman_rank_correlation", None, None),
        ("high_value_recall_at_nominal_k", "upper-tail-1pct", 23),
        ("high_value_recall_at_nominal_k", "upper-tail-0.5pct", 12),
        ("high_value_recall_at_nominal_k", "upper-tail-2pct", 46),
    ]
    metric_result_bytes = {
        f"runs/{run_spec.run_id}/attempt-001/metric-{index}.json": _metric_result(
            run_spec=run_spec,
            experiment_config_sha256=experiment_config_sha256,
            prediction_sha256=prediction_sha256,
            attempt_id="attempt-001",
            metric_id=metric_id,
            definition_id=definition_id,
            nominal_k=nominal_k,
        )
        for index, (metric_id, definition_id, nominal_k) in enumerate(identities)
    }
    return {
        "run_spec_bytes": run_spec.canonical_json().encode("utf-8"),
        "experiment_config_bytes": experiment_config_bytes,
        "run_attempt_bytes": _run_attempt_bytes(
            run_spec=run_spec,
            prediction_object_key=prediction_object_key,
            prediction_bytes=prediction_bytes,
            metric_result_bytes=metric_result_bytes,
        ),
        "expected_executed_git_commit": run_spec.git_commit,
        "expected_executed_environment_digest": run_spec.environment_digest,
        "prediction_object_key": prediction_object_key,
        "prediction_bytes": prediction_bytes,
        "metric_result_bytes": metric_result_bytes,
        "feature_artifact": logical,
        "feature_descriptor_bytes": descriptor_bytes,
        "feature_member_bytes": member_bytes,
        "expected_role_memberships": memberships,
    }


def test_complete_static_prediction_evidence_closure_passes(
    evidence_inputs: dict[str, Any],
) -> None:
    evidence = validate_m2_static_prediction_evidence(**evidence_inputs)

    assert evidence.prediction.value["candidate_count"] == 2262
    assert len(evidence.metric_results) == 4
    assert evidence.run_attempt.value["status"] == "succeeded"


def test_complete_closure_accepts_reviewed_actual_runtime_provenance(
    evidence_inputs: dict[str, Any],
) -> None:
    changed = dict(evidence_inputs)
    run_spec = load_run_spec_v1(changed["run_spec_bytes"])
    runtime_commit = "b" * 40
    runtime_environment = "d" * 64
    attempt = json.loads(changed["run_attempt_bytes"])
    attempt["executed_git_commit"] = runtime_commit
    attempt["executed_environment_digest"] = runtime_environment
    changed["run_attempt_bytes"] = canonical_json_bytes(attempt)
    changed["expected_executed_git_commit"] = runtime_commit
    changed["expected_executed_environment_digest"] = runtime_environment

    evidence = validate_m2_static_prediction_evidence(**changed)

    assert run_spec.git_commit != runtime_commit
    assert evidence.run_attempt.value["executed_git_commit"] == runtime_commit


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_executed_git_commit", "c" * 40),
        ("expected_executed_environment_digest", "e" * 64),
    ],
)
def test_complete_closure_rejects_unreviewed_execution_provenance(
    evidence_inputs: dict[str, Any],
    field: str,
    value: str,
) -> None:
    changed = dict(evidence_inputs)
    changed[field] = value

    with pytest.raises(ContractValidationError, match="RunAttempt executed_.* exact-match"):
        validate_m2_static_prediction_evidence(**changed)


def test_gate_b_generated_run_attempt_passes_formal_prediction_and_metric_closure(
    evidence_inputs: dict[str, Any],
    tmp_path: Path,
) -> None:
    packet_path = ROOT / "manifests/experiments/m2/issue-58/gate-b-packet.v1.json"
    packet = load_gate_b_packet(packet_path.read_bytes()).value
    step = dict(
        next(
            item
            for item in packet["operator_plan"]["steps"]
            if item["operation"] == "generate-canonical-run-attempt-exact-cover-five-payloads"
        )
    )
    run_spec = load_run_spec_v1(evidence_inputs["run_spec_bytes"])
    runtime_commit = "b" * 40
    runtime_environment = "d" * 64
    step["run_id"] = run_spec.run_id
    step["attempt_id"] = "attempt-001"

    class NoSideEffectObjects:
        def list_keys(self, bucket: str, prefix: str) -> tuple[str, ...]:
            return ()

        def head(self, bucket: str, key: str) -> object:
            raise AssertionError("formal COS must not be reached")

        def get(self, bucket: str, key: str) -> bytes:
            raise AssertionError("formal COS must not be reached")

        def put_forbid_overwrite(self, bucket: str, key: str, payload: bytes) -> None:
            raise AssertionError("formal COS must not be reached")

    class NoSideEffectEvaluator:
        def evaluate(self, request: object) -> dict[str, bytes]:
            raise AssertionError("formal evaluator must not be reached")

    scratch_root = tmp_path / "scratch"
    runtime = FrozenGateBRuntime(
        repository_root=ROOT,
        gate_a_root=tmp_path / "gate-a",
        scratch_root=scratch_root,
        objects=cast(Any, NoSideEffectObjects()),
        evaluator=NoSideEffectEvaluator(),
        approval=GATE_B_APPROVAL,
        runtime_git_commit=runtime_commit,
        runtime_environment_digest=runtime_environment,
        attempt_started_at=datetime(2026, 8, 7, 10, 0, tzinfo=UTC),
        now=lambda: datetime(2026, 8, 7, 10, 5, tzinfo=UTC),
    )
    runtime.bind_reviewed_packet(packet)
    references = [str(key) for key in step["referenced_object_keys"]]
    metric_payloads = list(evidence_inputs["metric_result_bytes"].values())
    for key, payload in zip(
        references,
        [evidence_inputs["prediction_bytes"], *metric_payloads],
        strict=True,
    ):
        runtime._stage(key, payload)

    runtime.generate_run_attempt(step)

    changed = dict(evidence_inputs)
    changed["run_attempt_bytes"] = (scratch_root / str(step["output_object_key"])).read_bytes()
    changed["prediction_object_key"] = references[0]
    changed["metric_result_bytes"] = dict(zip(references[1:], metric_payloads, strict=True))
    changed["expected_executed_git_commit"] = runtime_commit
    changed["expected_executed_environment_digest"] = runtime_environment
    validated = validate_m2_static_prediction_evidence(**changed)

    assert validated.run_attempt.value["executed_git_commit"] == runtime_commit
    assert len(validated.metric_results) == 4


def test_run_spec_and_attempt_strict_loaders_accept_evidence(
    evidence_inputs: dict[str, Any],
) -> None:
    run_spec = load_run_spec_v1(evidence_inputs["run_spec_bytes"])

    assert load_run_spec_v1(run_spec.canonical_json().encode("utf-8")) == run_spec
    assert (
        load_run_attempt_v1(evidence_inputs["run_attempt_bytes"]).value["run_id"] == run_spec.run_id
    )
    with pytest.raises(ContractValidationError, match="canonical"):
        load_run_spec_v1(evidence_inputs["run_spec_bytes"] + b"\n")


def test_capability_limited_orchestration_uses_fresh_prediction_bytes(
    evidence_inputs: dict[str, Any],
) -> None:
    observations: dict[str, Any] = {}

    def producer(inputs: M2StaticProducerInputs) -> bytes:
        observations["producer_fields"] = set(inputs.__dataclass_fields__)
        assert inputs.model_config_bytes == MODEL_CONFIG_BYTES
        return cast(bytes, evidence_inputs["prediction_bytes"])

    def evaluator(inputs: M2StaticEvaluatorInputs) -> dict[str, bytes]:
        observations["evaluator_fields"] = set(inputs.__dataclass_fields__)
        observations["fresh_bytes"] = (
            inputs.prediction_bytes is not evidence_inputs["prediction_bytes"]
        )
        synthetic_truth = [
            (candidate_id, float(index)) for index, candidate_id in enumerate(inputs.candidate_ids)
        ]
        truth_owner = HighValueEvaluator(synthetic_truth)
        prediction_rows = [
            (candidate_id, float(index)) for index, candidate_id in enumerate(inputs.candidate_ids)
        ]
        fractions = {
            "upper-tail-1pct": 0.01,
            "upper-tail-0.5pct": 0.005,
            "upper-tail-2pct": 0.02,
        }
        results: dict[str, bytes] = {}
        for object_key, payload in evidence_inputs["metric_result_bytes"].items():
            result = json.loads(payload)
            definition_id = result["definition_id"]
            if definition_id is None:
                metric = truth_owner.spearman_rank_correlation(prediction_rows)
            else:
                fraction = fractions[definition_id]
                metric = truth_owner.high_value_recall_at_nominal_k(
                    prediction_rows,
                    fraction=fraction,
                )
                summary = truth_owner.definition_summary(fraction=fraction)
                result["support"].update(
                    {
                        "actual_high_value_count": summary.actual_high_value_count,
                        "boundary_score": summary.boundary_score,
                        "boundary_tie_count": summary.boundary_tie_count,
                        "nominal_k": summary.nominal_k,
                    }
                )
            result["status"] = metric.status
            result["value"] = metric.value
            result["undefined_reason"] = metric.undefined_reason
            results[object_key] = canonical_json_bytes(result)
        return results

    outputs = orchestrate_m2_static_prediction(
        run_spec_bytes=evidence_inputs["run_spec_bytes"],
        experiment_config_bytes=evidence_inputs["experiment_config_bytes"],
        attempt_id="attempt-001",
        model_config_bytes=MODEL_CONFIG_BYTES,
        feature_artifact=evidence_inputs["feature_artifact"],
        feature_descriptor_bytes=evidence_inputs["feature_descriptor_bytes"],
        feature_member_bytes=evidence_inputs["feature_member_bytes"],
        expected_role_memberships=evidence_inputs["expected_role_memberships"],
        producer=producer,
        evaluator=evaluator,
    )

    assert "truth" not in observations["producer_fields"]
    assert "evaluator" not in observations["producer_fields"]
    assert "feature_member_bytes" not in observations["evaluator_fields"]
    assert observations["fresh_bytes"] is True
    assert (
        outputs.prediction.sha256 == hashlib.sha256(evidence_inputs["prediction_bytes"]).hexdigest()
    )
    assert len(outputs.metric_result_bytes) == 4
    assert {json.loads(payload)["value"] for payload in outputs.metric_result_bytes.values()} == {
        1.0
    }


@pytest.mark.parametrize(
    ("failing_callback", "expected_message"),
    [
        ("producer", "target-free prediction producer failed"),
        ("evaluator", "M2 aggregate evaluator failed"),
    ],
)
def test_orchestration_drops_truth_bearing_callback_error_chain(
    evidence_inputs: dict[str, Any],
    failing_callback: str,
    expected_message: str,
) -> None:
    private_payload = "candidate truth and membership must not escape"

    def producer(_: Any) -> bytes:
        if failing_callback == "producer":
            raise RuntimeError(private_payload)
        return cast(bytes, evidence_inputs["prediction_bytes"])

    def evaluator(_: Any) -> dict[str, bytes]:
        if failing_callback == "evaluator":
            raise RuntimeError(private_payload)
        return dict(evidence_inputs["metric_result_bytes"])

    with pytest.raises(
        ContractValidationError,
        match=f"^{expected_message}$",
    ) as error:
        orchestrate_m2_static_prediction(
            run_spec_bytes=evidence_inputs["run_spec_bytes"],
            experiment_config_bytes=evidence_inputs["experiment_config_bytes"],
            attempt_id="attempt-001",
            model_config_bytes=MODEL_CONFIG_BYTES,
            feature_artifact=evidence_inputs["feature_artifact"],
            feature_descriptor_bytes=evidence_inputs["feature_descriptor_bytes"],
            feature_member_bytes=evidence_inputs["feature_member_bytes"],
            expected_role_memberships=evidence_inputs["expected_role_memberships"],
            producer=producer,
            evaluator=evaluator,
        )
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert private_payload not in repr(error.value)


def test_orchestration_rejects_truth_field_in_aggregate_result(
    evidence_inputs: dict[str, Any],
) -> None:
    metric_results = dict(evidence_inputs["metric_result_bytes"])
    object_key = next(iter(metric_results))
    leaked_result = json.loads(metric_results[object_key])
    leaked_result["truth"] = [{"candidate_id": "0" * 64, "label": 1.0}]
    metric_results[object_key] = canonical_json_bytes(leaked_result)

    with pytest.raises(ContractValidationError, match="fields differ"):
        orchestrate_m2_static_prediction(
            run_spec_bytes=evidence_inputs["run_spec_bytes"],
            experiment_config_bytes=evidence_inputs["experiment_config_bytes"],
            attempt_id="attempt-001",
            model_config_bytes=MODEL_CONFIG_BYTES,
            feature_artifact=evidence_inputs["feature_artifact"],
            feature_descriptor_bytes=evidence_inputs["feature_descriptor_bytes"],
            feature_member_bytes=evidence_inputs["feature_member_bytes"],
            expected_role_memberships=evidence_inputs["expected_role_memberships"],
            producer=lambda _: evidence_inputs["prediction_bytes"],
            evaluator=lambda _: metric_results,
        )


def test_static_profile_rejects_run_and_config_sentinel_drift(
    evidence_inputs: dict[str, Any],
) -> None:
    run_spec = load_run_spec_v1(evidence_inputs["run_spec_bytes"])
    experiment_config = load_experiment_config(evidence_inputs["experiment_config_bytes"])
    with pytest.raises(ContractValidationError, match="budget"):
        validate_m2_static_profile(replace(run_spec, budget=2), experiment_config)

    config = json.loads(evidence_inputs["experiment_config_bytes"])
    config["batch_sizes"] = [2]
    changed_config = load_experiment_config(canonical_json_bytes(config))
    with pytest.raises(ContractValidationError, match="batch_sizes"):
        validate_m2_static_profile(
            replace(run_spec, experiment_config_sha256=changed_config.sha256),
            changed_config,
        )


def test_run_attempt_loader_rejects_unknown_nested_compute_field(
    evidence_inputs: dict[str, Any],
) -> None:
    attempt = json.loads(evidence_inputs["run_attempt_bytes"])
    attempt["compute"]["host"] = "forbidden"

    with pytest.raises(ContractValidationError, match="compute fields"):
        load_run_attempt_v1(canonical_json_bytes(attempt))


def test_evidence_rejects_missing_metric_identity(
    evidence_inputs: dict[str, Any],
) -> None:
    changed = dict(evidence_inputs)
    changed["metric_result_bytes"] = dict(list(evidence_inputs["metric_result_bytes"].items())[:-1])

    with pytest.raises(ContractValidationError, match="exactly four"):
        validate_m2_static_prediction_evidence(**changed)


def test_evidence_rejects_extra_metric_identity(
    evidence_inputs: dict[str, Any],
) -> None:
    changed = dict(evidence_inputs)
    metric_results = dict(evidence_inputs["metric_result_bytes"])
    metric_results["metrics/extra.json"] = next(iter(metric_results.values()))
    changed["metric_result_bytes"] = metric_results

    with pytest.raises(ContractValidationError, match="exactly four"):
        validate_m2_static_prediction_evidence(**changed)


def test_evidence_rejects_metric_candidate_count_drift(
    evidence_inputs: dict[str, Any],
) -> None:
    changed = dict(evidence_inputs)
    metric_results = dict(evidence_inputs["metric_result_bytes"])
    object_key = next(iter(metric_results))
    result = json.loads(metric_results[object_key])
    result["support"]["evaluated_candidate_count"] = 2261
    metric_results[object_key] = canonical_json_bytes(result)
    changed["metric_result_bytes"] = metric_results
    changed["run_attempt_bytes"] = _run_attempt_bytes(
        run_spec=load_run_spec_v1(changed["run_spec_bytes"]),
        prediction_object_key=changed["prediction_object_key"],
        prediction_bytes=changed["prediction_bytes"],
        metric_result_bytes=metric_results,
    )

    with pytest.raises(ContractValidationError, match="evaluated_candidate_count"):
        validate_m2_static_prediction_evidence(**changed)


def test_evidence_rejects_duplicate_metric_identity(
    evidence_inputs: dict[str, Any],
) -> None:
    changed = dict(evidence_inputs)
    metric_results = dict(evidence_inputs["metric_result_bytes"])
    object_keys = list(metric_results)
    duplicate = json.loads(metric_results[object_keys[0]])
    duplicate["value"] = 0.25
    metric_results[object_keys[-1]] = canonical_json_bytes(duplicate)
    changed["metric_result_bytes"] = metric_results
    changed["run_attempt_bytes"] = _run_attempt_bytes(
        run_spec=load_run_spec_v1(changed["run_spec_bytes"]),
        prediction_object_key=changed["prediction_object_key"],
        prediction_bytes=changed["prediction_bytes"],
        metric_result_bytes=metric_results,
    )

    with pytest.raises(ContractValidationError, match="identities must be unique"):
        validate_m2_static_prediction_evidence(**changed)


def test_evidence_rejects_prediction_membership_drift(
    evidence_inputs: dict[str, Any],
) -> None:
    changed = dict(evidence_inputs)
    prediction = json.loads(evidence_inputs["prediction_bytes"])
    replacement = "f" * 64
    existing = {row["candidate_id"] for row in prediction["predictions"]}
    assert replacement not in existing
    prediction["predictions"][0]["candidate_id"] = replacement
    prediction["predictions"].sort(key=lambda row: row["candidate_id"])
    prediction_bytes = canonical_json_bytes(prediction)
    changed["prediction_bytes"] = prediction_bytes
    changed["run_attempt_bytes"] = _run_attempt_bytes(
        run_spec=load_run_spec_v1(changed["run_spec_bytes"]),
        prediction_object_key=changed["prediction_object_key"],
        prediction_bytes=prediction_bytes,
        metric_result_bytes=changed["metric_result_bytes"],
    )

    with pytest.raises(ContractValidationError, match="exact cover"):
        validate_m2_static_prediction_evidence(**changed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("experiment_config_sha256", "0" * 64),
        ("feature_sha256", "0" * 64),
        ("run_id", "0" * 64),
        ("attempt_id", "attempt-002"),
    ],
)
def test_evidence_rejects_prediction_identity_drift(
    evidence_inputs: dict[str, Any],
    field: str,
    value: str,
) -> None:
    changed = dict(evidence_inputs)
    prediction = json.loads(evidence_inputs["prediction_bytes"])
    prediction[field] = value
    prediction_bytes = canonical_json_bytes(prediction)
    changed["prediction_bytes"] = prediction_bytes
    changed["run_attempt_bytes"] = _run_attempt_bytes(
        run_spec=load_run_spec_v1(changed["run_spec_bytes"]),
        prediction_object_key=changed["prediction_object_key"],
        prediction_bytes=prediction_bytes,
        metric_result_bytes=changed["metric_result_bytes"],
    )

    with pytest.raises(ContractValidationError, match=f"prediction {field}"):
        validate_m2_static_prediction_evidence(**changed)


def test_run_attempt_loader_rejects_duplicate_output_object_keys(
    evidence_inputs: dict[str, Any],
) -> None:
    attempt = copy.deepcopy(json.loads(evidence_inputs["run_attempt_bytes"]))
    attempt["outputs"].append(copy.deepcopy(attempt["outputs"][0]))

    with pytest.raises(ContractValidationError, match="object keys"):
        load_run_attempt_v1(canonical_json_bytes(attempt))


def test_evidence_rejects_missing_prediction_output_and_success_marker(
    evidence_inputs: dict[str, Any],
) -> None:
    attempt = json.loads(evidence_inputs["run_attempt_bytes"])
    attempt["outputs"] = [
        output
        for output in attempt["outputs"]
        if output["object_key"] != evidence_inputs["prediction_object_key"]
    ]
    changed = dict(evidence_inputs)
    changed["run_attempt_bytes"] = canonical_json_bytes(attempt)
    with pytest.raises(ContractValidationError, match="prediction reference"):
        validate_m2_static_prediction_evidence(**changed)

    del attempt["completion_marker"]
    changed["run_attempt_bytes"] = canonical_json_bytes(attempt)
    with pytest.raises(ContractValidationError, match="terminal state"):
        validate_m2_static_prediction_evidence(**changed)
