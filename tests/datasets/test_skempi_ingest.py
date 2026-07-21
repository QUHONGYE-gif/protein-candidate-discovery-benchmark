import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from pcdb.datasets.skempi import (
    SkempiSourceFile,
    SkempiSourceInventory,
    SourceSizeMismatchError,
    VerifiedSkempiFile,
    build_skempi_dataset_manifest,
    build_skempi_source_inspection,
    download_skempi_source_file,
    load_skempi_source_inventory,
)

REPOSITORY_ROOT = Path(__file__).parents[2]


class FakeResponse(io.BytesIO):
    def __init__(
        self,
        payload: bytes,
        *,
        status: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(payload)
        self.status = status
        self.headers = headers or {}


class InterruptingResponse(FakeResponse):
    def __init__(self, payload: bytes, *, headers: dict[str, str]) -> None:
        super().__init__(payload, status=200, headers=headers)
        self._read_once = False

    def read(self, size: int | None = -1) -> bytes:
        if self._read_once:
            raise OSError("simulated interrupted transfer")
        self._read_once = True
        return super().read(size)


def test_repository_skempi_inventory_pins_two_official_source_objects() -> None:
    inventory = load_skempi_source_inventory(
        REPOSITORY_ROOT / "configs/datasets/skempi/v2.0/2018-06/source-bundle.json"
    )

    assert inventory.dataset_id == "skempi"
    assert inventory.dataset_version == "v2.0-2018-06"
    assert inventory.task_id == "source-bundle"
    assert inventory.license == "CC-BY-4.0"
    assert inventory.publisher_checksum_policy == "not-provided"
    assert inventory.object_prefix == "raw/skempi/v2.0/2018-06/source-bundle"
    assert sum(source.size_bytes for source in inventory.files) == 32_084_298
    assert all(source.http_status == 200 for source in inventory.files)
    assert all(source.etag is None for source in inventory.files)
    assert all(source.last_modified is None for source in inventory.files)
    assert {
        (source.key, source.published_date, source.size_bytes, source.content_type)
        for source in inventory.files
    } == {
        ("skempi_v2.csv", "2018-06-06", 1_602_208, "text/csv; charset=utf-8"),
        (
            "SKEMPI2_PDBs.tgz",
            "2018-06-08",
            30_482_090,
            "application/x-compressed-tar",
        ),
    }


def test_skempi_download_restarts_without_a_stable_validator(tmp_path: Path) -> None:
    payload = b"public SKEMPI fixture"
    source = SkempiSourceFile(
        key="skempi_v2.csv",
        url="https://life.bsc.es/pid/skempi2/database/download/skempi_v2.csv",
        published_date="2018-06-06",
        size_bytes=len(payload),
        content_type="text/csv; charset=utf-8",
    )
    part_path = tmp_path / "staging/incoming/skempi_v2.csv.part"
    part_path.parent.mkdir(parents=True)
    part_path.write_bytes(payload[:7])
    requests: list[Request] = []

    def open_url(request: Request) -> FakeResponse:
        requests.append(request)
        return FakeResponse(payload, status=200)

    verified = download_skempi_source_file(source, tmp_path / "staging", open_url=open_url)

    assert requests[0].get_header("Range") is None
    assert requests[0].get_header("If-range") is None
    assert verified.path.read_bytes() == payload
    assert verified.sha256 == hashlib.sha256(payload).hexdigest()
    assert verified.size_bytes == len(payload)
    assert not part_path.exists()


def test_skempi_download_resumes_with_matching_strong_validator(tmp_path: Path) -> None:
    payload = b"public SKEMPI fixture"
    etag = '"stable-source-v1"'
    source = SkempiSourceFile(
        key="skempi_v2.csv",
        url="https://life.bsc.es/pid/skempi2/database/download/skempi_v2.csv",
        published_date="2018-06-06",
        size_bytes=len(payload),
        content_type="text/csv; charset=utf-8",
        etag=etag,
    )
    requests: list[Request] = []
    responses = [
        InterruptingResponse(payload[:7], headers={"ETag": etag}),
        FakeResponse(
            payload[7:],
            status=206,
            headers={
                "Content-Range": f"bytes 7-{len(payload) - 1}/{len(payload)}",
                "ETag": etag,
            },
        ),
    ]

    def open_url(request: Request) -> FakeResponse:
        requests.append(request)
        return responses.pop(0)

    with pytest.raises(OSError, match="interrupted transfer"):
        download_skempi_source_file(source, tmp_path / "staging", open_url=open_url)

    verified = download_skempi_source_file(source, tmp_path / "staging", open_url=open_url)

    assert requests[1].get_header("Range") == "bytes=7-"
    assert requests[1].get_header("If-range") == etag
    assert verified.path.read_bytes() == payload
    assert verified.sha256 == hashlib.sha256(payload).hexdigest()


def test_skempi_resume_rejects_missing_content_range(tmp_path: Path) -> None:
    payload = b"public SKEMPI fixture"
    etag = '"stable-source-v1"'
    source = SkempiSourceFile(
        key="skempi_v2.csv",
        url="https://life.bsc.es/pid/skempi2/database/download/skempi_v2.csv",
        published_date="2018-06-06",
        size_bytes=len(payload),
        content_type="text/csv; charset=utf-8",
        etag=etag,
    )
    responses = [
        InterruptingResponse(payload[:7], headers={"ETag": etag}),
        FakeResponse(payload[7:], status=206, headers={"ETag": etag}),
    ]

    def open_url(_request: Request) -> FakeResponse:
        return responses.pop(0)

    with pytest.raises(OSError, match="interrupted transfer"):
        download_skempi_source_file(source, tmp_path / "staging", open_url=open_url)
    with pytest.raises(ValueError, match="Content-Range"):
        download_skempi_source_file(source, tmp_path / "staging", open_url=open_url)

    assert not (tmp_path / "staging/verified/skempi_v2.csv").exists()
    assert not (tmp_path / "staging/incoming/skempi_v2.csv.part").exists()


def test_skempi_resume_rejects_changed_validator(tmp_path: Path) -> None:
    payload = b"public SKEMPI fixture"
    etag = '"stable-source-v1"'
    source = SkempiSourceFile(
        key="skempi_v2.csv",
        url="https://life.bsc.es/pid/skempi2/database/download/skempi_v2.csv",
        published_date="2018-06-06",
        size_bytes=len(payload),
        content_type="text/csv; charset=utf-8",
        etag=etag,
    )
    responses = [
        InterruptingResponse(payload[:7], headers={"ETag": etag}),
        FakeResponse(
            payload[7:],
            status=206,
            headers={
                "Content-Range": f"bytes 7-{len(payload) - 1}/{len(payload)}",
                "ETag": '"changed-source-v2"',
            },
        ),
    ]

    def open_url(_request: Request) -> FakeResponse:
        return responses.pop(0)

    with pytest.raises(OSError, match="interrupted transfer"):
        download_skempi_source_file(source, tmp_path / "staging", open_url=open_url)
    with pytest.raises(ValueError, match="validator"):
        download_skempi_source_file(source, tmp_path / "staging", open_url=open_url)

    assert not (tmp_path / "staging/verified/skempi_v2.csv").exists()
    assert not (tmp_path / "staging/incoming/skempi_v2.csv.part").exists()


def test_skempi_resume_rejects_wrong_content_range(tmp_path: Path) -> None:
    payload = b"public SKEMPI fixture"
    etag = '"stable-source-v1"'
    source = SkempiSourceFile(
        key="skempi_v2.csv",
        url="https://life.bsc.es/pid/skempi2/database/download/skempi_v2.csv",
        published_date="2018-06-06",
        size_bytes=len(payload),
        content_type="text/csv; charset=utf-8",
        etag=etag,
    )
    responses = [
        InterruptingResponse(payload[:7], headers={"ETag": etag}),
        FakeResponse(
            payload[7:],
            status=206,
            headers={
                "Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}",
                "ETag": etag,
            },
        ),
    ]

    def open_url(_request: Request) -> FakeResponse:
        return responses.pop(0)

    with pytest.raises(OSError, match="interrupted transfer"):
        download_skempi_source_file(source, tmp_path / "staging", open_url=open_url)
    with pytest.raises(ValueError, match="Content-Range"):
        download_skempi_source_file(source, tmp_path / "staging", open_url=open_url)

    assert not (tmp_path / "staging/verified/skempi_v2.csv").exists()
    assert not (tmp_path / "staging/incoming/skempi_v2.csv.part").exists()


def test_skempi_resume_restarts_when_server_ignores_range(tmp_path: Path) -> None:
    payload = b"public SKEMPI fixture"
    etag = '"stable-source-v1"'
    source = SkempiSourceFile(
        key="skempi_v2.csv",
        url="https://life.bsc.es/pid/skempi2/database/download/skempi_v2.csv",
        published_date="2018-06-06",
        size_bytes=len(payload),
        content_type="text/csv; charset=utf-8",
        etag=etag,
    )
    requests: list[Request] = []
    responses = [
        InterruptingResponse(payload[:7], headers={"ETag": etag}),
        FakeResponse(payload, status=200, headers={"ETag": etag}),
    ]

    def open_url(request: Request) -> FakeResponse:
        requests.append(request)
        return responses.pop(0)

    with pytest.raises(OSError, match="interrupted transfer"):
        download_skempi_source_file(source, tmp_path / "staging", open_url=open_url)
    verified = download_skempi_source_file(source, tmp_path / "staging", open_url=open_url)

    assert requests[1].get_header("Range") == "bytes=7-"
    assert requests[1].get_header("If-range") == etag
    assert verified.path.read_bytes() == payload
    assert verified.sha256 == hashlib.sha256(payload).hexdigest()


def test_skempi_size_mismatch_never_reaches_verified_directory(tmp_path: Path) -> None:
    source = SkempiSourceFile(
        key="skempi_v2.csv",
        url="https://life.bsc.es/pid/skempi2/database/download/skempi_v2.csv",
        published_date="2018-06-06",
        size_bytes=10,
        content_type="text/csv; charset=utf-8",
    )

    def open_url(_request: Request) -> FakeResponse:
        return FakeResponse(b"short", status=200)

    with pytest.raises(SourceSizeMismatchError):
        download_skempi_source_file(source, tmp_path / "staging", open_url=open_url)

    assert not (tmp_path / "staging/verified/skempi_v2.csv").exists()


def test_skempi_source_inspection_records_table_and_archive_inventory(tmp_path: Path) -> None:
    csv_path = tmp_path / "skempi_v2.csv"
    csv_path.write_text("#Pdb;Mutation(s)_PDB\n1ABC;AA1V\n2DEF;GB2A\n", encoding="utf-8")
    archive_path = tmp_path / "SKEMPI2_PDBs.tgz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, payload in [
            ("PDBs/._1ABC.pdb", b"metadata"),
            ("PDBs/1ABC.mapping", b"A:A"),
            ("PDBs/1ABC.pdb", b"ATOM\n"),
            ("PDBs/2DEF.pdb", b"HEADER\n"),
        ]:
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    csv_source = SkempiSourceFile(
        key="skempi_v2.csv",
        url="https://life.bsc.es/pid/skempi2/database/download/skempi_v2.csv",
        published_date="2018-06-06",
        size_bytes=csv_path.stat().st_size,
        content_type="text/csv; charset=utf-8",
    )
    archive_source = SkempiSourceFile(
        key="SKEMPI2_PDBs.tgz",
        url="https://life.bsc.es/pid/skempi2/database/download/SKEMPI2_PDBs.tgz",
        published_date="2018-06-08",
        size_bytes=archive_path.stat().st_size,
        content_type="application/x-compressed-tar",
    )
    inventory = SkempiSourceInventory(
        dataset_id="skempi",
        dataset_version="v2.0-2018-06",
        task_id="source-bundle",
        source_url="https://life.bsc.es/pid/skempi2/database/index",
        doi="10.1093/bioinformatics/bty635",
        license="CC-BY-4.0",
        license_url="https://life.bsc.es/pid/skempi2/info/terms",
        attribution="SKEMPI 2.0",
        publisher_checksum_policy="not-provided",
        http_metadata_observed_at="2026-07-21T02:39:21Z",
        object_prefix="raw/skempi/v2.0/2018-06/source-bundle",
        known_limitations=("Raw ingest only.",),
        files=(csv_source, archive_source),
    )
    verified = [
        VerifiedSkempiFile(
            source=csv_source,
            path=csv_path,
            sha256=hashlib.sha256(csv_path.read_bytes()).hexdigest(),
            size_bytes=csv_path.stat().st_size,
        ),
        VerifiedSkempiFile(
            source=archive_source,
            path=archive_path,
            sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            size_bytes=archive_path.stat().st_size,
        ),
    ]

    inspection = build_skempi_source_inspection(inventory, verified)

    assert inspection["table"] == {
        "object_key": "raw/skempi/v2.0/2018-06/source-bundle/skempi_v2.csv",
        "sha256": verified[0].sha256,
        "delimiter": ";",
        "header": ["#Pdb", "Mutation(s)_PDB"],
        "row_count": 2,
    }
    assert inspection["structure_archive"] == {
        "object_key": "raw/skempi/v2.0/2018-06/source-bundle/SKEMPI2_PDBs.tgz",
        "sha256": verified[1].sha256,
        "regular_file_count": 4,
        "apple_double_member_count": 1,
        "pdb_payload_member_count": 2,
        "mapping_payload_member_count": 1,
        "members": [
            {"path": "PDBs/._1ABC.pdb", "size_bytes": 8},
            {"path": "PDBs/1ABC.mapping", "size_bytes": 3},
            {"path": "PDBs/1ABC.pdb", "size_bytes": 5},
            {"path": "PDBs/2DEF.pdb", "size_bytes": 7},
        ],
    }
    assert inspection["observed_limitations"] == [
        "The publisher archive contains 1 AppleDouble metadata member(s); they are preserved "
        "in the raw object and deterministic member inventory but excluded from PDB and mapping "
        "payload counts."
    ]


