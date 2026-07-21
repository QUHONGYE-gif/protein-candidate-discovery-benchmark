from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from pcdb.datasets.ingest import (
    ChecksumMismatchError,
    build_dataset_manifest,
    download_source_file,
    load_source_inventory,
)

REPOSITORY_ROOT = Path(__file__).parents[2]


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, *, status: int) -> None:
        super().__init__(payload)
        self.status = status


def _inventory_payload(payload: bytes) -> dict[str, object]:
    return {
        "inventory_version": "1",
        "dataset_id": "flip2",
        "dataset_version": "zenodo-18433203-v3",
        "task_id": "amylase",
        "source_url": "https://zenodo.org/records/18433203",
        "doi": "10.5281/zenodo.18433203",
        "license": "MIT",
        "object_prefix": "raw/flip2/zenodo-18433203-v3",
        "known_limitations": ["Raw ingest only; processing and split audits are pending."],
        "files": [
            {
                "key": "amylase/example.csv.gz",
                "url": (
                    "https://zenodo.org/api/records/18433203/files/amylase/example.csv.gz/content"
                ),
                "md5": hashlib.md5(payload, usedforsecurity=False).hexdigest(),
                "size_bytes": len(payload),
            }
        ],
    }


def _write_inventory(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("key", "url"),
    [
        ("../escape.csv.gz", "https://zenodo.org/api/records/18433203/files/x/content"),
        ("/absolute.csv.gz", "https://zenodo.org/api/records/18433203/files/x/content"),
        ("amylase/x.csv.gz", "http://zenodo.org/api/records/18433203/files/x/content"),
        ("amylase/x.csv.gz", "https://mirror.invalid/x.csv.gz"),
    ],
)
def test_inventory_rejects_unsafe_or_noncanonical_sources(
    tmp_path: Path, key: str, url: str
) -> None:
    payload = _inventory_payload(b"public fixture")
    files = payload["files"]
    assert isinstance(files, list)
    files[0] = {**files[0], "key": key, "url": url}

    with pytest.raises(ValueError):
        load_source_inventory(_write_inventory(tmp_path, payload))


def test_download_resumes_then_publishes_verified_file_atomically(tmp_path: Path) -> None:
    payload = b"public benchmark fixture"
    inventory = load_source_inventory(_write_inventory(tmp_path, _inventory_payload(payload)))
    source = inventory.files[0]
    part_path = tmp_path / "staging/incoming/amylase/example.csv.gz.part"
    part_path.parent.mkdir(parents=True)
    part_path.write_bytes(payload[:7])
    requests: list[Request] = []

    def open_url(request: Request) -> FakeResponse:
        requests.append(request)
        return FakeResponse(payload[7:], status=206)

    verified = download_source_file(source, tmp_path / "staging", open_url=open_url)

    assert requests[0].get_header("Range") == "bytes=7-"
    assert verified.path.read_bytes() == payload
    assert verified.sha256 == hashlib.sha256(payload).hexdigest()
    assert verified.size_bytes == len(payload)
    assert not part_path.exists()


def test_complete_part_is_verified_without_another_network_request(tmp_path: Path) -> None:
    payload = b"public benchmark fixture"
    inventory = load_source_inventory(_write_inventory(tmp_path, _inventory_payload(payload)))
    part_path = tmp_path / "staging/incoming/amylase/example.csv.gz.part"
    part_path.parent.mkdir(parents=True)
    part_path.write_bytes(payload)

    def open_url(_request: Request) -> FakeResponse:
        raise AssertionError("a complete part must not be downloaded again")

    verified = download_source_file(inventory.files[0], tmp_path / "staging", open_url=open_url)

    assert verified.path.read_bytes() == payload
    assert not part_path.exists()


def test_download_checksum_failure_never_reaches_verified_directory(tmp_path: Path) -> None:
    expected = b"expected public fixture"
    inventory = load_source_inventory(_write_inventory(tmp_path, _inventory_payload(expected)))

    def open_url(_request: Request) -> FakeResponse:
        return FakeResponse(b"corrupted", status=200)

    with pytest.raises(ChecksumMismatchError):
        download_source_file(inventory.files[0], tmp_path / "staging", open_url=open_url)

    assert not (tmp_path / "staging/verified/amylase/example.csv.gz").exists()


