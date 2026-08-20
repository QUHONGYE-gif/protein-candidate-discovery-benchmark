from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
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

import pcdb.datasets.proteingym as proteingym_module
from pcdb.cli import build_parser
from pcdb.datasets.proteingym import (
    APPROVAL,
    CONTINUATION_APPROVAL,
    AcquiredMember,
    Archive,
    Assay,
    Caps,
    CoscliFilePublisher,
    ExpectedFile,
    Member,
    ProteinGymConfig,
    ProteinGymError,
    PublishedFile,
    RangeReader,
    acquire_member,
    build_dataset_manifest,
    build_ingest_attempt,
    load_config,
    process_assay,
    run_local_bundle,
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


def _zip_members_fixture(
    payloads: dict[str, bytes], archive_id: str
) -> tuple[bytes, dict[str, Member]]:
    archive = io.BytesIO()
    members: dict[str, Member] = {}
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for member_name, payload in payloads.items():
            bundle.writestr(f"nested/{member_name}", payload)
        for member_name in payloads:
            info = bundle.getinfo(f"nested/{member_name}")
            members[member_name] = Member(
                archive_id=archive_id,
                member_name=member_name,
                local_header_offset=info.header_offset,
                compressed_size=info.compress_size,
                uncompressed_size=info.file_size,
                crc32=info.CRC,
            )
    return archive.getvalue(), members


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


def _multi_range_opener(
    sources: dict[str, tuple[bytes, Archive]],
) -> Callable[[Request], AbstractContextManager[BinaryIO]]:
    def open_url(request: Request) -> AbstractContextManager[BinaryIO]:
        archive_bytes, identity = sources[request.full_url]
        return _range_opener(archive_bytes, identity)(request)

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


def test_coscli_publisher_parses_one_complete_exact_prefix_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = """\
 KEY | TYPE | LAST MODIFIED | ETAG | SIZE | RESTORESTATUS
 prefix/a.json | STANDARD | 2026-08-20 | \"a\" | 1 B |
 prefix/b.csv | STANDARD | 2026-08-20 | \"b\" | 2 B |
 TOTAL OBJECTS: | 2 |
"""
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(subprocess, "run", fake_run)
    publisher = CoscliFilePublisher("cos://data", tmp_path / "readback")

    assert publisher.list_keys("prefix") == ("prefix/a.json", "prefix/b.csv")
    assert commands == [["coscli", "ls", "cos://data/prefix/", "-r", "--limit", "1000"]]


def test_local_bundle_acquires_and_revalidates_exact_eight_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assay_ids = (
        "BLAT_ECOLX_Firnberg_2014",
        "CAPSD_AAV2S_Sinai_2021",
        "GFP_AEQVI_Sarkisyan_2016",
        "SPG1_STRSG_Olson_2014",
    )
    truth_payloads = {
        f"{assay_id}.csv": (b"mutant,mutated_sequence,DMS_score\nA1C,CCC,1\nA2D,CDD,-1\n")
        for assay_id in assay_ids
    }
    score_payloads = {
        f"{assay_id}.csv": b"mutant,mutated_sequence,model\nA1C,CCC,0.5\nA2D,CDD,0.1\n"
        for assay_id in assay_ids
    }
    truth_zip, truth_members = _zip_members_fixture(truth_payloads, "truth")
    score_zip, score_members = _zip_members_fixture(score_payloads, "score")
    truth_archive = Archive(
        "truth",
        "https://marks.hms.harvard.edu/proteingym/ProteinGym_v1.3/truth.zip",
        len(truth_zip),
        "truth-etag",
        "Wed, 19 Aug 2026 00:00:00 GMT",
    )
    score_archive = Archive(
        "score",
        "https://marks.hms.harvard.edu/proteingym/ProteinGym_v1.3/score.zip",
        len(score_zip),
        "score-etag",
        "Wed, 19 Aug 2026 00:00:00 GMT",
    )
    assays = tuple(
        Assay(
            assay_id,
            "10.1000/test",
            2 if index == 0 else None,
            truth_members[f"{assay_id}.csv"],
            score_members[f"{assay_id}.csv"],
        )
        for index, assay_id in enumerate(assay_ids)
    )
    config = ProteinGymConfig(
        dataset_id="proteingym",
        dataset_version="v1.3",
        task_id="four-assay-substitutions",
        source_url="https://github.com/OATML-Markslab/ProteinGym",
        doi="10.5281/zenodo.15293562",
        license="bounded private research use",
        license_boundary="no public raw redistribution",
        object_prefix="proteingym/v1.3/test",
        approval=APPROVAL,
        caps=Caps(1_000_000, 10_000_000, 10_000_000, 1_000_000, 1_000_000, 1_000_000),
        archives=(truth_archive, score_archive),
        assays=assays,
    )
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    config_path = repository_root.joinpath(*proteingym_module.CANONICAL_CONFIG_RELATIVE.parts)
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}\n", encoding="utf-8")
    scratch_root = tmp_path / "bundle"
    reviewed_commit = "a" * 40
    monkeypatch.setattr(proteingym_module, "load_config", lambda _path: config)
    monkeypatch.setattr(proteingym_module, "_verify_repository", lambda _root, _commit: None)

    manifest = run_local_bundle(
        config_path=config_path,
        repository_root=repository_root,
        scratch_root=scratch_root,
        reviewed_git_commit=reviewed_commit,
        approval=CONTINUATION_APPROVAL,
        approval_comment_id=999,
        original_approval_comment_id=5352364303,
        open_url=_multi_range_opener(
            {
                truth_archive.url: (truth_zip, truth_archive),
                score_archive.url: (score_zip, score_archive),
            }
        ),
    )

    assert manifest["source_network_cap_scope"] == "v3_local_acquisition_only"
    assert manifest["full_archive_downloaded"] is False
    assert len(cast(list[object], manifest["members"])) == 8
    loaded = proteingym_module._load_local_bundle(
        bundle_root=scratch_root,
        repository_root=repository_root,
        config=config,
        config_sha256=hashlib.sha256(b"{}\n").hexdigest(),
        reviewed_git_commit=reviewed_commit,
        approval_comment_id=999,
        original_approval_comment_id=5352364303,
    )
    assert tuple(loaded.members) == assay_ids
    assert loaded.source_network_bytes == manifest["source_network_bytes"]
    assert loaded.transfer_bytes == sum(
        path.stat().st_size for path in scratch_root.rglob("*") if path.is_file()
    )

    def load_bundle() -> object:
        return proteingym_module._load_local_bundle(
            bundle_root=scratch_root,
            repository_root=repository_root,
            config=config,
            config_sha256=hashlib.sha256(b"{}\n").hexdigest(),
            reviewed_git_commit=reviewed_commit,
            approval_comment_id=999,
            original_approval_comment_id=5352364303,
        )

    extra_file = scratch_root / "DMS_ProteinGym_substitutions.zip"
    extra_file.write_bytes(b"must not transfer")
    with pytest.raises(ProteinGymError, match="nine-file inventory"):
        load_bundle()
    extra_file.unlink()

    extra_symlink = scratch_root / "unexpected-link"
    extra_symlink.symlink_to(scratch_root / "local-acquisition-bundle.json")
    with pytest.raises(ProteinGymError, match="contains a symlink"):
        load_bundle()
    extra_symlink.unlink()

    extra_fifo = scratch_root / "unexpected-fifo"
    os.mkfifo(extra_fifo)
    with pytest.raises(ProteinGymError, match="contains a special file"):
        load_bundle()
    extra_fifo.unlink()

    manifest_path = scratch_root / "local-acquisition-bundle.json"
    manifest_copy = tmp_path / "manifest-copy.json"
    manifest_copy.write_bytes(manifest_path.read_bytes())
    manifest_path.unlink()
    manifest_path.symlink_to(manifest_copy)
    with pytest.raises(ProteinGymError, match="manifest must be a regular non-symlink file"):
        load_bundle()


