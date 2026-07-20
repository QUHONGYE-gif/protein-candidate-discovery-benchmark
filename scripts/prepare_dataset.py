#!/usr/bin/env python3
"""Download and verify one public source inventory outside the Git worktree."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from pcdb.datasets import build_dataset_manifest, download_source_file, load_source_inventory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path("schemas/dataset-manifest.schema.json"),
    )
    return parser


def _ensure_outside_repository(staging_root: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    if staging_root.resolve().is_relative_to(repository_root):
        raise ValueError("staging root must be outside the Git repository")


def main() -> int:
    args = _parser().parse_args()
    _ensure_outside_repository(args.staging_root)
    inventory = load_source_inventory(args.inventory)
    verified = [download_source_file(source, args.staging_root) for source in inventory.files]
    manifest = build_dataset_manifest(inventory, verified, downloaded_at=datetime.now(UTC))

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)

    destination = (
        args.staging_root
        / "manifests"
        / inventory.dataset_id
        / inventory.dataset_version
        / inventory.task_id
        / "dataset-manifest.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
