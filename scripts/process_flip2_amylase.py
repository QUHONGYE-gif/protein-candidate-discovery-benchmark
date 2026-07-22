#!/usr/bin/env python3
"""Build one immutable FLIP2 Alpha Amylase processing bundle outside Git."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator, FormatChecker

from pcdb.datasets.amylase import build_amylase_processing_bundle, load_amylase_processing_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path("schemas/processing-manifest.schema.json"),
    )
    return parser


def _ensure_outside_repository(path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    if path.resolve().is_relative_to(repository_root):
        raise ValueError("staging root must be outside the Git repository")


def _exclusive_write(root: Path, object_key: str, payload: bytes) -> Path:
    relative = PurePosixPath(object_key)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise ValueError("object key must be a safe relative POSIX path")
    destination = root.joinpath(*relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as handle:
        handle.write(payload)
        handle.flush()
    return destination


def main() -> int:
    args = _parser().parse_args()
    _ensure_outside_repository(args.staging_root)
    config_bytes = args.config.read_bytes()
    dataset_manifest_bytes = args.dataset_manifest.read_bytes()
    config = load_amylase_processing_config(args.config)
    payloads, manifest = build_amylase_processing_bundle(
        config=config,
        config_bytes=config_bytes,
        dataset_manifest_bytes=dataset_manifest_bytes,
        raw_root=args.raw_root,
        git_commit=args.git_commit,
    )

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    hidden_label_field = manifest.get("hidden_label_field")
    observable_feature_fields = manifest.get("observable_feature_fields")
    if not isinstance(hidden_label_field, str) or not isinstance(observable_feature_fields, list):
        raise ValueError("Processing Manifest field visibility is invalid")
    if hidden_label_field in observable_feature_fields:
        raise ValueError("hidden label cannot be observable")

    destinations = [
        _exclusive_write(args.staging_root, object_key, payload)
        for object_key, payload in sorted(payloads.items())
    ]
    processing_id = manifest["processing_id"]
    if not isinstance(processing_id, str):
        raise ValueError("Processing Manifest processing_id must be a string")
    manifest_key = (
        f"manifests/{config.dataset_id}/{config.dataset_version}/{config.task_id}/"
        f"processing/{processing_id}/processing-manifest.json"
    )
    destinations.append(
        _exclusive_write(
            args.staging_root,
            manifest_key,
            (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
        )
    )
    for destination in destinations:
        print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
