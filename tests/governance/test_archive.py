from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from pcdb.cli import main
from pcdb.contracts.experiment import canonical_json_bytes
from pcdb.governance.archive import (
    ArchiveAttemptRecorder,
    ArchiveObject,
    ImmutableArchiveStore,
    LocalArchiveStore,
    archive_evidence_digest,
    archive_terminal_milestone,
)
from pcdb.governance.checks import CheckViolation, sha256_bytes
from tests.governance.helpers import (
    FakeDecisionEvidenceVerifier,
    attach_example_attestations,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
CONFIG = REPOSITORY_ROOT / "configs/governance/v2.json"
AUTHORITIES = ("github:QUHONGYE-gif", "github:willzhang")


def _write_json(path: Path, value: object) -> bytes:
    payload = canonical_json_bytes(value)
    path.write_bytes(payload)
    return payload


def _reference(path: str, payload: bytes, archive_path: str) -> dict[str, object]:
    return {
        "archive_path": archive_path,
        "path": path,
        "sha256": sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def _build_bundle(
    tmp_path: Path,
    *,
    verdict: str = "accept",
    terminal_class: str = "scientific_terminal",
) -> tuple[Path, Path, Path]:
    root = tmp_path / "source"
    root.mkdir()
    scientific_contract = {
        "allowed_deviations": [],
        "budget_cost_model": "fixed synthetic budget",
        "candidate_high_value_definition": "prospective synthetic upper tail",
        "comparison_set": ["greedy", "exploration"],
        "dataset_split_semantics": "public synthetic development fixture",
        "held_out_boundary": "no held-out access",
        "known_risks": ["synthetic-only evidence"],
        "primary_metrics": ["simple_regret"],
        "research_question": "Does the synthetic branch alter immediate decision value?",
        "statistical_rule": "paired descriptive result only",
    }
    contract_digest = sha256_bytes(canonical_json_bytes(scientific_contract))
    gate_a = {
        "approvals": [
            {
                "authority": AUTHORITIES[1],
                "decided_at": "2026-09-02T00:01:00Z",
                "explicit_boundaries": [],
                "verdict": "authorized",
            }
        ],
        "contract_digest": contract_digest,
        "contract_id": "m5-test-contract",
        "contract_version": 1,
        "decided_at": "2026-09-02T00:01:00Z",
        "decision_id": "gate-a-m5-test-v1",
        "formulation": "proposed_and_approved",
        "proposers": [AUTHORITIES[0]],
        "rationale": "test contract authorization",
        "record_status": "final",
        "schema_version": "pcdb-gate-a-decision-v1",
        "verdict": "authorized",
    }
    attach_example_attestations(gate_a, first_comment_id=101)
    gate_b = {
        "claim_boundary": "synthetic test result only",
        "contract_digest": contract_digest,
        "contract_id": "m5-test-contract",
        "contract_version": 1,
        "decided_at": "2026-09-02T00:02:00Z",
        "decision_id": "gate-b-m5-test-v1",
        "evidence_digest": "e" * 64,
        "explicit_boundary_approvals": [],
        "rationale": "terminal test decision",
        "record_status": "final",
        "reviewers": [{"authority": AUTHORITIES[1], "relation": "not_summary_preparer"}],
        "schema_version": "pcdb-gate-b-decision-v1",
        "verdict": verdict,
    }
    gate_a_payload = _write_json(root / "gate-a.json", gate_a)
    gate_b_payload = _write_json(root / "gate-b.json", gate_b)
    input_payload = _write_json(
        root / "input-manifest.json",
        {"dataset": "public-synthetic", "schema_version": "test-input-v1"},
    )
    manifest: dict[str, Any] = {
        "contract_digest": contract_digest,
        "contract_id": "m5-test-contract",
        "contract_version": 1,
        "gate_a": {"path": "gate-a.json", "sha256": sha256_bytes(gate_a_payload)},
        "gate_b": {
            "path": "gate-b.json",
            "sha256": sha256_bytes(gate_b_payload),
            "verdict": verdict,
        },
        "input_artifacts": [
            {
                "consumers": ["runner"],
                "path": "input-manifest.json",
                "role": "dataset_manifest",
                "sha256": sha256_bytes(input_payload),
                "size_bytes": len(input_payload),
                "visibility": "observable",
            }
        ],
        "major_boundaries": [],
        "manifest_revision": 2,
        "manifest_status": "terminal",
        "milestone_id": "M5T",
        "run_policy": {
            "attempt_semantics": "append_only",
            "completion_marker": "_SUCCESS_last",
            "ordinary_run_cos_writes": 0,
            "storage_mode": "server_first",
        },
        "schema_version": "pcdb-milestone-contract-manifest-v1",
        "scientific_authorities": list(AUTHORITIES),
        "scientific_contract": scientific_contract,
    }
    manifest_payload = _write_json(root / "contract.json", manifest)
    run_identity_payload = _write_json(
        root / "run-identity.json",
        {
            "attempt_id": "attempt-001",
            "attempt_root": "/srv/pcdb/runs/M5T/attempt-001",
            "contract_digest": contract_digest,
            "cos_write_count": 0,
            "environment_digest": "d" * 64,
            "git_commit": "a" * 40,
            "git_tree": "b" * 40,
            "input_manifest_digest": sha256_bytes(input_payload),
            "observed_usage": {
                "cost": 0,
                "cpu_seconds": 2,
                "network_bytes": 0,
                "peak_rss_bytes": 1024,
                "scratch_bytes": 2048,
                "wall_seconds": 3,
            },
            "resource_caps": {
                "cost": 1,
                "cpu_seconds": 10,
                "network_bytes": 1024,
                "peak_rss_bytes": 4096,
                "scratch_bytes": 4096,
                "wall_seconds": 10,
            },
            "run_id": "c" * 64,
            "schema_version": "pcdb-governance-run-identity-v1",
            "source_clean": True,
        },
    )
    payloads = {
        "contract_manifest": (
            "contract.json",
            manifest_payload,
            "contract-manifest.json",
        ),
        "run_identity": ("run-identity.json", run_identity_payload, "run-identity.json"),
        "final_result": ("final.csv", b"metric,value\nsimple_regret,0.1\n", "final.csv"),
        "important_log": ("important.log", b"terminal reached\n", "important.log"),
        "summary": ("summary.md", b"# Synthetic test summary\n", "summary.md"),
        "provenance": (
            "provenance.json",
            b'{"source":"public synthetic"}\n',
            "provenance.json",
        ),
        "gate_b_decision": ("gate-b.json", gate_b_payload, "gate-b-decision.json"),
    }
    inventory: list[dict[str, Any]] = []
    for role, (source_path, payload, archive_path) in payloads.items():
        if role not in {"contract_manifest", "gate_b_decision"}:
            (root / source_path).write_bytes(payload)
        inventory.append({"role": role, **_reference(source_path, payload, archive_path)})
    evidence_objects = tuple(
        ArchiveObject(
            role=str(item["role"]),
            path=str(item["path"]),
            archive_path=str(item["archive_path"]),
            size_bytes=int(item["size_bytes"]),
            sha256=str(item["sha256"]),
        )
        for item in inventory
    )
    gate_b["evidence_digest"] = archive_evidence_digest(evidence_objects)
    attach_example_attestations(gate_b, first_comment_id=201)
    gate_b_payload = _write_json(root / "gate-b.json", gate_b)
    manifest["gate_b"]["sha256"] = sha256_bytes(gate_b_payload)
    manifest_payload = _write_json(root / "contract.json", manifest)
    for item in inventory:
        if item["role"] == "contract_manifest":
            item.update(_reference("contract.json", manifest_payload, "contract-manifest.json"))
        elif item["role"] == "gate_b_decision":
            item.update(_reference("gate-b.json", gate_b_payload, "gate-b-decision.json"))
    role_map = {item["role"]: item for item in inventory}
    contract_reference = dict(role_map["contract_manifest"])
    del contract_reference["role"]
    gate_b_reference = dict(role_map["gate_b_decision"])
    del gate_b_reference["role"]
    request = {
        "archive_id": "archive-001",
        "attempt_id": "attempt-001",
        "contract_manifest": contract_reference,
        "destination_prefix": "milestone-archives/v2/M5T/archive-001/",
        "gate_b_decision": gate_b_reference,
        "limits": {"max_bytes": 1_000_000, "max_objects": 20},
        "milestone_id": "M5T",
        "request_status": "final",
        "schema_version": "pcdb-milestone-archive-request-v1",
        "source_inventory": inventory,
        "terminal_class": terminal_class,
    }
    request_path = root / "archive-request.json"
    _write_json(request_path, request)
    return root, request_path, root / "gate-b.json"


class RecordingStore(LocalArchiveStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.writes: list[str] = []

    def put_forbid_overwrite(self, key: str, payload: bytes) -> None:
        self.writes.append(key)
        super().put_forbid_overwrite(key, payload)


class CorruptingStore:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def list_keys(self, prefix: str) -> tuple[str, ...]:
        return tuple(sorted(key for key in self.data if key.startswith(prefix)))

    def put_forbid_overwrite(self, key: str, payload: bytes) -> None:
        if key in self.data:
            raise FileExistsError(key)
        self.data[key] = payload

    def get(self, key: str) -> bytes:
        if key.endswith("summary.md"):
            return b"corrupted"
        return self.data[key]


@pytest.mark.parametrize("verdict", ["accept", "reject", "inconclusive"])
def test_all_valid_scientific_terminal_verdicts_archive_with_success_last(
    tmp_path: Path, verdict: str
) -> None:
    root, request_path, gate_b_path = _build_bundle(tmp_path, verdict=verdict)
    store = RecordingStore(tmp_path / "archive")
    recorder_root = tmp_path / "attempts"

    receipt = archive_terminal_milestone(
        request_path=request_path,
        source_root=root,
        gate_b_path=gate_b_path,
        config_path=CONFIG,
        store=store,
        recorder=ArchiveAttemptRecorder(recorder_root),
        evidence_verifier=FakeDecisionEvidenceVerifier(),
    )

    assert receipt.status == "archived"
    assert receipt.object_count == 9
    assert receipt.check_report.overall_status == "pass"
    assert {item.category for item in receipt.check_report.checks} >= {
        "authenticated_human_evidence",
        "archive_readback",
        "completion_marker",
        "hash_exact_bytes",
        "hidden_label_leakage",
        "inventory_cross_links",
        "prefix_storage_safety",
        "resource_cost_cap",
        "schema_domain",
        "source_environment_identity",
    }
    assert store.writes[-1].endswith("/_SUCCESS")
    assert store.get(store.writes[-1]) == b""
    archive_manifest = json.loads(
        store.get("milestone-archives/v2/M5T/archive-001/archive-manifest.json")
    )
    archive_schema = json.loads(
        (REPOSITORY_ROOT / "schemas/milestone-archive-manifest.schema.json").read_bytes()
    )
    Draft202012Validator(archive_schema, format_checker=FormatChecker()).validate(archive_manifest)
    gate_b = json.loads(gate_b_path.read_bytes())
    assert archive_manifest["evidence_digest"] == gate_b["evidence_digest"]
    attempt_records = sorted((recorder_root / "M5T/archive-attempts").glob("*.json"))
    assert [path.name for path in attempt_records] == [
        "attempt-001.started.json",
        "attempt-001.succeeded.json",
    ]


@pytest.mark.parametrize(
    "terminal_class",
    ["invalid_run", "debug_run", "aborted_attempt", "failed_implementation_run"],
)
def test_non_scientific_attempt_classes_write_only_server_record(
    tmp_path: Path, terminal_class: str
) -> None:
    root, request_path, gate_b_path = _build_bundle(tmp_path, terminal_class=terminal_class)
    store = RecordingStore(tmp_path / "archive")
    recorder_root = tmp_path / "attempts"

    receipt = archive_terminal_milestone(
        request_path=request_path,
        source_root=root,
        gate_b_path=gate_b_path,
        config_path=CONFIG,
        store=store,
        recorder=ArchiveAttemptRecorder(recorder_root),
        evidence_verifier=FakeDecisionEvidenceVerifier(),
    )

    assert receipt.status == "skipped"
    assert receipt.object_count == 0
    assert store.writes == []
    assert (recorder_root / "M5T/archive-attempts/attempt-001.skipped.json").is_file()


def test_readback_failure_never_writes_completion_marker(tmp_path: Path) -> None:
    root, request_path, gate_b_path = _build_bundle(tmp_path)
    store: ImmutableArchiveStore = CorruptingStore()
    recorder_root = tmp_path / "attempts"

    with pytest.raises(CheckViolation, match="fresh archive readback mismatch"):
        archive_terminal_milestone(
            request_path=request_path,
            source_root=root,
            gate_b_path=gate_b_path,
            config_path=CONFIG,
            store=store,
            recorder=ArchiveAttemptRecorder(recorder_root),
            evidence_verifier=FakeDecisionEvidenceVerifier(),
        )

    assert not any(key.endswith("_SUCCESS") for key in store.list_keys(""))
    assert (recorder_root / "M5T/archive-attempts/attempt-001.failed.json").is_file()


def test_existing_prefix_fails_closed_without_overwrite(tmp_path: Path) -> None:
    root, request_path, gate_b_path = _build_bundle(tmp_path)
    store = RecordingStore(tmp_path / "archive")
    store.put_forbid_overwrite("milestone-archives/v2/M5T/archive-001/existing.txt", b"existing")

    with pytest.raises(CheckViolation, match="archive-prefix-safety=fail"):
        archive_terminal_milestone(
            request_path=request_path,
            source_root=root,
            gate_b_path=gate_b_path,
            config_path=CONFIG,
            store=store,
            recorder=ArchiveAttemptRecorder(tmp_path / "attempts"),
            evidence_verifier=FakeDecisionEvidenceVerifier(),
        )

    assert store.get("milestone-archives/v2/M5T/archive-001/existing.txt") == b"existing"
    assert not any(key.endswith("_SUCCESS") for key in store.list_keys(""))


def test_archive_rejects_gate_b_inventory_path_different_from_validated_decision(
    tmp_path: Path,
) -> None:
    root, request_path, gate_b_path = _build_bundle(tmp_path)
    alternate = root / "alternate-gate-b.json"
    alternate.write_bytes(gate_b_path.read_bytes())
    request = json.loads(request_path.read_bytes())
    request["gate_b_decision"]["path"] = alternate.name
    gate_b_entry = next(
        item for item in request["source_inventory"] if item["role"] == "gate_b_decision"
    )
    gate_b_entry["path"] = alternate.name
    _write_json(request_path, request)

    with pytest.raises(CheckViolation, match="Gate B path differs"):
        archive_terminal_milestone(
            request_path=request_path,
            source_root=root,
            gate_b_path=gate_b_path,
            config_path=CONFIG,
            store=RecordingStore(tmp_path / "archive"),
            recorder=ArchiveAttemptRecorder(tmp_path / "attempts"),
            evidence_verifier=FakeDecisionEvidenceVerifier(),
        )


def test_archive_rejects_gate_b_evidence_digest_different_from_inventory(
    tmp_path: Path,
) -> None:
    root, request_path, gate_b_path = _build_bundle(tmp_path)
    changed = b"metric,value\nsimple_regret,0.2\n"
    (root / "final.csv").write_bytes(changed)
    request = json.loads(request_path.read_bytes())
    result_entry = next(
        item for item in request["source_inventory"] if item["role"] == "final_result"
    )
    result_entry["sha256"] = sha256_bytes(changed)
    result_entry["size_bytes"] = len(changed)
    _write_json(request_path, request)

    with pytest.raises(CheckViolation, match="evidence digest differs"):
        archive_terminal_milestone(
            request_path=request_path,
            source_root=root,
            gate_b_path=gate_b_path,
            config_path=CONFIG,
            store=RecordingStore(tmp_path / "archive"),
            recorder=ArchiveAttemptRecorder(tmp_path / "attempts"),
            evidence_verifier=FakeDecisionEvidenceVerifier(),
        )


def test_standalone_archive_cli_publishes_to_local_store(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, request_path, gate_b_path = _build_bundle(tmp_path)
    monkeypatch.setattr(
        "pcdb.governance.cli.GitHubCommentEvidenceVerifier.from_environment",
        FakeDecisionEvidenceVerifier,
    )

    exit_code = main(
        [
            "governance-archive",
            "--request",
            str(request_path),
            "--source-root",
            str(root),
            "--gate-b",
            str(gate_b_path),
            "--config",
            str(CONFIG),
            "--local-store",
            str(tmp_path / "archive"),
            "--attempt-root",
            str(tmp_path / "attempts"),
        ]
    )

    receipt = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert receipt["status"] == "archived"
    assert receipt["check_report"]["approval_events_emitted"] == 0
