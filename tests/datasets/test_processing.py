from __future__ import annotations

from copy import deepcopy
from itertools import combinations

import pytest

from pcdb.datasets.processing import (
    ProcessingError,
    modal_reference,
    near_duplicate_counts,
    validate_processing_manifest_domain,
)


def _brute_force_counts(sequences: tuple[str, ...], threshold: int) -> dict[str, int]:
    counts = {str(distance): 0 for distance in range(1, threshold + 1)}
    for left, right in combinations(sequences, 2):
        distance = sum(a != b for a, b in zip(left, right, strict=True))
        if 0 < distance <= threshold:
            counts[str(distance)] += 1
    return counts


@pytest.mark.parametrize("threshold", [1, 2, 3])
def test_near_duplicate_counts_match_full_length_brute_force(threshold: int) -> None:
    sequences = ("AAAA", "AAAC", "AACC", "ACCC", "CCCC", "CCCA", "DCCA")

    assert near_duplicate_counts(sequences, "AAAA", threshold) == _brute_force_counts(
        sequences, threshold
    )


def test_modal_reference_can_fail_closed_on_a_tie() -> None:
    with pytest.raises(ProcessingError, match="ambiguous"):
        modal_reference(("AAAA", "CAAA"), require_unique=True)


def test_near_duplicate_audit_rejects_duplicate_sequences() -> None:
    with pytest.raises(ProcessingError, match="unique sequences"):
        near_duplicate_counts(("AAAA", "AAAA"), "AAAA", 1)


def _domain_manifest() -> dict[str, object]:
    return {
        "candidate_id_unique": True,
        "observable_feature_fields": ["sequence"],
        "hidden_label_field": "target",
        "output_files": [
            {"object_key": "processed/example/candidates.csv", "row_count": 2},
            {"object_key": "processed/example/hidden-truth.csv", "row_count": 2},
        ],
    }


def test_processing_manifest_domain_accepts_separated_complete_outputs() -> None:
    validate_processing_manifest_domain(_domain_manifest())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"observable_feature_fields": ["sequence", "target"]}, "cannot be observable"),
        ({"candidate_id_unique": False}, "unique candidate IDs"),
        (
            {
                "output_files": [
                    {"object_key": "processed/example/candidates.csv", "row_count": 2},
                    {"object_key": "processed/example/candidates.csv", "row_count": 2},
                ]
            },
            "object keys must be unique",
        ),
        (
            {"output_files": [{"object_key": "processed/example/candidates.csv"}]},
            "must include row_count",
        ),
    ],
)
def test_processing_manifest_domain_fails_closed(mutation: dict[str, object], message: str) -> None:
    manifest = deepcopy(_domain_manifest())
    manifest.update(mutation)

    with pytest.raises(ProcessingError, match=message):
        validate_processing_manifest_domain(manifest)
