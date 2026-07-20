"""Canonical run identity contract."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class RunSpec:
    """Inputs that define one reproducible benchmark run.

    Runtime attempt data such as host name, timestamps, or retry number is deliberately excluded
    because attempts do not change scientific run identity.
    """

    schema_version: str
    protocol_version: str
    git_commit: str
    environment_digest: str
    dataset_manifest_sha256: str
    processing_manifest_sha256: str
    split_sha256: str
    feature_sha256: str
    experiment_config_sha256: str
    model_id: str
    model_config_sha256: str
    acquisition_id: str
    acquisition_config_sha256: str
    seed: int
    initial_sample_size: int
    batch_size: int
    budget: int

    def __post_init__(self) -> None:
        if self.schema_version != "1":
            raise ValueError("schema_version must be '1'")
        if not self.protocol_version:
            raise ValueError("protocol_version must not be empty")
        if not _GIT_COMMIT_PATTERN.fullmatch(self.git_commit):
            raise ValueError("git_commit must be a full lowercase 40-character SHA")

        hash_fields = (
            "environment_digest",
            "dataset_manifest_sha256",
            "processing_manifest_sha256",
            "split_sha256",
            "feature_sha256",
            "experiment_config_sha256",
            "model_config_sha256",
            "acquisition_config_sha256",
        )
        for field_name in hash_fields:
            if not _SHA256_PATTERN.fullmatch(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")

        if not self.model_id or not self.acquisition_id:
            raise ValueError("model_id and acquisition_id must not be empty")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        for field_name in ("initial_sample_size", "batch_size", "budget"):
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be positive")

    def canonical_json(self) -> str:
        """Return a stable JSON representation used for run identity."""
        return json.dumps(asdict(self), allow_nan=False, separators=(",", ":"), sort_keys=True)

    @property
    def run_id(self) -> str:
        """Return the full content-derived SHA-256 run identifier."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
