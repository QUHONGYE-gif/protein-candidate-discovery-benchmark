"""Deterministic SKEMPI processing and conservative external-pool grouping.

This module prepares data. It performs no acquisition, high-value calculation or
scientific evaluation. Only ObservableCandidate may be passed to feature code.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import statistics
import tarfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

AA = "ACDEFGHIKLMNPQRSTVWY"
MUTATION = re.compile(
    r"([ACDEFGHIKLMNPQRSTVWY])([A-Za-z0-9])(-?\d+[A-Za-z]?)([ACDEFGHIKLMNPQRSTVWY])"
)
COMPLEX = re.compile(r"([0-9][A-Za-z0-9]{3})_([A-Za-z0-9]+)_([A-Za-z0-9]+)")
NUMBER = re.compile(r"[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
TYPES = {"Pr/PI", "AB/AG", "TCR/pMHC"}
THREE_TO_ONE = dict(
    zip(
        [
            "ALA",
            "CYS",
            "ASP",
            "GLU",
            "PHE",
            "GLY",
            "HIS",
            "ILE",
            "LYS",
            "LEU",
            "MET",
            "ASN",
            "PRO",
            "GLN",
            "ARG",
            "SER",
            "THR",
            "VAL",
            "TRP",
            "TYR",
        ],
        AA,
        strict=True,
    )
)
ResidueMap = dict[tuple[str, str, str], tuple[str, str]]
REQUIRED = {
    "#Pdb",
    "Mutation(s)_PDB",
    "Mutation(s)_cleaned",
    "Protein 1",
    "Protein 2",
    "Hold_out_type",
    "Hold_out_proteins",
    "Affinity_mut (M)",
    "Affinity_wt (M)",
    "Temperature",
}
CONTRACT: dict[str, object] = {
    "version": "m5d-skempi-readiness-v1",
    "target": "-(8.314/4184)*kelvin*ln(Kd_mut/Kd_wt)",
    "direction": "higher_is_better",
    "temperature": "explicit_numeric_kelvin_250_to_400_no_imputation",
    "affinities": "exact_positive_finite_original_M_fields_no_censoring",
    "duplicates": "same_complex_original_mutation_same_temperature_median_target",
    "multiple_temperatures": "exclude_entire_candidate",
    "grouping": "all_source_rows_pdb_shared_protein_name_holdout_link_type_components",
    "partition": "external-candidate",
    "features": "mutation_residue_counts_40_plus_mutation_count",
    "mapping": "publisher_mapping_and_cleaned_pdb_wildtype_check",
    "feasibility_pool_sizes": [48, 80],
    "formal_held_out": False,
}


class ReadinessError(ValueError):
    """Identity or contract failure; never replace with partial scientific evidence."""


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_complex(value: str) -> str:
    match = COMPLEX.fullmatch(value.strip())
    if not match:
        raise ReadinessError("invalid complex syntax")
    pdb, first, second = match.groups()
    if len(set(first + second)) != len(first + second):
        raise ReadinessError("duplicate or overlapping partner chains")
    return pdb.upper() + "_" + "".join(sorted(first)) + "_" + "".join(sorted(second))


def normalize_mutations(value: str, complex_id: str) -> tuple[str, ...]:
    chains = "".join(complex_id.split("_")[1:])
    result = []
    sites = set()
    for token in value.split(","):
        token = token.strip()
        match = MUTATION.fullmatch(token)
        if not match:
            raise ReadinessError("invalid mutation syntax")
        wild, chain, position, mutant = match.groups()
        if chain not in chains or wild == mutant or (chain, position) in sites:
            raise ReadinessError("invalid mutation chain, duplicate site or no-op")
        sites.add((chain, position))
        result.append(wild + chain + position + mutant)
    return tuple(sorted(result))


@dataclass(frozen=True)
class ObservableCandidate:
    candidate_id: str
    complex_id: str
    mutations: tuple[str, ...]
    cleaned_mutations: tuple[str, ...]


@dataclass(frozen=True)
class HiddenTarget:
    candidate_id: str
    target: float


@dataclass(frozen=True)
class Prepared:
    candidates: tuple[ObservableCandidate, ...]
    truth: tuple[HiddenTarget, ...]
    membership: tuple[tuple[str, str], ...]
    provenance: tuple[dict[str, object], ...]
    report: dict[str, object]


def observable_features(candidate: ObservableCandidate) -> tuple[int, ...]:
    """41 mutation-only features; no truth/temperature/provenance argument."""
    wild = Counter(token[0] for token in candidate.mutations)
    mutant = Counter(token[-1] for token in candidate.mutations)
    return (
        tuple(wild[aa] for aa in AA) + tuple(mutant[aa] for aa in AA) + (len(candidate.mutations),)
    )


def load_residue_map(archive: Path) -> ResidueMap:
    """Read publisher mapping and residue names, never structural feature values."""
    mappings: ResidueMap = {}
    residues: dict[tuple[str, str, str], str] = {}
    seen = set()
    total = 0
    with tarfile.open(archive, "r|gz") as bundle:
        for member in bundle:
            if not member.isfile() or Path(member.name).name.startswith("._"):
                continue
            if member.name in seen:
                raise ReadinessError("duplicate archive member")
            seen.add(member.name)
            total += member.size
            if total > 512 * 1024 * 1024:
                raise ReadinessError("uncompressed structure input cap exceeded")
            pdb = Path(member.name).stem.upper()
            handle = bundle.extractfile(member)
            if handle is None:
                raise ReadinessError("unreadable archive member")
            with handle:
                for raw in handle:
                    line = raw.decode("ascii")
                    if member.name.endswith(".mapping"):
                        match = re.fullmatch(
                            r"([A-Z]{3})\s+([A-Za-z0-9])\s*(-?\d+[A-Za-z]?)\s+(-?\d+[A-Za-z]?)\s*",
                            line,
                        )
                        if match is None:
                            raise ReadinessError("unexpected publisher mapping syntax")
                        residue, chain, original, cleaned = match.groups()
                        if residue not in THREE_TO_ONE:
                            continue
                        key = (pdb, chain, original)
                        value = (THREE_TO_ONE[residue], cleaned)
                        if key in mappings and mappings[key] != value:
                            raise ReadinessError("ambiguous publisher residue mapping")
                        mappings[key] = value
                    elif member.name.endswith(".pdb") and line.startswith(("ATOM  ", "HETATM")):
                        residue = line[17:20].strip()
                        if residue not in THREE_TO_ONE:
                            continue
                        key = (pdb, line[21], line[22:27].strip())
                        value_aa = THREE_TO_ONE[residue]
                        if key in residues and residues[key] != value_aa:
                            raise ReadinessError("ambiguous cleaned PDB residue")
                        residues[key] = value_aa
    return {
        key: value
        for key, value in mappings.items()
        if residues.get((key[0], key[1], value[1])) == value[0]
    }


def validate_residue_mapping(candidate: ObservableCandidate, mappings: ResidueMap) -> None:
    translated = []
    for token in candidate.mutations:
        value = mappings.get((candidate.complex_id[:4], token[1], token[2:-1]))
        if value is None or value[0] != token[0]:
            raise ReadinessError("missing_or_mismatched_publisher_structure_mapping")
        translated.append(token[0] + token[1] + value[1] + token[-1])
    if tuple(sorted(translated)) != candidate.cleaned_mutations:
        raise ReadinessError("original_to_cleaned_position_mismatch")


def positive_exact(value: str) -> float:
    if not NUMBER.fullmatch(value.strip()):
        raise ReadinessError("missing_censored_or_ambiguous_affinity")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ReadinessError("nonpositive_or_nonfinite_affinity")
    return number


def temperature(value: str) -> float:
    if not NUMBER.fullmatch(value.strip()):
        raise ReadinessError("missing_or_assumed_temperature")
    number = float(value)
    if not 250 <= number <= 400:
        raise ReadinessError("temperature_outside_kelvin_contract")
    return number


def binding_improvement(mutant: str, wild_type: str, kelvin: float) -> float:
    # Subtract logs to avoid overflow of a finite-positive ratio.
    value = (
        -(8.314 / 4184)
        * kelvin
        * (math.log(positive_exact(mutant)) - math.log(positive_exact(wild_type)))
    )
    if not math.isfinite(value):
        raise ReadinessError("nonfinite_derived_label")
    return value


def _name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def leakage_groups(rows: list[dict[str, str]]) -> dict[str, str]:
    """Build before filtering by label validity; preserve virtual publisher links."""
    parent: dict[str, str] = {}

    def root(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: str, b: str) -> None:
        left, right = sorted((root(a), root(b)))
        parent[right] = left

    complexes = set()
    for row in rows:
        complex_id = normalize_complex(row["#Pdb"])
        complexes.add(complex_id)
        node = "complex:" + complex_id
        union(node, "pdb:" + complex_id[:4])
        for field in ("Protein 1", "Protein 2"):
            name = _name(row[field])
            if not name:
                raise ReadinessError("missing protein identity; cannot guarantee grouping")
            union(node, "protein:" + name)
        for field in ("Hold_out_type", "Hold_out_proteins"):
            for token in filter(None, (v.strip() for v in row[field].split(","))):
                if token in TYPES:
                    union(node, "type:" + token)
                else:
                    linked = normalize_complex(token)  # Unknown syntax is a hard failure.
                    union(node, "complex:" + linked)
                    union("complex:" + linked, "pdb:" + linked[:4])
    components: dict[str, list[str]] = defaultdict(list)
    for complex_id in sorted(complexes):
        components[root("complex:" + complex_id)].append(complex_id)
    ids = {key: "skempi-group-" + sha(canonical(values)) for key, values in components.items()}
    return {c: ids[root("complex:" + c)] for c in sorted(complexes)}


def validate_partition_groups(membership: list[tuple[str, str, str]]) -> None:
    groups: dict[str, str] = {}
    seen = set()
    for candidate_id, group, partition in membership:
        if candidate_id in seen or (group in groups and groups[group] != partition):
            raise ReadinessError("duplicate candidate or leakage group crosses partitions")
        seen.add(candidate_id)
        groups[group] = partition


def prepare(rows: list[dict[str, str]], mappings: ResidueMap | None = None) -> Prepared:
    if not rows or any(not row.keys() >= REQUIRED for row in rows):
        raise ReadinessError("empty source or missing required source columns")
    groups = leakage_groups(rows)
    exclusions: Counter[str] = Counter()
    buckets: dict[str, list[tuple[ObservableCandidate, float, float, str]]] = defaultdict(list)
    # Input row order does not affect provenance or any output identity.
    for row in rows:
        try:
            complex_id = normalize_complex(row["#Pdb"])
            mutations = normalize_mutations(row["Mutation(s)_PDB"], complex_id)
            cleaned = normalize_mutations(row["Mutation(s)_cleaned"], complex_id)
            if sorted((m[0], m[1], m[-1]) for m in mutations) != sorted(
                (m[0], m[1], m[-1]) for m in cleaned
            ):
                raise ReadinessError("original_cleaned_substitution_mismatch")
            candidate_id = "skempi-" + sha(canonical([complex_id, mutations]))
            candidate = ObservableCandidate(candidate_id, complex_id, mutations, cleaned)
            if mappings is not None:
                validate_residue_mapping(candidate, mappings)
            kelvin = temperature(row["Temperature"])
            target = binding_improvement(row["Affinity_mut (M)"], row["Affinity_wt (M)"], kelvin)
        except ReadinessError as error:
            exclusions[str(error)] += 1
            continue
        buckets[candidate_id].append((candidate, kelvin, target, sha(canonical(row))))

    candidates = []
    truth = []
    provenance = []
    merged_rows = 0
    for candidate_id, records in sorted(buckets.items()):
        if len({r[1] for r in records}) != 1:
            exclusions["candidate_multiple_temperatures"] += len(records)
            continue
        if len({r[0] for r in records}) != 1:
            exclusions["candidate_cleaned_mapping_conflict"] += len(records)
            continue
        candidate = records[0][0]
        candidates.append(candidate)
        # Exact duplicate source rows do not gain extra weight in a median.
        unique = {record[3]: record for record in records}
        target = statistics.median(sorted(r[2] for r in unique.values()))
        truth.append(HiddenTarget(candidate_id, target))
        provenance.append(
            {
                "candidate_id": candidate_id,
                "source_row_hashes": sorted(unique),
                "source_rows": len(records),
                "kelvin": records[0][1],
            }
        )
        merged_rows += len(records) - 1
    if not candidates:
        raise ReadinessError("no eligible candidate after declared processing")
    membership = tuple((c.candidate_id, groups[c.complex_id]) for c in candidates)
    validate_partition_groups([(c, g, "external-candidate") for c, g in membership])
    if [c.candidate_id for c in candidates] != [t.candidate_id for t in truth]:
        raise ReadinessError("candidate/truth exact cover failed")
    pool_sizes = Counter(c.complex_id for c in candidates)
    feasibility = {}
    for size in (48, 80):
        eligible = [c for c, n in pool_sizes.items() if n >= size]
        feasibility[str(size)] = {
            "complexes": len(eligible),
            "leakage_groups": len({groups[c] for c in eligible}),
        }
    report: dict[str, object] = {
        "source_rows": len(rows),
        "candidate_count": len(candidates),
        "excluded_rows": sum(exclusions.values()),
        "exclusion_reasons": dict(sorted(exclusions.items())),
        "merged_duplicate_rows": merged_rows,
        "complex_count": len(pool_sizes),
        "leakage_group_count": len({g for _, g in membership}),
        "feasibility": feasibility,
        "source_rows_accounted": len(candidates) + merged_rows + sum(exclusions.values())
        == len(rows),
        "candidate_truth_exact_cover": True,
        "cross_partition_group_count": 0,
        "formal_split": False,
        "model_fit_count": 0,
        "high_value_calculation_count": 0,
        "scientific_result": "NOT_EVALUATED",
        "structure_mapping": "PUBLISHER_MAPPING_AND_WILDTYPE_CHECKED"
        if mappings is not None
        else "SYNTHETIC_TEST_ONLY",
    }
    if report["source_rows_accounted"] is not True:
        raise ReadinessError("source row accounting failure")
    return Prepared(tuple(candidates), tuple(truth), membership, tuple(provenance), report)


def csv_bytes(header: list[str], rows: list[list[object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue().encode()


def render(prepared: Prepared, manifest_sha: str, git_commit: str) -> dict[str, bytes]:
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha) or not re.fullmatch(
        r"[0-9a-f]{40}", git_commit
    ):
        raise ReadinessError("invalid source manifest or Git identity")
    contract_sha = sha(canonical(CONTRACT))
    identity = "skempi-readiness-" + sha(canonical([manifest_sha, contract_sha, git_commit]))
    payloads = {
        "candidates.json": canonical([asdict(c) for c in prepared.candidates]),
        "hidden-truth.json": canonical([asdict(t) for t in prepared.truth]),
        "features.json": canonical(
            [
                {"candidate_id": c.candidate_id, "values": observable_features(c)}
                for c in prepared.candidates
            ]
        ),
        "source-provenance.json": canonical(prepared.provenance),
        "processing-audit.json": canonical(prepared.report),
        "membership.csv": csv_bytes(
            ["candidate_id", "partition", "group_id"],
            [[c, "external-candidate", g] for c, g in prepared.membership],
        ),
        "processing-contract.json": canonical(CONTRACT),
    }

    def artifact(name: str) -> dict[str, object]:
        return {
            "object_key": identity + "/" + name,
            "sha256": sha(payloads[name]),
            "size_bytes": len(payloads[name]),
        }

    limitations = [
        "Readiness only; no formal held-out allocation or scientific result.",
        "Grouping uses names and publisher links; sequence homology was not independently audited.",
        "Publisher mapping and wild-type identity checked; no structural features or repair.",
    ]
    processing = {
        "schema_version": "1",
        "processing_id": identity,
        "dataset_manifest_sha256": manifest_sha,
        "processing_config_sha256": contract_sha,
        "git_commit": git_commit,
        "candidate_id_field": "candidate_id",
        "candidate_id_unique": True,
        "observable_feature_fields": ["complex_id", "mutations", "cleaned_mutations"],
        "hidden_label_field": "target",
        "label_direction": "higher_is_better",
        "duplicate_policy": {
            "exact_duplicates": "aggregate",
            "near_duplicates": "grouped",
            "near_duplicate_method": "conservative_publisher_link_components",
        },
        "output_files": [artifact(n) for n in sorted(payloads)],
        "known_limitations": limitations,
    }
    payloads["processing-manifest.json"] = canonical(processing)
    payloads["split-manifest.json"] = canonical(
        {
            "schema_version": "1",
            "split_id": identity + "-groups",
            "protocol_version": CONTRACT["version"],
            "processing_manifest_sha256": sha(payloads["processing-manifest.json"]),
            "split_config_sha256": contract_sha,
            "git_commit": git_commit,
            "membership_file": artifact("membership.csv"),
            "audit_file": artifact("processing-audit.json"),
            "partitions": ["external-candidate"],
            "held_out_partitions": [],
            "grouping_unit": "publisher_link_and_shared_protein_component",
            "leakage_controls": ["whole_components_only", "groups_built_before_label_filtering"],
            "known_limitations": limitations,
        }
    )
    return payloads


def build(raw_root: Path, manifest_path: Path, git_commit: str) -> dict[str, bytes]:
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if sha(manifest_bytes) != "773d714ad10bbce46d3791da2bbd5feb75377239fb39a7785421bb1a014536ee":
        raise ReadinessError("not the accepted SKEMPI Dataset Manifest")
    for item in manifest["raw_files"]:
        path = raw_root / Path(item["object_key"]).name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item["size_bytes"]:
            raise ReadinessError("raw size or file type mismatch")
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise ReadinessError("raw SHA-256 mismatch")
    with (raw_root / "skempi_v2.csv").open(newline="", encoding="utf-8-sig") as handle:
        rows = cast(list[dict[str, str]], list(csv.DictReader(handle, delimiter=";")))
    mappings = load_residue_map(raw_root / "SKEMPI2_PDBs.tgz")
    return render(prepare(rows, mappings), sha(manifest_bytes), git_commit)
