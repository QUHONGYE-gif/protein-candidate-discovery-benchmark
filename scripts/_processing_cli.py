"""Shared command-line staging workflow for deterministic processing bundles."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Protocol

from jsonschema import Draft202012Validator, FormatChecker

from pcdb.datasets.processing import validate_processing_manifest_domain


class ConfigLike(Protocol):
    dataset_id: str
    dataset_version: str
    task_id: str


def _parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
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


def run_processing_cli(
    *,
    description: str,
    load_config: Callable[[Path], ConfigLike],
    build_bundle: Callable[..., tuple[dict[str, bytes], dict[str, object]]],
) -> int:
    """Validate and stage one task-specific processing bundle without publishing it."""
    args = _parser(description).parse_args()
    _ensure_outside_repository(args.staging_root)
    config_bytes = args.config.read_bytes()
    dataset_manifest_bytes = args.dataset_manifest.read_bytes()
    config = load_config(args.config)
    payloads, manifest = build_bundle(
        config=config,
        config_bytes=config_bytes,
        dataset_manifest_bytes=dataset_manifest_bytes,
        raw_root=args.raw_root,
        git_commit=args.git_commit,
    )

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    validate_processing_manifest_domain(manifest)

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
