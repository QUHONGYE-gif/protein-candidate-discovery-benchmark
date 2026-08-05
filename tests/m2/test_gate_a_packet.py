"""Zero-real-data identity checks for the Issue #58 Gate A packet."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from pcdb.contracts.experiment import canonical_json_bytes, load_experiment_config
from pcdb.contracts.run import RunSpec
from pcdb.m2.gate_a import (
    GATE_A_MODELS,
    GATE_A_TASKS,
    build_gate_a_packet,
    packet_identity,
)
from pcdb.m2.model_identity import MODEL_CONFIG_SHA256, MODEL_CONFIG_SIZE_BYTES
from pcdb.m2.models import model_config_bytes

ROOT = Path(__file__).resolve().parents[2]
REVIEWED_COMMIT = "a" * 40


def _packet() -> tuple[bytes, dict[str, Any]]:
    payload = build_gate_a_packet(ROOT, REVIEWED_COMMIT)
    return payload, json.loads(payload)


def test_gate_a_packet_is_canonical_and_fully_recomputable_from_reviewed_git_bytes() -> None:
    payload, packet = _packet()
    size_bytes, sha256 = packet_identity(payload)

    assert canonical_json_bytes(packet) == payload
    assert size_bytes == len(payload)
    assert sha256 == hashlib.sha256(payload).hexdigest()
    assert packet["reviewed_git_commit"] == REVIEWED_COMMIT
    assert packet["scratch_logical_root"] == "scratch://pcdb/issue-58/gate-a"
    assert packet["truth_boundary"] == {
        "candidate_truth_reads": 0,
        "cos_accesses": 0,
        "evaluator_calls": 0,
        "held_out_reads": 0,
        "real_input_reads_during_packet_build": 0,
        "server_accesses_during_packet_build": 0,
    }


def test_packet_binds_two_formal_experiment_configs_and_all_model_bytes() -> None:
    _, packet = _packet()
    experiments = packet["experiment_configs"]
    assert set(experiments) == set(GATE_A_TASKS)
    for task_id, reference in experiments.items():
        payload = (ROOT / reference["object_path"]).read_bytes()
        loaded = load_experiment_config(payload)
        assert loaded.value["task_id"] == task_id
        assert tuple(loaded.value["models"]) == GATE_A_MODELS
        assert tuple(loaded.value["seeds"]) == (1729,)
        assert len(payload) == reference["size_bytes"]
        assert hashlib.sha256(payload).hexdigest() == reference["sha256"]

    models = packet["model_configs"]
    assert set(models) == set(GATE_A_MODELS)
    for model_id, reference in models.items():
        payload = model_config_bytes(model_id)
        assert len(payload) == reference["size_bytes"] == MODEL_CONFIG_SIZE_BYTES[model_id]
        assert (
            hashlib.sha256(payload).hexdigest()
            == reference["sha256"]
            == MODEL_CONFIG_SHA256[model_id]
        )


def test_six_templates_are_explicitly_not_runspecs_until_real_feature_roots_exist() -> None:
    payload, packet = _packet()
    templates = packet["run_spec_templates"]
    expected_fields = {field.name for field in fields(RunSpec)} - {"feature_sha256"}

    assert len(templates) == 6
    assert {
        (template["task_id"], template["run_spec_fields"]["model_id"]) for template in templates
    } == {(task, model) for task in GATE_A_TASKS for model in GATE_A_MODELS}
    for template in templates:
        assert template["status"] == "template-not-a-run-spec"
        assert template["unresolved_fields"] == ["feature_sha256", "run_id"]
        assert set(template["run_spec_fields"]) == expected_fields
        assert template["run_spec_fields"]["seed"] == 1729
        assert template["run_spec_fields"]["initial_sample_size"] == 1
        assert template["run_spec_fields"]["batch_size"] == 1
        assert template["run_spec_fields"]["budget"] == 1
    assert b'"feature_sha256": "' not in payload
    assert b'"run_id": "' not in payload
    assert b"UNSET" not in payload


def test_environment_and_acquisition_identities_are_exact() -> None:
    _, packet = _packet()
    environment = packet["environment_candidate"]
    environment_bytes = (ROOT / environment["object_path"]).read_bytes()
    assert canonical_json_bytes(json.loads(environment_bytes)) == environment_bytes
    assert len(environment_bytes) == environment["size_bytes"]
    assert hashlib.sha256(environment_bytes).hexdigest() == environment["sha256"]
    assert packet["acquisition_config"] == {
        "derivation": "pcdb.contracts.prediction.M2_STATIC_ACQUISITION_CONFIG_BYTES",
        "sha256": "d50c876c6d114eb74a878bbce43a2f760e21507dad80c0d0f87ce02f2ff7c9d5",
        "size_bytes": 186,
    }
