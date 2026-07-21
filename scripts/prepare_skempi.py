#!/usr/bin/env python3
"""Download, verify, and inspect the official SKEMPI 2.0 source bundle."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from pcdb.datasets.skempi import (
    build_skempi_dataset_manifest,
    build_skempi_source_inspection,
    download_skempi_source_file,
    load_skempi_source_inventory,
)


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


def _write_json_exclusive(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    args = _parser().parse_args()
    _ensure_outside_repository(args.staging_root)
    inventory = load_skempi_source_inventory(args.inventory)
    verified = [
        download_skempi_source_file(source, args.staging_root) for source in inventory.files
    ]
    inspection = build_skempi_source_inspection(inventory, verified)
    observed_limitations = inspection["observed_limitations"]
    if not isinstance(observed_limitations, list) or not all(
        isinstance(item, str) for item in observed_limitations
    ):
        raise TypeError("source inspection observed_limitations must be strings")
    manifest = build_skempi_dataset_manifest(
        inventory,
        verified,
        downloaded_at=datetime.now(UTC),
        observed_limitations=observed_limitations,
    )

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)

    destination_root = (
        args.staging_root
        / "manifests"
        / inventory.dataset_id
        / inventory.dataset_version
        / inventory.task_id
    )
    manifest_path = destination_root / "dataset-manifest.json"
    inspection_path = destination_root / "source-inspection.json"
    _write_json_exclusive(manifest_path, manifest)
    _write_json_exclusive(inspection_path, inspection)

    print(manifest_path)
    print(inspection_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
