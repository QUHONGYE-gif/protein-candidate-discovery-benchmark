#!/usr/bin/env python3
"""Build one immutable FLIP2 Hydro topology stress tracer bundle outside Git."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker

from pcdb.datasets.hydro_split import (
    build_hydro_split_bundle,
    load_hydro_split_config,
    validate_hydro_split_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--processing-manifest", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--upstream-metadata", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument(
        "--processing-schema",
        type=Path,
        default=Path("schemas/processing-manifest.schema.json"),
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path("schemas/split-manifest.schema.json"),
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
    processing_manifest_bytes = args.processing_manifest.read_bytes()
    candidate_bytes = args.candidates.read_bytes()
    upstream_bytes = args.upstream_metadata.read_bytes()
    config = load_hydro_split_config(args.config)

    processing_schema = json.loads(args.processing_schema.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(processing_schema)
    processing_manifest = json.loads(processing_manifest_bytes)
    Draft202012Validator(
        processing_schema,
        format_checker=FormatChecker(),
    ).validate(processing_manifest)

    payloads, manifest = build_hydro_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_manifest_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        git_commit=args.git_commit,
    )
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    by_name = {PurePosixPath(key).name: payload for key, payload in payloads.items()}
    validate_hydro_split_bundle(
        config=config,
        config_bytes=config_bytes,
        processing_manifest_bytes=processing_manifest_bytes,
        candidate_bytes=candidate_bytes,
        upstream_bytes=upstream_bytes,
        membership_bytes=by_name["membership.csv"],
        audit_bytes=by_name["split-audit.json"],
        manifest=manifest,
    )

    destinations = [
        _exclusive_write(args.staging_root, object_key, payload)
        for object_key, payload in sorted(payloads.items())
    ]
    membership = manifest.get("membership_file")
    if not isinstance(membership, dict):
        raise ValueError("Split Manifest membership_file must be an object")
    membership_key = cast(dict[str, object], membership).get("object_key")
    if not isinstance(membership_key, str):
        raise ValueError("Split Manifest membership object key must be a string")
    protocol_hash = PurePosixPath(membership_key).parent.name
    manifest_key = (
        f"manifests/{config.dataset_id}/{config.dataset_version}/{config.task_id}/splits/"
        f"{config.split_id}/{protocol_hash}/split-manifest.json"
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
