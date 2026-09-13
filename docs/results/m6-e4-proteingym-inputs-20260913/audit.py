"""只读审计四 assay 的元数据、候选和 membership；不打开 truth 或 zero-shot。"""

import argparse
import csv
import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ASSAYS = (
    "BLAT_ECOLX_Firnberg_2014",
    "CAPSD_AAV2S_Sinai_2021",
    "GFP_AEQVI_Sarkisyan_2016",
    "SPG1_STRSG_Olson_2014",
)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit(root):
    receipt_path = root / "evidence/unified-attempt.json"
    receipt = json.loads(receipt_path.read_text())
    parents = {a["assay_id"]: a for a in receipt["assays"]}
    if set(parents) != set(ASSAYS):
        raise ValueError("unexpected cohort")
    rows = []
    for assay in ASSAYS:
        parent = parents[assay]
        matches = list((root / "processed" / assay).glob("*/processing-manifest.json"))
        if len(matches) != 1:
            raise ValueError("ambiguous processing manifest")
        processing_path = matches[0]
        split_path = processing_path.parent / "split-manifest.json"
        dataset_path = root / "manifests" / assay / "dataset-manifest-v1.json"
        files = []
        for path, key in (
            (dataset_path, "dataset_manifest_sha256"),
            (processing_path, "processing_manifest_sha256"),
            (split_path, "split_manifest_sha256"),
        ):
            actual = digest(path)
            if actual != parent[key]:
                raise ValueError(f"{assay}: {key} mismatch")
            files.append(
                {
                    "relative_path": str(path.relative_to(root)),
                    "sha256": actual,
                    "size_bytes": path.stat().st_size,
                }
            )
        processing = json.loads(processing_path.read_text())
        split = json.loads(split_path.read_text())
        if (
            processing["dataset_manifest_sha256"] != parent["dataset_manifest_sha256"]
            or split["processing_manifest_sha256"] != parent["processing_manifest_sha256"]
            or split["partitions"] != ["candidate-pool"]
            or split["held_out_partitions"] != []
            or processing["label_direction"] != "higher_is_better"
            or processing["observable_feature_fields"] != ["mutant", "mutated_sequence"]
        ):
            raise ValueError("parent binding or visibility mismatch")
        observable = [
            f for f in processing["output_files"] if f["object_key"].endswith("/candidates.csv.gz")
        ]
        if len(observable) != 1:
            raise ValueError("ambiguous candidate file")
        records = {}
        for entry in (*observable, split["membership_file"]):
            path = processing_path.parent / Path(entry["object_key"]).name
            if digest(path) != entry["sha256"] or path.stat().st_size != entry["size_bytes"]:
                raise ValueError("observable or membership checksum mismatch")
            files.append(
                {
                    "relative_path": str(path.relative_to(root)),
                    "sha256": entry["sha256"],
                    "size_bytes": entry["size_bytes"],
                }
            )
            with gzip.open(path, "rt", newline="") as stream:
                reader = csv.DictReader(stream)
                expected = (
                    ["candidate_id", "partition"]
                    if path.name.startswith("membership")
                    else ["candidate_id", "mutant", "mutated_sequence"]
                )
                if reader.fieldnames != expected:
                    raise ValueError("unexpected columns; possible visibility violation")
                ids = set()
                for row in reader:
                    candidate = row["candidate_id"]
                    if candidate in ids:
                        raise ValueError("duplicate ID")
                    if "partition" in row and row["partition"] != "candidate-pool":
                        raise ValueError("unexpected partition")
                    if "mutated_sequence" in row and (
                        len(row["mutated_sequence"]) < 2
                        or set(row["mutated_sequence"]) - set("ACDEFGHIKLMNPQRSTVWY")
                    ):
                        raise ValueError("invalid observable sequence")
                    ids.add(candidate)
                if len(ids) != entry["row_count"]:
                    raise ValueError("row count mismatch")
                records[path.name] = ids
        ids = records["candidates.csv.gz"]
        if ids != records["membership.csv.gz"] or len(ids) != parent["candidate_count"]:
            raise ValueError("candidate/membership exact cover failed")
        subset = sorted(
            ids,
            key=lambda c: (hashlib.sha256(f"m6-e4-pool-v1:{assay}:{c}".encode()).hexdigest(), c),
        )[:256]
        if len(subset) != 256:
            raise ValueError("insufficient candidate pool")
        subset_hash = hashlib.sha256(json.dumps(subset, separators=(",", ":")).encode()).hexdigest()
        rows.append(
            {
                "assay_id": assay,
                "candidate_count": len(ids),
                "files": files,
                "subset_count": len(subset),
                "ordered_subset_ids_sha256": subset_hash,
                "subset_ids_persisted": False,
                "feature_matrix_generated": False,
                "truth_hash_verified": False,
                "label_direction": processing["label_direction"],
                "held_out_partitions": split["held_out_partitions"],
            }
        )
    return {
        "schema_version": "m6-e4-proteingym-observable-audit-v1",
        "checked_at": datetime.now(UTC).isoformat(),
        "audit_script_sha256": digest(Path(__file__)),
        "receipt_sha256": digest(receipt_path),
        "status": "OBSERVABLE_INPUTS_VERIFIED",
        "truth_read": False,
        "zero_shot_read": False,
        "scientific_trajectories": 0,
        "gate_a": "PENDING",
        "assays": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # 独占创建结果目录，失败也保留尝试，禁止覆盖旧记录。
    args.output.mkdir(parents=True, exist_ok=False)
    result = audit(args.root)
    payload = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    (args.output / "report.json").write_bytes(payload)
    (args.output / "report.sha256").write_text(hashlib.sha256(payload).hexdigest() + "\n")
    print(json.dumps({"status": result["status"], "assays": len(result["assays"])}))