@pytest.mark.parametrize("location", ["outside", "inside-drift"])
def test_local_bundle_rejects_noncanonical_config_before_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    config_path = (
        tmp_path / "outside-config.json"
        if location == "outside"
        else repository_root / "config.json"
    )
    config_path.write_text("{}\n", encoding="utf-8")
    scratch_root = tmp_path / f"bundle-{location}"
    monkeypatch.setattr(proteingym_module, "_verify_repository", lambda _root, _commit: None)
    monkeypatch.setattr(
        proteingym_module,
        "load_config",
        lambda _path: pytest.fail("config must be rejected before it is loaded"),
    )

    with pytest.raises(ProteinGymError, match="canonical file in the reviewed repository"):
        run_local_bundle(
            config_path=config_path,
            repository_root=repository_root,
            scratch_root=scratch_root,
            reviewed_git_commit="a" * 40,
            approval=CONTINUATION_APPROVAL,
            approval_comment_id=999,
            original_approval_comment_id=5352364303,
            open_url=lambda _request: pytest.fail(
                "config must be rejected before any network request"
            ),
        )

    assert not scratch_root.exists()


class _InventoryPublisher:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def publish(self, path: Path, object_key: str) -> PublishedFile:
        payload = path.read_bytes()
        self.objects[object_key] = payload
        return PublishedFile(object_key, hashlib.sha256(payload).hexdigest(), len(payload))

    def verify_duplicate_rejected(self, path: Path, object_key: str) -> str:
        return "rejected"

    def list_keys(self, object_prefix: str) -> tuple[str, ...]:
        return tuple(sorted(self.objects))

    def retrieve(self, object_key: str, destination: Path) -> PublishedFile:
        payload = self.objects[object_key]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return PublishedFile(object_key, hashlib.sha256(payload).hexdigest(), len(payload))


