"""Gate B review-packet contracts for Issue #58."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from pcdb.contracts.experiment import ContractValidationError, canonical_json_bytes
from pcdb.m2.gate_b import (
    GateBOperatorRuntime,
    build_gate_b_packet,
    build_m3_freeze_record,
    execute_gate_b_operator,
    load_gate_b_operator_plan,
    load_gate_b_packet,
    load_m3_freeze_record,
)
from pcdb.m2.model_identity import MODEL_CONFIG_SHA256

SOURCE_COMMIT = "9e6fae5fe4b068ccf25d13ceb9913d6f7f2fffd5"
ROOT = Path(__file__).resolve().parents[2]
KNOWN_INPUTS_PATH = ROOT / "manifests/experiments/m2/issue-58/gate-a-known-inputs.v1.json"
EVIDENCE_BUCKET = "pcdb-benchmark-artifacts-1408226911"
EVIDENCE_PREFIX = "m2/issue-58/alpha-ired-static-v1/9e6fae5fe4b068ccf25d13ceb9913d6f7f2fffd5/"
FEATURE_ROOTS = {
    "alpha-amylase": "fa64315824eccde93e5c4740bbaf638aa54da2d2b8b96053bbae193ec89bbe42",
    "ired": "3d20f04020f1b136d1097d6beee7fcaa7b09729e80babd6d96154f71c510fb1b",
}
RUN_IDS = {
    ("alpha-amylase", "ridge-sklearn-v1"): (
        "835788c12467d4625b2ea482baa3dc69e77fc1c4f9452746aa4ae4d5bad4904c"
    ),
    ("alpha-amylase", "random-forest-sklearn-v1"): (
        "0146cffe9490a9f7c4e9f7909c9cd5225acc06c56d00cd88e9786062ca6e5be2"
    ),
    ("alpha-amylase", "xgboost-cpu-v1"): (
        "60713aeabbcdd2fe6b4077ce8d53ec3d0c37f93b4d45ffeb7d796c7cfa959788"
    ),
    ("ired", "ridge-sklearn-v1"): (
        "0c1426decf706cc086d64e9d2ac94a96915c8a6e515aca7f8d113e750a8a5c09"
    ),
    ("ired", "random-forest-sklearn-v1"): (
        "6250d7e2279ade16252b548d0df5278e0a5d60ccfa41b31789129847ee8d19a6"
    ),
    ("ired", "xgboost-cpu-v1"): (
        "18352e9b9065eaf28ebd42ba92d14f0f0f3b09838b8c674dc7d9bff082da9675"
    ),
}


def _freeze_bytes() -> bytes:
    return build_m3_freeze_record(
        source_git_commit=SOURCE_COMMIT,
        task_feature_roots=FEATURE_ROOTS,
        run_ids=RUN_IDS,
    )


def _packet_bytes() -> bytes:
    return build_gate_b_packet(
        repository_root=ROOT,
        gate_a_known_inputs_bytes=KNOWN_INPUTS_PATH.read_bytes(),
        evidence_bucket=EVIDENCE_BUCKET,
        evidence_prefix=EVIDENCE_PREFIX,
    )


def _mutated_packet(mutator: Callable[[dict[str, Any]], None]) -> bytes:
    value = json.loads(_packet_bytes())
    mutator(value)
    return canonical_json_bytes(value)


def test_m3_freeze_is_canonical_and_binds_the_exact_six_run_cohort() -> None:
    payload = _freeze_bytes()
    value = json.loads(payload)
    loaded = load_m3_freeze_record(payload)

    assert canonical_json_bytes(value) == payload
    assert loaded.canonical_bytes == payload
    assert value["source_git_commit"] == SOURCE_COMMIT
    assert value["frozen_before_candidate_truth_read"] is True
    assert value["cohort"]["seed"] == 1729
    assert value["cohort"]["feature_config_id"] == "sequence-composition-420-v1"
    assert value["cohort"]["model_configs"] == [
        {"model_config_sha256": MODEL_CONFIG_SHA256[model_id], "model_id": model_id}
        for model_id in (
            "random-forest-sklearn-v1",
            "ridge-sklearn-v1",
            "xgboost-cpu-v1",
        )
    ]
    assert {
        (task["task_id"], run["model_id"], run["run_id"])
        for task in value["cohort"]["tasks"]
        for run in task["runs"]
    } == {(task_id, model_id, run_id) for (task_id, model_id), run_id in RUN_IDS.items()}


def test_m3_freeze_closes_post_hoc_and_held_out_paths() -> None:
    value = json.loads(_freeze_bytes())

    assert value["anti_post_hoc_policy"]["same_cohort_prohibited_changes"] == [
        "acquisition-config",
        "feature-config",
        "model-config",
        "model-set",
        "seed",
    ]
    assert value["anti_post_hoc_policy"]["preserve_negative_and_discordant_results"] is True
    assert value["authorization_boundaries"] == {
        "candidate_truth_consumer": "capability-separated-evaluator-after-sealed-prediction",
        "gate_2_authorized": False,
        "held_out_reads": 0,
        "m3_execution_authorized": False,
    }


def test_m3_loader_rejects_noncanonical_or_drifted_records() -> None:
    value = json.loads(_freeze_bytes())
    noncanonical = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    with pytest.raises(ContractValidationError, match="canonical"):
        load_m3_freeze_record(noncanonical)

    value["cohort"]["seed"] = 1730
    with pytest.raises(ContractValidationError, match="seed"):
        load_m3_freeze_record(canonical_json_bytes(value))


def test_gate_b_packet_exact_covers_26_shared_and_42_per_attempt_objects() -> None:
    payload = _packet_bytes()
    packet = json.loads(payload)
    loaded = load_gate_b_packet(payload)
    inventory = packet["evidence_inventory"]

    assert loaded.canonical_bytes == payload
    assert canonical_json_bytes(packet) == payload
    assert len(inventory) == 68
    assert len({item["object_key"] for item in inventory}) == 68
    assert [item["publication_order"] for item in inventory] == list(range(1, 69))
    assert sum(item["scope"] == "shared" for item in inventory) == 26
    assert sum(item["scope"] == "per-attempt" for item in inventory) == 42
    assert all(item["object_key"].startswith(EVIDENCE_PREFIX) for item in inventory)


def test_gate_b_packet_uses_exact_known_hashes_and_no_fake_runtime_hashes() -> None:
    packet = json.loads(_packet_bytes())
    embedded_index = packet["gate_a_known_inputs"]
    inventory = packet["evidence_inventory"]
    known = [item for item in inventory if item["payload"]["status"] == "known"]
    dynamic = [item for item in inventory if item["payload"]["status"] == "runtime-generated"]

    assert embedded_index["artifact"] == {
        "sha256": "783e338fd20396ea340d10140592c2752a30ac8fc7115e71b432c0796fe03ae2",
        "size_bytes": 16_873,
    }
    assert canonical_json_bytes(embedded_index["value"]) == KNOWN_INPUTS_PATH.read_bytes()

    assert len(known) == 38
    assert len(dynamic) == 30
    for item in known:
        assert isinstance(item["payload"]["size_bytes"], int)
        assert len(item["payload"]["sha256"]) == 64
    for item in dynamic:
        assert item["payload"]["size_bytes"] is None
        assert item["payload"]["sha256"] is None
        assert item["payload"]["generation_rule"]
        assert item["payload"]["validation_rule"]

    success_markers = [item for item in known if item["kind"] == "success-marker"]
    assert len(success_markers) == 6
    assert all(item["payload"]["size_bytes"] == 0 for item in success_markers)
    assert all(
        item["payload"]["sha256"]
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        for item in success_markers
    )


def test_gate_b_packet_freezes_alpha_ridge_first_fail_fast_publication() -> None:
    packet = json.loads(_packet_bytes())
    plan = packet["publication_plan"]
    first_run_id = RUN_IDS[("alpha-amylase", "ridge-sklearn-v1")]
    first_attempt = [
        item
        for item in packet["evidence_inventory"]
        if item.get("run_id") == first_run_id and item["scope"] == "per-attempt"
    ]

    assert plan["first_run_id"] == first_run_id
    assert plan["first_task_model"] == {
        "model_id": "ridge-sklearn-v1",
        "task_id": "alpha-amylase",
    }
    assert plan["remaining_runs_start_condition"] == (
        "independent-fresh-readback-exact-covers-shared-26-and-alpha-ridge-7"
    )
    assert plan["first_run_failure_action"] == "stop-with-other-five-runs-at-zero"
    assert [item["kind"] for item in first_attempt] == [
        "prediction",
        "metric-result",
        "metric-result",
        "metric-result",
        "metric-result",
        "run-attempt",
        "success-marker",
    ]
    assert first_attempt[-1]["object_key"].endswith("/_SUCCESS")


def test_gate_b_packet_records_external_refs_caps_and_minimal_cam_boundary() -> None:
    packet = json.loads(_packet_bytes())

    assert len(packet["external_manifest_references"]) == 6
    assert packet["execution_caps"] == {
        "automatic_retry": 0,
        "cos_net_new_bytes": 5 * 1024**3,
        "cos_object_count": 68,
        "formal_attempts": 6,
        "max_cost_cny": 40,
        "max_network_transfer_bytes": 10 * 1024**3,
        "max_ram_bytes": 16 * 1024**3,
        "max_scratch_bytes": 20 * 1024**3,
        "max_vcpu": 8,
        "max_wall_seconds": 4 * 60 * 60,
        "per_run_spec_attempts": 1,
    }
    permission = packet["credential_boundary"]
    assert permission["accepted_inputs"]["operations"] == [
        "fresh-get",
        "head",
        "restricted-inventory",
    ]
    assert permission["accepted_inputs"]["cam_actions"] == [
        "name/cos:GetBucket",
        "name/cos:GetObject",
        "name/cos:HeadObject",
    ]
    assert permission["new_evidence_prefix"]["operations"] == [
        "forbid-overwrite-put",
        "fresh-get",
        "head",
        "restricted-inventory",
    ]
    assert permission["new_evidence_prefix"]["cam_actions"] == [
        "name/cos:GetBucket",
        "name/cos:GetObject",
        "name/cos:HeadObject",
        "name/cos:PutObject",
    ]
    assert permission["explicitly_forbidden"] == [
        "delete-object",
        "overwrite",
        "other-buckets",
        "other-prefixes",
    ]
    assert packet["preparation_truth_boundary"] == {
        "candidate_truth_reads": 0,
        "cos_writes": 0,
        "evaluator_calls": 0,
        "held_out_reads": 0,
        "run_attempts_created": 0,
        "success_markers_created": 0,
    }


def test_operator_plan_fresh_validates_prediction_before_truth_and_fails_fast() -> None:
    packet = json.loads(_packet_bytes())
    plan = packet["operator_plan"]
    plan_bytes = canonical_json_bytes(plan)
    loaded = load_gate_b_operator_plan(plan_bytes)
    first_run_id = RUN_IDS[("alpha-amylase", "ridge-sklearn-v1")]
    first_run_steps = [step for step in plan["steps"] if step.get("run_id") == first_run_id]

    assert loaded.canonical_bytes == plan_bytes
    operations = [step["operation"] for step in first_run_steps]
    assert operations.index("fresh-validate-sealed-prediction") < operations.index(
        "evaluator-read-candidate-truth-return-four-aggregate-results"
    )
    assert operations[-1] == "forbid-overwrite-put-empty-success-marker"
    assert all(step["on_failure"] == "stop-no-retry-no-next-run" for step in plan["steps"])
    checkpoint = plan["alpha_ridge_checkpoint"]
    assert checkpoint["required_exact_key_count"] == 33
    assert checkpoint["failure_action"] == "stop-with-other-five-runs-at-zero"
    assert plan["final_readback"]["required_exact_key_count"] == 68
    assert plan["final_readback"]["external_manifest_reference_count"] == 6
    assert plan["final_readback"]["reject_missing_extra_duplicate"] is True
    external_steps = [
        step
        for step in plan["steps"]
        if step["operation"] == "fresh-readback-validate-external-manifest-reference"
    ]
    assert len(external_steps) == 6
    first_write_sequence = next(
        step["sequence"] for step in plan["steps"] if step["operation"].startswith("forbid-")
    )
    assert all(step["sequence"] < first_write_sequence for step in external_steps)


def test_operator_plan_loader_rejects_truth_before_prediction_validation() -> None:
    packet = json.loads(_packet_bytes())
    plan = packet["operator_plan"]
    first_run_id = RUN_IDS[("alpha-amylase", "ridge-sklearn-v1")]
    first_run_steps = [step for step in plan["steps"] if step.get("run_id") == first_run_id]
    validation = next(
        step for step in first_run_steps if step["operation"] == "fresh-validate-sealed-prediction"
    )
    evaluator = next(
        step
        for step in first_run_steps
        if step["operation"] == "evaluator-read-candidate-truth-return-four-aggregate-results"
    )
    validation["sequence"], evaluator["sequence"] = evaluator["sequence"], validation["sequence"]
    plan["steps"].sort(key=lambda step: step["sequence"])

    with pytest.raises(ContractValidationError, match="prediction"):
        load_gate_b_operator_plan(canonical_json_bytes(plan))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda packet: packet["execution_caps"].__setitem__("max_cost_cny", 40_000),
            "execution caps",
        ),
        (
            lambda packet: packet["credential_boundary"]["explicitly_forbidden"].clear(),
            "credential boundary",
        ),
        (
            lambda packet: packet["credential_boundary"]["accepted_inputs"]["cam_actions"].remove(
                "name/cos:HeadObject"
            ),
            "credential boundary",
        ),
        (
            lambda packet: packet.__setitem__("source_git_commit", "0" * 40),
            "source commit",
        ),
    ],
)
def test_gate_b_packet_loader_rejects_governance_drift(
    mutation: Callable[[dict[str, Any]], None], message: str
) -> None:
    with pytest.raises(ContractValidationError, match=message):
        load_gate_b_packet(_mutated_packet(mutation))


def test_operator_plan_loader_rejects_non_first_run_before_checkpoint() -> None:
    plan = json.loads(_packet_bytes())["operator_plan"]
    checkpoint_index = next(
        index
        for index, step in enumerate(plan["steps"])
        if step["operation"] == "independent-fresh-readback-exact-cover-alpha-ridge-checkpoint"
    )
    later_index = next(
        index
        for index, step in enumerate(plan["steps"])
        if index > checkpoint_index and "run_id" in step
    )
    later = plan["steps"].pop(later_index)
    plan["steps"].insert(checkpoint_index, later)
    for sequence, step in enumerate(plan["steps"], start=1):
        step["sequence"] = sequence

    with pytest.raises(ContractValidationError, match="checkpoint"):
        load_gate_b_operator_plan(canonical_json_bytes(plan))


@pytest.mark.parametrize(
    "operation",
    [
        "independent-fresh-readback-exact-cover-alpha-ridge-checkpoint",
        "independent-fresh-readback-exact-cover-final-inventory",
    ],
)
def test_operator_plan_loader_rejects_readback_key_drift(operation: str) -> None:
    plan = json.loads(_packet_bytes())["operator_plan"]
    readback = next(step for step in plan["steps"] if step["operation"] == operation)
    readback["required_exact_keys"][0] = EVIDENCE_PREFIX + "outside-inventory.json"

    with pytest.raises(ContractValidationError, match="exact keys"):
        load_gate_b_operator_plan(canonical_json_bytes(plan))


def test_gate_b_packet_loader_rejects_incomplete_per_run_closure() -> None:
    def mutate(packet: dict[str, Any]) -> None:
        inventory = packet["evidence_inventory"]
        first_metric = next(item for item in inventory if item.get("kind") == "metric-result")
        first_metric["kind"] = "unknown-result"

    with pytest.raises(ContractValidationError, match="seven-object closure"):
        load_gate_b_packet(_mutated_packet(mutate))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda packet: next(
                item for item in packet["evidence_inventory"] if item["kind"] == "metric-result"
            )["payload"].pop("generation_rule"),
            "metric-result runtime payload",
        ),
        (
            lambda packet: next(
                item for item in packet["evidence_inventory"] if item["kind"] == "run-attempt"
            )["payload"].__setitem__("validation_rule", "accept-anything"),
            "RunAttempt runtime payload",
        ),
    ],
)
def test_gate_b_packet_loader_rejects_runtime_payload_semantic_drift(
    mutation: Callable[[dict[str, Any]], None], message: str
) -> None:
    with pytest.raises(ContractValidationError, match=message):
        load_gate_b_packet(_mutated_packet(mutation))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda packet: packet["gate_a_known_inputs"]["artifact"].__setitem__(
                "sha256", "0" * 64
            ),
            "known-input index identity",
        ),
        (
            lambda packet: packet["m3_freeze_record"]["cohort"]["tasks"][0].__setitem__(
                "feature_root_sha256", "0" * 64
            ),
            "M3 feature roots",
        ),
        (
            lambda packet: next(
                item
                for item in packet["evidence_inventory"]
                if item["kind"] == "feature-descriptor"
            )["payload"].__setitem__("sha256", "0" * 64),
            "known payload identities",
        ),
        (
            lambda packet: next(
                item for item in packet["evidence_inventory"] if item["kind"] == "prediction"
            )["payload"].__setitem__("sha256", "0" * 64),
            "known payload identities",
        ),
    ],
)
def test_gate_b_packet_loader_rejects_exact_scientific_identity_drift(
    mutation: Callable[[dict[str, Any]], None], message: str
) -> None:
    with pytest.raises(ContractValidationError, match=message):
        load_gate_b_packet(_mutated_packet(mutation))


def test_operator_loader_rejects_issue_58_prefix_drift() -> None:
    plan = json.loads(_packet_bytes())["operator_plan"]
    list_fields = (
        "object_keys",
        "output_object_keys",
        "referenced_object_keys",
        "required_exact_keys",
    )
    for step in plan["steps"]:
        for field in ("object_key", "output_object_key"):
            if isinstance(step.get(field), str) and step[field].startswith(EVIDENCE_PREFIX):
                step[field] = step[field].replace(
                    EVIDENCE_PREFIX, "m2/outside-authorized-prefix/", 1
                )
        for field in list_fields:
            if isinstance(step.get(field), list):
                step[field] = [
                    key.replace(EVIDENCE_PREFIX, "m2/outside-authorized-prefix/", 1)
                    for key in step[field]
                ]

    with pytest.raises(ContractValidationError, match="evidence prefix"):
        load_gate_b_operator_plan(canonical_json_bytes(plan))


def test_operator_loader_rejects_issue_58_run_id_drift() -> None:
    plan = json.loads(_packet_bytes())["operator_plan"]
    first_run_id = plan["alpha_ridge_checkpoint"]["first_run_id"]
    plan["alpha_ridge_checkpoint"]["first_run_id"] = "0" * 64
    for step in plan["steps"]:
        if step.get("run_id") == first_run_id:
            step["run_id"] = "0" * 64

    with pytest.raises(ContractValidationError, match="six-run cohort"):
        load_gate_b_operator_plan(canonical_json_bytes(plan))


@pytest.mark.parametrize("scope", ["accepted_inputs", "new_evidence_prefix"])
def test_gate_b_packet_requires_head_object_for_each_head_operation(scope: str) -> None:
    def mutate(packet: dict[str, Any]) -> None:
        boundary = packet["credential_boundary"]
        scoped = boundary[scope]
        scoped["cam_actions"].remove("name/cos:HeadObject")

    with pytest.raises(ContractValidationError, match="credential boundary"):
        load_gate_b_packet(_mutated_packet(mutate))


class _RecordingRuntime:
    def __init__(
        self,
        *,
        fail_binding: bool = False,
        fail_operation: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.bound_packet: Mapping[str, Any] | None = None
        self.fail_binding = fail_binding
        self.fail_operation = fail_operation

    def bind_reviewed_packet(self, packet: Mapping[str, Any]) -> None:
        self.bound_packet = packet
        if self.fail_binding:
            raise RuntimeError("injected boundary failure")

    def __getattr__(self, method_name: str) -> Callable[[Mapping[str, Any]], None]:
        def call(step: Mapping[str, Any]) -> None:
            operation = str(step["operation"])
            self.calls.append((method_name, dict(step)))
            if operation == self.fail_operation:
                raise RuntimeError("injected failure")

        return call


class _BoundaryMutatingRuntime(_RecordingRuntime):
    def __init__(self, path: tuple[str, ...], value: Any) -> None:
        super().__init__()
        self.path = path
        self.value = value

    def bind_reviewed_packet(self, packet: Mapping[str, Any]) -> None:
        super().bind_reviewed_packet(packet)
        target: Any = packet
        for field in self.path[:-1]:
            target = target[field]
        target[self.path[-1]] = self.value


def test_executable_operator_binds_full_packet_to_all_113_steps_without_retry() -> None:
    runtime = _RecordingRuntime()

    execute_gate_b_operator(_packet_bytes(), cast(GateBOperatorRuntime, runtime))

    assert runtime.bound_packet is not None
    assert runtime.bound_packet["execution_caps"]["max_cost_cny"] == 40
    assert (
        runtime.bound_packet["credential_boundary"]["new_evidence_prefix"]["exact_prefix"]
        == EVIDENCE_PREFIX
    )
    assert len(runtime.calls) == 113
    assert [call[1]["sequence"] for call in runtime.calls] == list(range(1, 114))
    assert sum(name == "publish_known_shared" for name, _ in runtime.calls) == 26
    assert sum(name == "evaluate_four_aggregate_results" for name, _ in runtime.calls) == 6
    assert sum(name == "publish_empty_success_marker" for name, _ in runtime.calls) == 6
    assert runtime.calls[-1][0] == "readback_exact_keys"


def test_executable_operator_boundary_failure_has_zero_steps_truth_and_writes() -> None:
    runtime = _RecordingRuntime(fail_binding=True)

    with pytest.raises(RuntimeError, match="boundary failure"):
        execute_gate_b_operator(_packet_bytes(), cast(GateBOperatorRuntime, runtime))

    assert runtime.bound_packet is not None
    assert runtime.calls == []


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("execution_caps", "max_cost_cny"), 40_000),
        (("credential_boundary", "explicitly_forbidden"), []),
        (("source_git_commit",), "0" * 40),
        (("operator_plan", "steps"), []),
    ],
)
def test_executable_operator_binds_recursive_immutable_packet_snapshot(
    path: tuple[str, ...], value: Any
) -> None:
    runtime = _BoundaryMutatingRuntime(path, value)

    with pytest.raises(TypeError):
        execute_gate_b_operator(_packet_bytes(), cast(GateBOperatorRuntime, runtime))

    assert runtime.bound_packet is not None
    assert runtime.calls == []


def test_executable_operator_external_failure_has_zero_truth_and_zero_writes() -> None:
    runtime = _RecordingRuntime(
        fail_operation="fresh-readback-validate-external-manifest-reference"
    )

    with pytest.raises(RuntimeError, match="injected failure"):
        execute_gate_b_operator(_packet_bytes(), cast(GateBOperatorRuntime, runtime))

    assert len(runtime.calls) == 1
    assert all(not name.startswith("publish_") for name, _ in runtime.calls)
    assert all(name != "evaluate_four_aggregate_results" for name, _ in runtime.calls)


def test_executable_operator_first_evaluator_failure_stops_other_five_runs() -> None:
    runtime = _RecordingRuntime(
        fail_operation="evaluator-read-candidate-truth-return-four-aggregate-results"
    )

    with pytest.raises(RuntimeError, match="injected failure"):
        execute_gate_b_operator(_packet_bytes(), cast(GateBOperatorRuntime, runtime))

    evaluator_calls = [
        step for name, step in runtime.calls if name == "evaluate_four_aggregate_results"
    ]
    assert len(evaluator_calls) == 1
    assert evaluator_calls[0]["run_id"] == RUN_IDS[("alpha-amylase", "ridge-sklearn-v1")]
    assert all(name != "publish_aggregate_metric_result" for name, _ in runtime.calls)
    assert all(name != "publish_empty_success_marker" for name, _ in runtime.calls)
