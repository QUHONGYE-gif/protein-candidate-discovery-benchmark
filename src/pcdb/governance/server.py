"""M5+ target-host namespace, ACL and capacity preflight."""

from __future__ import annotations

import grp
import pwd
import shutil
import socket
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from pcdb.governance.checks import (
    CheckEvidence,
    CheckReport,
    CheckViolation,
    build_report,
    capture_check,
)
from pcdb.governance.contract import load_json, validate_governance_config


@dataclass(frozen=True, slots=True)
class DirectoryObservation:
    """Filesystem facts collected directly from the target host."""

    path: str
    is_directory: bool
    group: str
    mode: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ServerObservation:
    """One target-host OS/filesystem observation."""

    hostname: str
    observed_at: str
    group: str
    group_members: tuple[str, ...]
    available_bytes: int
    directories: tuple[DirectoryObservation, ...]
    capacity_path: str | None = None
    group_error: str | None = None
    capacity_error: str | None = None


class ServerStateProbe(Protocol):
    """Trusted boundary for host-owned group, mode and filesystem facts."""

    def observe(
        self, *, root: Path, group: str, subdirectories: tuple[str, ...]
    ) -> ServerObservation:
        """Collect current facts without accepting caller-supplied observations."""
        ...


class SystemServerStateProbe:
    """Read group membership, stat metadata and free space from the current host."""

    def observe(
        self, *, root: Path, group: str, subdirectories: tuple[str, ...]
    ) -> ServerObservation:
        try:
            group_record = grp.getgrnam(group)
        except KeyError:
            group_record = None
            group_error = f"target host has no Unix group {group!r}"
            members: set[str] = set()
        else:
            group_error = None
            members = set(group_record.gr_mem)
            members.update(
                account.pw_name
                for account in pwd.getpwall()
                if account.pw_gid == group_record.gr_gid
            )
        paths = (root, *(root / item for item in subdirectories))
        observations: list[DirectoryObservation] = []
        for path in paths:
            try:
                metadata = path.stat()
            except OSError as exc:
                observations.append(
                    DirectoryObservation(
                        path=str(path),
                        is_directory=False,
                        group="",
                        mode=0,
                        error=f"cannot stat required server directory {path}: {exc}",
                    )
                )
                continue
            try:
                observed_group = grp.getgrgid(metadata.st_gid).gr_name
            except KeyError:
                observations.append(
                    DirectoryObservation(
                        path=str(path),
                        is_directory=stat.S_ISDIR(metadata.st_mode),
                        group=f"gid:{metadata.st_gid}",
                        mode=stat.S_IMODE(metadata.st_mode),
                        error=f"server directory {path} has unknown group id {metadata.st_gid}",
                    )
                )
                continue
            observations.append(
                DirectoryObservation(
                    path=str(path),
                    is_directory=stat.S_ISDIR(metadata.st_mode),
                    group=observed_group,
                    mode=stat.S_IMODE(metadata.st_mode),
                )
            )

        capacity_path = root
        while not capacity_path.exists() and capacity_path.parent != capacity_path:
            capacity_path = capacity_path.parent
        try:
            available_bytes = shutil.disk_usage(capacity_path).free
        except OSError as exc:
            available_bytes = 0
            capacity_error = (
                f"cannot read filesystem capacity for target {root} via {capacity_path}: {exc}"
            )
        else:
            capacity_error = None
        return ServerObservation(
            hostname=socket.gethostname(),
            observed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            group=group,
            group_members=tuple(sorted(members)),
            available_bytes=available_bytes,
            directories=tuple(observations),
            capacity_path=str(capacity_path),
            group_error=group_error,
            capacity_error=capacity_error,
        )


def server_preflight_report(
    *,
    config_path: Path,
    expected_peak_bytes: int,
    probe: ServerStateProbe | None = None,
) -> CheckReport:
    """Observe and check the current host's namespace, ACL and capacity."""

    loaded = load_json(config_path)
    active_probe = SystemServerStateProbe() if probe is None else probe
    server = cast(dict[str, Any], loaded.value.get("server", {}))
    authorities = cast(list[dict[str, Any]], loaded.value.get("scientific_authorities", []))
    observations: dict[str, ServerObservation] = {}

    def observed() -> ServerObservation:
        if "server" not in observations:
            observations["server"] = active_probe.observe(
                root=Path(cast(str, server["root"])),
                group=cast(str, server["project_group"]),
                subdirectories=tuple(cast(list[str], server["subdirectories"])),
            )
        return observations["server"]

    def config_check() -> CheckEvidence:
        handles = validate_governance_config(loaded.value)
        return CheckEvidence(
            reason="active Governance v2 deployment configuration is structurally valid",
            evidence={"scientific_authorities": list(handles)},
        )

    def namespace_acl() -> CheckEvidence:
        observation = observed()
        expected_members = {
            *(str(item["server_user"]) for item in authorities),
            *cast(list[str], server["service_accounts"]),
        }
        actual_members = set(observation.group_members)
        violations: list[str] = []
        if observation.group_error is not None:
            violations.append(observation.group_error)
        if observation.group != server["project_group"] or actual_members != expected_members:
            violations.append(
                f"target-host writer group mismatch: expected={sorted(expected_members)}, "
                f"actual={sorted(actual_members)}"
            )
        invalid: list[str] = []
        observation_errors: list[str] = []
        modes: dict[str, str] = {}
        for item in observation.directories:
            modes[item.path] = oct(item.mode)
            if item.error is not None:
                observation_errors.append(item.error)
            if (
                item.error is not None
                or not item.is_directory
                or item.group != observation.group
                or not item.mode & stat.S_IWGRP
                or not item.mode & stat.S_ISGID
                or item.mode & 0o007
            ):
                invalid.append(item.path)
        if invalid:
            violations.append(
                "required server directories must be group-owned, group-writable, setgid and "
                f"closed to others: {invalid}"
            )
        violations.extend(observation_errors)
        if violations:
            raise CheckViolation("; ".join(violations))
        return CheckEvidence(
            reason="target-host namespace, Unix group and directory modes match",
            evidence={
                "group": observation.group,
                "group_members": list(observation.group_members),
                "hostname": observation.hostname,
                "modes": modes,
                "observed_at": observation.observed_at,
            },
        )

    def capacity() -> CheckEvidence:
        observation = observed()
        if type(expected_peak_bytes) is not int or expected_peak_bytes < 0:
            raise CheckViolation("expected peak bytes must be a non-negative integer")
        if observation.capacity_error is not None:
            raise CheckViolation(observation.capacity_error)
        if observation.available_bytes <= 0:
            raise CheckViolation("target-host available bytes must be positive")
        fraction = expected_peak_bytes / observation.available_bytes
        if fraction > 0.7:
            raise CheckViolation(f"planned peak {fraction:.3%} exceeds the 70% hard capacity cap")
        if fraction > 0.5:
            raise CheckViolation(
                f"planned peak {fraction:.3%} exceeds the 50% target; reduce it or add space"
            )
        return CheckEvidence(
            reason="planned milestone peak is within the observed 50% capacity target",
            evidence={
                "available_bytes": observation.available_bytes,
                "expected_peak_bytes": expected_peak_bytes,
                "fraction": fraction,
                "hard_cap_fraction": 0.7,
                "hostname": observation.hostname,
                "observed_at": observation.observed_at,
                "observed_filesystem_path": observation.capacity_path,
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