def _expected(tmp_path: Path, key: str, payload: bytes) -> ExpectedFile:
    path = tmp_path / key.replace("/", "-")
    path.write_bytes(payload)
    return ExpectedFile(
        key, path, hashlib.sha256(payload).hexdigest(), len(payload), "A", "payload"
    )


def test_inventory_reconciliation_reuses_exact_prefix_and_publishes_only_missing(
    tmp_path: Path,
) -> None:
    expected = (
        _expected(tmp_path, "prefix/a", b"a"),
        _expected(tmp_path, "prefix/b", b"b"),
        _expected(tmp_path, "prefix/c", b"c"),
    )
    publisher = _InventoryPublisher({"prefix/a": b"a", "prefix/b": b"b"})

    actions = proteingym_module._reconcile_inventory(
        expected_files=expected,
        observed_keys=publisher.list_keys("prefix"),
        publisher=publisher,
        scratch_root=tmp_path / "scratch",
        preverified={},
    )

    assert actions == {"prefix/a": "reuse", "prefix/b": "reuse", "prefix/c": "publish"}


@pytest.mark.parametrize(
    ("objects", "message"),
    [
        ({"prefix/a": b"changed"}, "identity differs"),
        ({"prefix/a": b"a", "prefix/extra": b"x"}, "outside the prepared inventory"),
        ({"prefix/b": b"b"}, "publication-order prefix"),
    ],
)
def test_inventory_reconciliation_fails_closed_on_ambiguous_existing_state(
    tmp_path: Path, objects: dict[str, bytes], message: str
) -> None:
    expected = (
        _expected(tmp_path, "prefix/a", b"a"),
        _expected(tmp_path, "prefix/b", b"b"),
    )
    publisher = _InventoryPublisher(objects)

    with pytest.raises(ProteinGymError, match=message):
        proteingym_module._reconcile_inventory(
            expected_files=expected,
            observed_keys=publisher.list_keys("prefix"),
            publisher=publisher,
            scratch_root=tmp_path / "scratch",
            preverified={},
        )


