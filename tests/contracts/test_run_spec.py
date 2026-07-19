from __future__ import annotations

from dataclasses import replace

import pytest

from pcdb.contracts import RunSpec


def make_run_spec() -> RunSpec:
    return RunSpec(
        schema_version="1",
        protocol_version="smoke-v1",
        git_commit="a" * 40,
        environment_digest="b" * 64,
        dataset_manifest_sha256="c" * 64,
        split_sha256="d" * 64,
        feature_sha256="e" * 64,
        experiment_config_sha256="f" * 64,
        model_id="ridge",
        model_config_sha256="1" * 64,
        acquisition_id="random",
        acquisition_config_sha256="2" * 64,
        seed=0,
        initial_sample_size=4,
        batch_size=2,
        budget=4,
    )


def test_run_id_is_deterministic() -> None:
    first = make_run_spec()
    second = make_run_spec()

    assert first.canonical_json() == second.canonical_json()
    assert first.run_id == second.run_id
    assert len(first.run_id) == 64


def test_run_id_changes_when_seed_changes() -> None:
    original = make_run_spec()

    assert original.run_id != replace(original, seed=1).run_id


def test_run_spec_rejects_abbreviated_git_commit() -> None:
    with pytest.raises(ValueError, match="full lowercase"):
        replace(make_run_spec(), git_commit="abc1234")
