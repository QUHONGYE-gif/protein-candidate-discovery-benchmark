from __future__ import annotations

import gzip
import hashlib
import io
import json
import subprocess
import zipfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, cast
from urllib.request import Request

import jsonschema
import pytest

from pcdb.datasets.proteingym import (
    APPROVAL,
    AcquiredMember,
    Archive,
    Assay,
    Caps,
    CoscliFilePublisher,
    Member,
    ProteinGymConfig,
    ProteinGymError,
    RangeReader,
    acquire_member,
    build_dataset_manifest,
    build_ingest_attempt,
    load_config,
    process_assay,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = (
    REPOSITORY_ROOT / "configs" / "datasets" / "proteingym" / "v1.3" / "four-assay-unified-v1.json"
)


class _Response(io.BytesIO):
    status = 206

    def __init__(self, payload: bytes, headers: dict[str, str]) -> None:
        super().__init__(payload)
        self.headers = headers

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _zip_fixture(payload: bytes, member_name: str) -> tuple[bytes, Member]:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(f"nested/{member_name}", payload)
        info = bundle.getinfo(f"nested/{member_name}")
    return archive.getvalue(), Member(
        archive_id="fixture",
        member_name=member_name,
        local_header_offset=info.header_offset,
        compressed_size=info.compress_size,
        uncompressed_size=info.file_size,
        crc32=info.CRC,
    )


def _range_opener(
    archive: bytes, identity: Archive
) -> Callable[[Request], AbstractContextManager[BinaryIO]]:
    def open_url(request: Request) -> AbstractContextManager[BinaryIO]:
        range_header = request.get_header("Range")
        assert range_header is not None
        start_text, end_text = range_header.removeprefix("bytes=").split("-")
        start = int(start_text)
        end = int(end_text)
        payload = archive[start : end + 1]
        return cast(
            AbstractContextManager[BinaryIO],
            _Response(
                payload,
                {
                    "Content-Range": f"bytes {start}-{end}/{len(archive)}",
                    "Content-Length": str(len(payload)),
                    "ETag": identity.etag,
                    "Last-Modified": identity.last_modified,
                },
            ),
        )

    return open_url


def _test_config(assay: Assay) -> ProteinGymConfig:
    return ProteinGymConfig(
        dataset_id="proteingym",
        dataset_version="v1.3",
        task_id="four-assay-substitutions",
        source_url="https://github.com/OATML-Markslab/ProteinGym",
        doi="10.5281/zenodo.15293562",
        license="bounded private research use",
        license_boundary="no public raw redistribution",
        object_prefix="proteingym/v1.3/test",
        approval=APPROVAL,
        caps=Caps(10_000, 10_000, 10_000, 10_000, 10_000, 10_000),
        archives=(),
        assays=(assay,),
    )


def _assay(expected_truth_rows: int | None = None) -> Assay:
    truth = Member("truth", "TEST_ASSAY.csv", 0, 1, 1, 0)
    score = Member("score", "TEST_ASSAY.csv", 0, 1, 1, 0)
    return Assay("TEST_ASSAY", "10.1000/test", expected_truth_rows, truth, score)


def _acquired(member: Member, path: Path) -> AcquiredMember:
    payload = path.read_bytes()
    return AcquiredMember(
        member=member,
        path=path,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        row_count=2,
        requested_ranges=("bytes=0-1", "bytes=2-3", "bytes=4-5"),
    )


def _schema(name: str) -> dict[str, object]:
    raw = json.loads((REPOSITORY_ROOT / "schemas" / name).read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return cast(dict[str, object], raw)


def test_frozen_config_preserves_exact_scope_and_caps() -> None:
    config = load_config(CONFIG_PATH)

    assert tuple(assay.assay_id for assay in config.assays) == (
        "BLAT_ECOLX_Firnberg_2014",
        "CAPSD_AAV2S_Sinai_2021",
        "GFP_AEQVI_Sarkisyan_2016",
        "SPG1_STRSG_Olson_2014",
    )
    members = tuple(member for assay in config.assays for member in (assay.truth, assay.score))
    assert sum(member.compressed_size for member in members) == 487_414_647
    assert sum(member.uncompressed_size for member in members) == 1_673_334_322
    assert config.caps.network_bytes == 550_000_000
    assert config.caps.scratch_bytes == 2_684_354_560
    assert config.caps.cos_growth_bytes == 2_000_000_000


def test_range_acquisition_validates_local_header_crc_and_exact_bytes(tmp_path: Path) -> None:
    payload = b"mutant,mutated_sequence,DMS_score\nA1C,CCC,1.25\n"
    archive_bytes, member = _zip_fixture(payload, "TEST_ASSAY.csv")
    archive = Archive(
        archive_id="fixture",
        url=("https://marks.hms.harvard.edu/proteingym/ProteinGym_v1.3/fixture.zip"),
        size_bytes=len(archive_bytes),
        etag="fixture-etag",
        last_modified="Wed, 19 Aug 2026 00:00:00 GMT",
    )
    reader = RangeReader(
        byte_cap=len(archive_bytes), open_url=_range_opener(archive_bytes, archive)
    )

    acquired = acquire_member(
        archive=archive,
        member=member,
        destination=tmp_path / "truth.csv",
        reader=reader,
    )

    assert acquired.path.read_bytes() == payload
    assert acquired.size_bytes == len(payload)
    assert acquired.row_count == 1
    assert reader.bytes_read < len(archive_bytes)
    assert len(acquired.requested_ranges) == 3


def test_range_acquisition_fails_closed_on_source_identity_drift(tmp_path: Path) -> None:
    payload = b"mutant,mutated_sequence,DMS_score\nA1C,CCC,1\n"
    archive_bytes, member = _zip_fixture(payload, "TEST_ASSAY.csv")
    archive = Archive(
        "fixture",
        "https://marks.hms.harvard.edu/proteingym/ProteinGym_v1.3/fixture.zip",
        len(archive_bytes),
        "expected-etag",
        "expected-last-modified",
    )

    with pytest.raises(ProteinGymError, match="ETag drifted"):
        observed = Archive(
            archive.archive_id,
            archive.url,
            archive.size_bytes,
            "observed-etag",
            archive.last_modified,
        )
        acquire_member(
            archive=archive,
            member=member,
            destination=tmp_path / "truth.csv",
            reader=RangeReader(
                byte_cap=len(archive_bytes), open_url=_range_opener(archive_bytes, observed)
            ),
        )


def test_processing_separates_truth_and_builds_candidate_pool_only(tmp_path: Path) -> None:
    assay = _assay(expected_truth_rows=2)
    config = _test_config(assay)
    truth_path = tmp_path / "truth.csv"
    score_path = tmp_path / "score.csv"
    truth_path.write_text(
        "mutant,mutated_sequence,DMS_score,DMS_score_bin\nA1C,CCC,1.250,1\nA2D,CDD,-0.0,0\n",
        encoding="utf-8",
    )
    score_path.write_text(
        "mutant,mutated_sequence,model_a,model_b\nA1C,CCC,0.5,\nA2D,CDD,NaN,-1\n",
        encoding="utf-8",
    )
    dataset_manifest = build_dataset_manifest(
        config,
        assay,
        _acquired(assay.truth, truth_path),
        _acquired(assay.score, score_path),
        downloaded_at=datetime(2026, 8, 19, tzinfo=UTC),
    )

    result = process_assay(
        config=config,
        assay=assay,
        truth_path=truth_path,
        score_path=score_path,
        dataset_manifest_bytes=dataset_manifest,
        config_bytes=b"{}\n",
        git_commit="a" * 40,
        output_root=tmp_path / "first",
    )

    jsonschema.validate(json.loads(dataset_manifest), _schema("dataset-manifest.schema.json"))
    processing_manifest = json.loads(result.processing_manifest_bytes)
    split_manifest = json.loads(result.split_manifest_bytes)
    jsonschema.validate(processing_manifest, _schema("processing-manifest.schema.json"))
    jsonschema.validate(split_manifest, _schema("split-manifest.schema.json"))
    assert processing_manifest["observable_feature_fields"] == ["mutant", "mutated_sequence"]
    assert processing_manifest["hidden_label_field"] == "DMS_score"
    assert split_manifest["partitions"] == ["candidate-pool"]
    assert split_manifest["held_out_partitions"] == []

    by_name = {path.name: path for _, path in result.payloads}
    with gzip.open(by_name["candidates.csv.gz"], "rt", encoding="utf-8") as handle:
        candidate_text = handle.read()
    with gzip.open(by_name["hidden-truth.csv.gz"], "rt", encoding="utf-8") as handle:
        truth_text = handle.read()
    assert "DMS_score" not in candidate_text
    assert "mutated_sequence" not in truth_text
    coverage = json.loads(by_name["score-coverage.json"].read_text(encoding="utf-8"))
    assert coverage["coverage"]["model_a"] == {
        "finite": 1,
        "missing": 0,
        "non_finite": 1,
    }
    assert coverage["coverage"]["model_b"] == {
        "finite": 1,
        "missing": 1,
        "non_finite": 0,
    }
    assert coverage["imputation"] == "none"
    assert coverage["model_approval"] == "none"

    ingest_attempt = build_ingest_attempt(
        config=config,
        assay=assay,
        truth=_acquired(assay.truth, truth_path),
        score=_acquired(assay.score, score_path),
        dataset_manifest_key="proteingym/v1.3/test/manifests/TEST_ASSAY/dataset.json",
        dataset_manifest_bytes=dataset_manifest,
        config_repository_path="configs/datasets/proteingym/test.json",
        config_sha256="b" * 64,
        git_commit="a" * 40,
        cos_destination="cos://data/proteingym",
        duplicate_write_status="coscli duplicate write rejected with exit 1",
        verified_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    jsonschema.validate(json.loads(ingest_attempt), _schema("dataset-ingest-attempt.schema.json"))


def test_processing_fails_closed_on_duplicate_truth_mutant(tmp_path: Path) -> None:
    assay = _assay()
    config = _test_config(assay)
    truth_path = tmp_path / "truth.csv"
    score_path = tmp_path / "score.csv"
    truth_path.write_text(
        "mutant,mutated_sequence,DMS_score\nA1C,CCC,1\nA1C,CCC,2\n",
        encoding="utf-8",
    )
    score_path.write_text("mutant,model\nA1C,0\n", encoding="utf-8")
    dataset_manifest = build_dataset_manifest(
        config,
        assay,
        _acquired(assay.truth, truth_path),
        _acquired(assay.score, score_path),
        downloaded_at=datetime(2026, 8, 19, tzinfo=UTC),
    )

    with pytest.raises(ProteinGymError, match="duplicate/missing mutant"):
        process_assay(
            config=config,
            assay=assay,
            truth_path=truth_path,
            score_path=score_path,
            dataset_manifest_bytes=dataset_manifest,
            config_bytes=b"{}\n",
            git_commit="a" * 40,
            output_root=tmp_path / "processed",
        )


def test_coscli_publisher_forbids_overwrite_and_fresh_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"immutable payload")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        if command[2].startswith("cos://"):
            Path(command[3]).write_bytes(source.read_bytes())
            return subprocess.CompletedProcess(command, 0)
        if len(commands) == 3:
            return subprocess.CompletedProcess(command, 17)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    publisher = CoscliFilePublisher("cos://data/approved", tmp_path / "readback")

    published = publisher.publish(source, "proteingym/object.txt")
    duplicate_status = publisher.verify_duplicate_rejected(source, "proteingym/object.txt")

    assert published.size_bytes == len(b"immutable payload")
    assert duplicate_status == "coscli duplicate write rejected with exit 17"
    assert commands[0][-3:] == ["--forbid-overwrite", "--err-retry-num", "0"]
    assert "--err-retry-num" in commands[1]
    assert "--forbid-overwrite" in commands[2]
    assert not any((tmp_path / "readback").rglob("*.txt"))