def test_generated_dataset_manifest_matches_repository_schema(tmp_path: Path) -> None:
    payload = b"public benchmark fixture"
    inventory = load_source_inventory(_write_inventory(tmp_path, _inventory_payload(payload)))

    def open_url(_request: Request) -> FakeResponse:
        return FakeResponse(payload, status=200)

    verified = download_source_file(inventory.files[0], tmp_path / "staging", open_url=open_url)
    manifest = build_dataset_manifest(
        inventory,
        [verified],
        downloaded_at=datetime(2026, 7, 20, 5, 0, tzinfo=UTC),
    )
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/dataset-manifest.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    assert manifest["doi"] == inventory.doi
    assert manifest["raw_files"] == [
        {
            "object_key": "raw/flip2/zenodo-18433203-v3/amylase/example.csv.gz",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
    ]


def test_repository_amylase_inventory_is_pinned_to_six_official_files() -> None:
    inventory = load_source_inventory(
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/amylase.json"
    )

    assert inventory.dataset_version == "zenodo-18433203-v3"
    assert inventory.license == "MIT"
    assert {source.key for source in inventory.files} == {
        "amylase/README.md",
        "amylase/by_mutation.csv.gz",
        "amylase/close_to_far.csv.gz",
        "amylase/far_to_close.csv.gz",
        "amylase/one_to_many.csv.gz",
        "amylase/random_split.csv.gz",
    }


def test_repository_ired_inventory_is_pinned_to_three_official_files() -> None:
    inventory = load_source_inventory(
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/ired.json"
    )

    assert inventory.dataset_version == "zenodo-18433203-v3"
    assert inventory.task_id == "ired"
    assert inventory.license == "CC-BY-4.0"
    assert sum(source.size_bytes for source in inventory.files) == 390_113
    assert {(source.key, source.md5, source.size_bytes) for source in inventory.files} == {
        ("ired/README.md", "f8461790121caa6a2fcefcc5f20d1f6f", 632),
        ("ired/random.csv.gz", "9f26de3ed0cc925d77604c07784debb1", 194_747),
        ("ired/two_to_many.csv.gz", "f514fa09bb8ba5fd092d9417ed8642d9", 194_734),
    }


def test_repository_nucb_inventory_is_pinned_to_three_official_files() -> None:
    inventory = load_source_inventory(
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/nucb.json"
    )

    assert inventory.dataset_version == "zenodo-18433203-v3"
    assert inventory.task_id == "nucb"
    assert inventory.license == "CC-BY-4.0"
    assert sum(source.size_bytes for source in inventory.files) == 1_255_423
    assert {(source.key, source.md5, source.size_bytes) for source in inventory.files} == {
        ("nucb/README.md", "6f6cef8efcdf701244c47830722f86b0", 733),
        ("nucb/random.csv.gz", "8b8f333d2c96fe6f92d7ebd692d684d8", 629_753),
        ("nucb/two_to_many.csv.gz", "cdedaadd57f2b148950b357e9f24c238", 624_937),
    }


def test_repository_hydro_inventory_is_pinned_to_seven_official_files() -> None:
    inventory = load_source_inventory(
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/hydro.json"
    )

    assert inventory.dataset_version == "zenodo-18433203-v3"
    assert inventory.task_id == "hydro"
    assert inventory.license == "CC-BY-4.0"
    assert sum(source.size_bytes for source in inventory.files) == 2_197_163
    assert {(source.key, source.md5, source.size_bytes) for source in inventory.files} == {
        ("hydro/README.md", "eb762cd877939c4cee891bdc1c159650", 430),
        ("hydro/random_split.csv.gz", "1007879548f6f414387ef474922308d9", 376_954),
        ("hydro/three_to_many.csv.gz", "a13b01d71faf2e76dd91dc57b7311972", 369_389),
        ("hydro/low_to_high.csv.gz", "fb9cfaef56791b90f2ddd154b90da8ed", 372_137),
        ("hydro/to_P06241.csv.gz", "1b860081da084196b276804b573283cf", 357_257),
        ("hydro/to_P0A9X9.csv.gz", "64d1690065aeb0ad90cd1e1b5dd7a581", 359_737),
        ("hydro/to_P01053.csv.gz", "5b7ade3bb837d2d8ade1e75f659d77f7", 361_259),
    }


def test_tracked_amylase_manifest_matches_schema_and_inventory() -> None:
    inventory = load_source_inventory(
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/amylase.json"
    )
    manifest = json.loads(
        (
            REPOSITORY_ROOT
            / "manifests/datasets/flip2/zenodo-18433203-v3/amylase/dataset-manifest.json"
        ).read_text(encoding="utf-8")
    )
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/dataset-manifest.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    assert manifest["dataset_id"] == inventory.dataset_id
    assert manifest["dataset_version"] == inventory.dataset_version
    assert manifest["task_id"] == inventory.task_id
    assert manifest["doi"] == inventory.doi
    assert manifest["license"] == inventory.license
    assert {item["object_key"] for item in manifest["raw_files"]} == {
        f"{inventory.object_prefix}/{source.key}" for source in inventory.files
    }


def test_tracked_ired_manifest_matches_schema_and_inventory() -> None:
    inventory = load_source_inventory(
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/ired.json"
    )
    manifest = json.loads(
        (
            REPOSITORY_ROOT
            / "manifests/datasets/flip2/zenodo-18433203-v3/ired/dataset-manifest.json"
        ).read_text(encoding="utf-8")
    )
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/dataset-manifest.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    assert manifest["dataset_id"] == inventory.dataset_id
    assert manifest["dataset_version"] == inventory.dataset_version
    assert manifest["task_id"] == inventory.task_id
    assert manifest["doi"] == inventory.doi
    assert manifest["license"] == inventory.license
    assert {item["object_key"] for item in manifest["raw_files"]} == {
        f"{inventory.object_prefix}/{source.key}" for source in inventory.files
    }
    assert {item["object_key"]: item["size_bytes"] for item in manifest["raw_files"]} == {
        f"{inventory.object_prefix}/{source.key}": source.size_bytes for source in inventory.files
    }


def test_tracked_nucb_manifest_matches_schema_and_inventory() -> None:
    inventory = load_source_inventory(
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/nucb.json"
    )
    manifest = json.loads(
        (
            REPOSITORY_ROOT
            / "manifests/datasets/flip2/zenodo-18433203-v3/nucb/dataset-manifest.json"
        ).read_text(encoding="utf-8")
    )
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/dataset-manifest.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    assert manifest["task_id"] == inventory.task_id
    assert manifest["license"] == inventory.license
    assert manifest["raw_files"] == [
        {
            "object_key": "raw/flip2/zenodo-18433203-v3/nucb/README.md",
            "sha256": "4490bac832fc374a94f2e679d0e1a917330a9361587ed347b9cd2bab9a0ada3f",
            "size_bytes": 733,
        },
        {
            "object_key": "raw/flip2/zenodo-18433203-v3/nucb/random.csv.gz",
            "sha256": "ecb3a205f82c1127c136382b8c3456c7206be951c655a2850dd7eecd961b3e9f",
            "size_bytes": 629_753,
        },
        {
            "object_key": "raw/flip2/zenodo-18433203-v3/nucb/two_to_many.csv.gz",
            "sha256": "9079cc5e2f880ecebfc51ecb8ec1e063b807a58e36ece4b739f98533ab9f1d96",
            "size_bytes": 624_937,
        },
    ]


def test_tracked_hydro_manifest_matches_schema_and_inventory() -> None:
    inventory = load_source_inventory(
        REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/hydro.json"
    )
    manifest = json.loads(
        (
            REPOSITORY_ROOT
            / "manifests/datasets/flip2/zenodo-18433203-v3/hydro/dataset-manifest.json"
        ).read_text(encoding="utf-8")
    )
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/dataset-manifest.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    assert manifest["task_id"] == inventory.task_id
    assert manifest["license"] == inventory.license
    assert manifest["raw_files"] == [
        {
            "object_key": "raw/flip2/zenodo-18433203-v3/hydro/README.md",
            "sha256": "274e7a53f92088cb156bf9b2a8c4397c89b477884fa63963ed2770de2cdaf406",
            "size_bytes": 430,
        },
        {
            "object_key": "raw/flip2/zenodo-18433203-v3/hydro/random_split.csv.gz",
            "sha256": "9f84a5139f97fda454e059a6b22cdd7ade7aaba698f66f7ac6104747b204aee6",
            "size_bytes": 376_954,
        },
        {
            "object_key": "raw/flip2/zenodo-18433203-v3/hydro/three_to_many.csv.gz",
            "sha256": "76befc506e84e0ef0dd96fa4c8e5b41c6ec47e1664eccbf8804862f3ea97cb90",
            "size_bytes": 369_389,
        },
        {
            "object_key": "raw/flip2/zenodo-18433203-v3/hydro/low_to_high.csv.gz",
            "sha256": "30b89e3102fe67c1726ebc897a127221c89e224801d60ede509d1ca8dc7d612f",
            "size_bytes": 372_137,
        },
        {
            "object_key": "raw/flip2/zenodo-18433203-v3/hydro/to_P06241.csv.gz",
            "sha256": "7b7cb9f0116e851b37c45f2cfaa826ebb5f98ef5b2012cf6f95b36d42a4926c9",
            "size_bytes": 357_257,
        },
        {
            "object_key": "raw/flip2/zenodo-18433203-v3/hydro/to_P0A9X9.csv.gz",
            "sha256": "0eb15476975373a13b6e8faa44f71168d347ec364bd410beff9b9b929f6ae0f7",
            "size_bytes": 359_737,
        },
        {
            "object_key": "raw/flip2/zenodo-18433203-v3/hydro/to_P01053.csv.gz",
            "sha256": "99c116848319fcbe56f5fad654bc3c51fbc8c114b2d350425a60a811de43141c",
            "size_bytes": 361_259,
        },
    ]


def test_tracked_amylase_ingest_attempt_is_successful_and_checksum_bound() -> None:
    inventory_path = REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/amylase.json"
    manifest_path = (
        REPOSITORY_ROOT
        / "manifests/datasets/flip2/zenodo-18433203-v3/amylase/dataset-manifest.json"
    )
    attempt_path = (
        REPOSITORY_ROOT
        / "manifests/datasets/flip2/zenodo-18433203-v3/amylase/ingest-attempts"
        / "flip2-amylase-20260720T072736Z.json"
    )
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/dataset-ingest-attempt.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(attempt)
    assert attempt["status"] == "succeeded"
    assert attempt["raw_ingest_git_commit"] == "c0c30da025a6ba2654c267f3919652b21f29db57"
    assert attempt["manifest_revision_git_commit"] == "d77b2d6a95915910deb5d1a6fd0ccf65ee032b49"
    assert (
        attempt["source_inventory"]["sha256"]
        == hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    )
    assert (
        attempt["dataset_manifest"]["sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert attempt["dataset_manifest"]["object_key"].endswith("dataset-manifest.v2.json")
    assert attempt["dataset_manifest"]["supersedes_sha256"] == (
        "657111a8517c8cbc9a17a5ac98964af77839d4fee8efee16438464c9d8347ce9"
    )
    assert attempt["raw_objects"] == manifest["raw_files"]
    assert attempt["verification"] == {
        "publisher_md5_and_size_verified": True,
        "cos_readback_sha256_and_size_verified": True,
        "manifest_schema_verified": True,
        "manifest_exact_bytes_verified": True,
        "duplicate_write_rejected": True,
        "duplicate_write_status": "409 FileAlreadyExists",
        "raw_object_count": len(manifest["raw_files"]),
    }


def test_tracked_ired_ingest_attempt_is_successful_and_checksum_bound() -> None:
    inventory_path = REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/ired.json"
    manifest_path = (
        REPOSITORY_ROOT / "manifests/datasets/flip2/zenodo-18433203-v3/ired/dataset-manifest.json"
    )
    attempt_path = (
        REPOSITORY_ROOT
        / "manifests/datasets/flip2/zenodo-18433203-v3/ired/ingest-attempts"
        / "flip2-ired-20260720T134110Z.json"
    )
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/dataset-ingest-attempt.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(attempt)
    assert attempt["status"] == "succeeded"
    assert attempt["raw_ingest_git_commit"] == "da8a8bd011b7cbb2f52fc7f02bae6a31fbb38a05"
    assert attempt["manifest_revision_git_commit"] == attempt["raw_ingest_git_commit"]
    assert (
        attempt["source_inventory"]["sha256"]
        == hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    )
    assert (
        attempt["dataset_manifest"]["sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert attempt["dataset_manifest"]["revision"] == 1
    assert attempt["dataset_manifest"]["object_key"].endswith("dataset-manifest.json")
    assert attempt["raw_objects"] == manifest["raw_files"]
    assert attempt["verification"] == {
        "publisher_md5_and_size_verified": True,
        "cos_readback_sha256_and_size_verified": True,
        "manifest_schema_verified": True,
        "manifest_exact_bytes_verified": True,
        "duplicate_write_rejected": True,
        "duplicate_write_status": "409 FileAlreadyExists",
        "raw_object_count": len(manifest["raw_files"]),
    }


def test_tracked_nucb_ingest_attempt_is_successful_and_checksum_bound() -> None:
    inventory_path = REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/nucb.json"
    manifest_path = (
        REPOSITORY_ROOT / "manifests/datasets/flip2/zenodo-18433203-v3/nucb/dataset-manifest.json"
    )
    attempt_path = (
        REPOSITORY_ROOT
        / "manifests/datasets/flip2/zenodo-18433203-v3/nucb/ingest-attempts"
        / "flip2-nucb-20260721T021708Z.json"
    )
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/dataset-ingest-attempt.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(attempt)
    assert attempt["status"] == "succeeded"
    assert attempt["raw_ingest_git_commit"] == "528f3eca87ae462cc8cba537cadf67a2deb34282"
    assert attempt["manifest_revision_git_commit"] == attempt["raw_ingest_git_commit"]
    assert (
        attempt["source_inventory"]["sha256"]
        == hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    )
    assert (
        attempt["dataset_manifest"]["sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert attempt["raw_objects"] == manifest["raw_files"]
    assert attempt["verification"] == {
        "publisher_md5_and_size_verified": True,
        "cos_readback_sha256_and_size_verified": True,
        "manifest_schema_verified": True,
        "manifest_exact_bytes_verified": True,
        "duplicate_write_rejected": True,
        "duplicate_write_status": "409 FileAlreadyExists",
        "raw_object_count": 3,
    }


def test_tracked_hydro_ingest_attempt_is_successful_and_checksum_bound() -> None:
    inventory_path = REPOSITORY_ROOT / "configs/datasets/flip2/zenodo-18433203-v3/hydro.json"
    manifest_path = (
        REPOSITORY_ROOT / "manifests/datasets/flip2/zenodo-18433203-v3/hydro/dataset-manifest.json"
    )
    attempt_path = (
        REPOSITORY_ROOT
        / "manifests/datasets/flip2/zenodo-18433203-v3/hydro/ingest-attempts"
        / "flip2-hydro-20260721T021708Z.json"
    )
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/dataset-ingest-attempt.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(attempt)
    assert attempt["status"] == "succeeded"
    assert attempt["raw_ingest_git_commit"] == "528f3eca87ae462cc8cba537cadf67a2deb34282"
    assert attempt["manifest_revision_git_commit"] == attempt["raw_ingest_git_commit"]
    assert (
        attempt["source_inventory"]["sha256"]
        == hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    )
    assert (
        attempt["dataset_manifest"]["sha256"]
        == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert attempt["raw_objects"] == manifest["raw_files"]
    assert attempt["verification"] == {
        "publisher_md5_and_size_verified": True,
        "cos_readback_sha256_and_size_verified": True,
        "manifest_schema_verified": True,
        "manifest_exact_bytes_verified": True,
        "duplicate_write_rejected": True,
        "duplicate_write_status": "409 FileAlreadyExists",
        "raw_object_count": 7,
    }
