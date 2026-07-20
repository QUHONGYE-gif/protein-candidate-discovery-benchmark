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
    assert manifest["license"] == inventory.license
    assert {item["object_key"] for item in manifest["raw_files"]} == {
        f"{inventory.object_prefix}/{source.key}" for source in inventory.files
    }
