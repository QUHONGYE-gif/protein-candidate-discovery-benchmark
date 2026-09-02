"""M5+ server-first namespace, ACL and capacity preflight."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from pcdb.governance.checks import (
    CheckEvidence,
    CheckReport,
    CheckViolation,
    build_report,
    capture_check,
)
from pcdb.governance.contract import load_json, validate_governance_config


def server_preflight_report(
    *,
    config_path: Path,
    expected_peak_bytes: int,
    available_bytes: int,
    observed_group: str,
    observed_writers: Sequence[str],
) -> CheckReport:
    """Check the planned peak, exact writer set and v2 server namespace."""

    loaded = load_json(config_path)

    def config_check() -> CheckEvidence:
        authorities = validate_governance_config(loaded.value)
        return CheckEvidence(
            reason="active Governance v2 deployment configuration is valid",
            evidence={"scientific_authorities": list(authorities)},
        )

    def namespace_acl() -> CheckEvidence:
        server = cast(dict[str, Any], loaded.value["server"])
        if observed_group != server["project_group"]:
            raise CheckViolation(
                f"server group mismatch: expected {server['project_group']}, got {observed_group}"
            )
        expected_writers = set(cast(list[str], server["writers"]))
        actual_writers = set(observed_writers)
        if len(actual_writers) != len(observed_writers) or actual_writers != expected_writers:
            raise CheckViolation(
                f"server writer set mismatch: expected={sorted(expected_writers)}, "
                f"actual={sorted(actual_writers)}"
            )
        root = Path(cast(str, server["root"]))
        paths = [str(root / item) for item in cast(list[str], server["subdirectories"])]
        return CheckEvidence(
            reason="server namespace, project group and exact writer set match",
            evidence={"group": observed_group, "paths": sorted(paths)},
        )

    def capacity() -> CheckEvidence:
        if (
            isinstance(expected_peak_bytes, bool)
            or isinstance(available_bytes, bool)
            or expected_peak_bytes < 0
            or available_bytes <= 0
        ):
            raise CheckViolation(
                "capacity inputs must be non-negative peak and positive available bytes"
            )
        fraction = expected_peak_bytes / available_bytes
        if fraction > 0.7:
            raise CheckViolation(f"planned peak {fraction:.3%} exceeds the 70% hard capacity cap")
        if fraction > 0.5:
            raise CheckViolation(
                f"planned peak {fraction:.3%} exceeds the 50% target; reduce it or add space"
            )
        return CheckEvidence(
            reason="planned milestone peak is within the 50% capacity target",
            evidence={
                "available_bytes": available_bytes,
                "expected_peak_bytes": expected_peak_bytes,
                "fraction": fraction,
                "hard_cap_fraction": 0.7,
                "target_fraction": 0.5,
            },
        )

    return build_report(
        (
            capture_check(
                name="server-governance-config",
                category="schema_domain",
                blocks=("server_provisioning", "formal_execution"),
                input_digests=(loaded.sha256,),
                operation=config_check,
            ),
            capture_check(
                name="server-namespace-acl",
                category="prefix_storage_safety",
                blocks=("formal_execution", "archive"),
                input_digests=(loaded.sha256,),
                operation=namespace_acl,
            ),
            capture_check(
                name="server-capacity",
                category="resource_cost_cap",
                blocks=("formal_execution",),
                input_digests=(loaded.sha256,),
                operation=capacity,
            ),
        )
    )