def test_skempi_source_inspection_rejects_unsafe_archive_members(tmp_path: Path) -> None:
    csv_path = tmp_path / "skempi_v2.csv"
    csv_path.write_text("#Pdb;Mutation(s)_PDB\n1ABC;AA1V\n", encoding="utf-8")
    archive_path = tmp_path / "SKEMPI2_PDBs.tgz"
    with tarfile.open(archive_path, "w:gz") as archive:
        payload = b"ATOM\n"
        member = tarfile.TarInfo("../escape.pdb")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    inventory = load_skempi_source_inventory(
        REPOSITORY_ROOT / "configs/datasets/skempi/v2.0/2018-06/source-bundle.json"
    )
    verified = [
        VerifiedSkempiFile(
            source=inventory.files[0],
            path=csv_path,
            sha256=hashlib.sha256(csv_path.read_bytes()).hexdigest(),
            size_bytes=csv_path.stat().st_size,
        ),
        VerifiedSkempiFile(
            source=inventory.files[1],
            path=archive_path,
            sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            size_bytes=archive_path.stat().st_size,
        ),
    ]

    with pytest.raises(ValueError, match="unsafe member path"):
        build_skempi_source_inspection(inventory, verified)


def test_skempi_dataset_manifest_matches_repository_schema(tmp_path: Path) -> None:
    inventory = load_skempi_source_inventory(
        REPOSITORY_ROOT / "configs/datasets/skempi/v2.0/2018-06/source-bundle.json"
    )
    verified = [
        VerifiedSkempiFile(
            source=source,
            path=tmp_path / source.key,
            sha256=hashlib.sha256(source.key.encode()).hexdigest(),
            size_bytes=source.size_bytes,
        )
        for source in inventory.files
    ]

    manifest = build_skempi_dataset_manifest(
        inventory,
        verified,
        downloaded_at=datetime(2026, 7, 21, 2, 45, tzinfo=UTC),
        observed_limitations=("Observed source-package limitation.",),
    )
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/dataset-manifest.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    assert manifest["source_url"] == "https://life.bsc.es/pid/skempi2/database/index"
    assert manifest["doi"] == "10.1093/bioinformatics/bty635"
    known_limitations = manifest["known_limitations"]
    assert isinstance(known_limitations, list)
    assert known_limitations[-1] == "Observed source-package limitation."
    assert manifest["raw_files"] == [
        {
            "object_key": f"raw/skempi/v2.0/2018-06/source-bundle/{source.key}",
            "sha256": item.sha256,
            "size_bytes": source.size_bytes,
        }
        for source, item in zip(inventory.files, verified, strict=True)
    ]