def test_existing_completed_assay_chain_is_fresh_validated_for_reuse(tmp_path: Path) -> None:
    assay = _assay(expected_truth_rows=2)
    config = _test_config(assay)
    truth_path = tmp_path / "bundle" / "raw" / assay.assay_id / "truth.csv"
    score_path = tmp_path / "bundle" / "raw" / assay.assay_id / "zero-shot-scores.csv"
    truth_path.parent.mkdir(parents=True)
    truth_path.write_text(
        "mutant,mutated_sequence,DMS_score\nA1C,CCC,1\nA2D,CDD,-1\n",
        encoding="utf-8",
    )
    score_path.write_text(
        "mutant,mutated_sequence,model\nA1C,CCC,0.5\nA2D,CDD,0.1\n",
        encoding="utf-8",
    )
    truth = _acquired(assay.truth, truth_path)
    score = _acquired(assay.score, score_path)
    config_bytes = b"{}\n"
    original_commit = "b" * 40
    prepared = proteingym_module._prepare_continuation_assay(
        config=config,
        assay=assay,
        truth=truth,
        score=score,
        acquired_at=datetime(2026, 8, 20, tzinfo=UTC),
        config_bytes=config_bytes,
        config_repository_path="config.json",
        reviewed_git_commit=original_commit,
        cos_destination="cos://data",
        scratch_root=tmp_path / "prior",
    )
    publisher = _InventoryPublisher(
        {item.object_key: item.path.read_bytes() for item in prepared.files}
    )

    files, evidence, dataset_path, preverified = proteingym_module._load_existing_blat(
        config=config,
        config_sha256=hashlib.sha256(config_bytes).hexdigest(),
        original_reviewed_git_commit=original_commit,
        observed_keys=publisher.list_keys(config.object_prefix),
        publisher=publisher,
        scratch_root=tmp_path / "continuation",
        truth=truth,
        score=score,
    )

    assert len(files) == 11
    assert len(preverified) == 11
    assert dataset_path.name == "dataset-manifest-v1.json"
    assert evidence["continuation_action"] == "reused_after_fresh_validation"
    assert evidence["candidate_count"] == 2


def test_final_evidence_byte_accounting_converges() -> None:
    evidence: dict[str, object] = {"contract_version": "2", "status": "succeeded"}

    payload = proteingym_module._final_evidence_bytes(
        evidence=evidence, growth_before_final=100, prefix_before_final=200
    )

    assert evidence["final_evidence_size_bytes"] == len(payload)
    assert evidence["continuation_cos_growth_bytes"] == 100 + len(payload)
    assert evidence["expected_prefix_bytes_after_final"] == 200 + len(payload)


def test_cli_exposes_separate_local_bundle_and_server_continuation_commands() -> None:
    local = build_parser().parse_args(
        [
            "proteingym-local-bundle",
            "--config",
            "config.json",
            "--reviewed-git-commit",
            "a" * 40,
            "--approval",
            CONTINUATION_APPROVAL,
            "--approval-comment-id",
            "999",
            "--original-approval-comment-id",
            "5352364303",
            "--repository-root",
            "repo",
            "--scratch-root",
            "bundle",
        ]
    )
    continuation = build_parser().parse_args(
        [
            "proteingym-continue",
            "--config",
            "config.json",
            "--reviewed-git-commit",
            "a" * 40,
            "--original-reviewed-git-commit",
            "b" * 40,
            "--approval",
            CONTINUATION_APPROVAL,
            "--approval-comment-id",
            "999",
            "--bundle-approval-comment-id",
            "999",
            "--original-approval-comment-id",
            "5352364303",
            "--cos-destination",
            "cos://data",
            "--repository-root",
            "repo",
            "--bundle-root",
            "bundle",
            "--scratch-root",
            "scratch",
        ]
    )

    assert local.command == "proteingym-local-bundle"
    assert continuation.command == "proteingym-continue"
    assert continuation.prior_continuation_approval_comment_id == []


def test_replacement_can_reuse_original_bundle_with_new_server_authorization() -> None:
    proteingym_module._validate_continuation_approvals(
        approval_comment_id=1000,
        bundle_approval_comment_id=999,
        original_approval_comment_id=5352364303,
        prior_continuation_approval_comment_ids=(999,),
    )


def test_replacement_rejects_bundle_id_missing_from_server_start_history() -> None:
    with pytest.raises(ProteinGymError, match="retain the bundle authorization"):
        proteingym_module._validate_continuation_approvals(
            approval_comment_id=1000,
            bundle_approval_comment_id=999,
            original_approval_comment_id=5352364303,
            prior_continuation_approval_comment_ids=(),
        )