def test_repository_skempi_evidence_is_frozen_and_internally_consistent() -> None:
    evidence_root = REPOSITORY_ROOT / "manifests/datasets/skempi/v2.0/2018-06/source-bundle"
    manifest = json.loads((evidence_root / "dataset-manifest.json").read_text(encoding="utf-8"))
    inspection = json.loads((evidence_root / "source-inspection.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/dataset-manifest.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    assert manifest["raw_files"] == [
        {
            "object_key": "raw/skempi/v2.0/2018-06/source-bundle/skempi_v2.csv",
            "sha256": "76f8e60d09eaa4f05fb265063886859a86895c0b2fb225e31fe82928a883972d",
            "size_bytes": 1_602_208,
        },
        {
            "object_key": "raw/skempi/v2.0/2018-06/source-bundle/SKEMPI2_PDBs.tgz",
            "sha256": "82418131a3864aa8484ea6ec300e58f9bc0349e21530f83f7ce336b41589a95a",
            "size_bytes": 30_482_090,
        },
    ]
    assert inspection["table"]["row_count"] == 7_085
    assert len(inspection["table"]["header"]) == 29

    archive = inspection["structure_archive"]
    members = archive["members"]
    assert [member["path"] for member in members] == sorted(member["path"] for member in members)
    assert archive["regular_file_count"] == len(members) == 1_381
    assert archive["apple_double_member_count"] == 691
    assert archive["pdb_payload_member_count"] == 345
    assert archive["mapping_payload_member_count"] == 345
    assert inspection["observed_limitations"][-1] == manifest["known_limitations"][-1]


def test_tracked_skempi_ingest_attempt_is_successful_and_checksum_bound() -> None:
    inventory_path = REPOSITORY_ROOT / "configs/datasets/skempi/v2.0/2018-06/source-bundle.json"
    evidence_root = REPOSITORY_ROOT / "manifests/datasets/skempi/v2.0/2018-06/source-bundle"
    manifest_path = evidence_root / "dataset-manifest.json"
    attempt_path = evidence_root / "ingest-attempts/skempi-v2-20260721T025812Z.json"
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (REPOSITORY_ROOT / "schemas/dataset-ingest-attempt.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(attempt)
    assert attempt["status"] == "succeeded"
    assert attempt["raw_ingest_git_commit"] == ("022efc65b4a4d08f4656c3bd950a3695f300c1a1")
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
        "publisher_md5_and_size_verified": False,
        "cos_readback_sha256_and_size_verified": True,
        "manifest_schema_verified": True,
        "manifest_exact_bytes_verified": True,
        "duplicate_write_rejected": True,
        "duplicate_write_status": "409 FileAlreadyExists",
        "raw_object_count": 2,
    }
