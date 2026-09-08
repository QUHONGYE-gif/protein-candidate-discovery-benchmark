"""Validity tests for SKEMPI processing; synthetic rows only."""

from __future__ import annotations

import io
import json
import tarfile
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from pcdb.datasets.skempi_readiness import (
    ReadinessError,
    binding_improvement,
    leakage_groups,
    load_residue_map,
    normalize_mutations,
    observable_features,
    prepare,
    render,
    validate_partition_groups,
)


def row(**updates: str) -> dict[str, str]:
    base = {
        "#Pdb": "1ABC_A_B",
        "Mutation(s)_PDB": "WA10F",
        "Mutation(s)_cleaned": "WA9F",
        "Protein 1": "protein one",
        "Protein 2": "protein two",
        "Hold_out_type": "",
        "Hold_out_proteins": "",
        "Affinity_mut (M)": "1e-8",
        "Affinity_wt (M)": "1e-7",
        "Temperature": "298",
        "Reference": "synthetic",
    }
    base.update(updates)
    return base


def test_label_direction_and_units() -> None:
    assert binding_improvement("1e-8", "1e-7", 298) == pytest.approx(1.363485, abs=1e-6)
    assert binding_improvement("1e-7", "1e-7", 298) == 0
    assert binding_improvement("1e-6", "1e-7", 298) < 0


@pytest.mark.parametrize("value", ["<1e-8", ">1e-8", "nan", "inf", "0", "-1", "", "~1e-8"])
def test_censored_or_invalid_affinity_fails(value: str) -> None:
    with pytest.raises(ReadinessError):
        binding_improvement(value, "1e-7", 298)


def test_mutation_chain_insertion_and_permutation() -> None:
    assert normalize_mutations("WA10AF,KA-1R", "1ABC_A_B") == ("KA-1R", "WA10AF")
    for value in ("WC1F", "WA1W", "WA1F,WA1G", "WA?F"):
        with pytest.raises(ReadinessError):
            normalize_mutations(value, "1ABC_A_B")


def test_rebuild_invariant_to_row_order_and_hidden_labels() -> None:
    rows = [row(), row(**{"Mutation(s)_PDB": "KA12R", "Mutation(s)_cleaned": "KA11R"})]
    a, b = prepare(rows), prepare(list(reversed(rows)))
    assert render(a, "a" * 64, "b" * 40) == render(b, "a" * 64, "b" * 40)
    changed = deepcopy(rows)
    for item in changed:
        item["Affinity_mut (M)"] = "1e-4"
    c = prepare(changed)
    assert a.candidates == c.candidates
    assert a.membership == c.membership
    assert [observable_features(x) for x in a.candidates] == [
        observable_features(x) for x in c.candidates
    ]
    assert a.truth != c.truth


def test_duplicate_median_and_exact_row_weight() -> None:
    a = row()
    b = row(**{"Affinity_mut (M)": "1e-6", "Reference": "synthetic second"})
    result = prepare([a, a, b])
    assert result.truth[0].target == pytest.approx(0, abs=1e-10)
    assert result.report["merged_duplicate_rows"] == 2
    assert result.report["source_rows_accounted"] is True


def test_temperature_and_mapping_exclusions_accounted() -> None:
    keep = row(**{"Mutation(s)_PDB": "KA12R", "Mutation(s)_cleaned": "KA11R"})
    result = prepare([row(), row(Temperature="300"), row(Temperature="298(assumed)"), keep])
    assert len(result.candidates) == 1
    assert result.report["excluded_rows"] == 3
    assert result.report["source_rows_accounted"] is True


def test_holdout_transitivity_shared_protein_and_virtual_links() -> None:
    a = row(**{"Hold_out_proteins": "2BCD_A_B"})
    b = row(**{"#Pdb": "2BCD_A_B", "Protein 1": "other", "Protein 2": "third"})
    c = row(**{"#Pdb": "3CDE_A_B", "Protein 1": " THIRD ", "Protein 2": "fourth"})
    assert len(set(leakage_groups([a, b, c]).values())) == 1
    a["Hold_out_proteins"] = "4DEF_A_B"
    b["Hold_out_proteins"] = "4DEF_A_B"
    assert len(set(leakage_groups([a, b]).values())) == 1
    a["Hold_out_proteins"] = "unresolved text"
    with pytest.raises(ReadinessError):
        leakage_groups([a, b])


def test_groups_constructed_before_affinity_filtering() -> None:
    bridge = row(
        **{
            "#Pdb": "2BCD_A_B",
            "Protein 1": "protein one",
            "Protein 2": "third",
            "Affinity_mut (M)": ">1e-8",
        }
    )
    other = row(**{"#Pdb": "3CDE_A_B", "Protein 1": "third", "Protein 2": "fourth"})
    result = prepare([row(), bridge, other])
    assert len(result.candidates) == 2
    assert len({g for _, g in result.membership}) == 1


def test_group_partition_leakage_fails_closed() -> None:
    with pytest.raises(ReadinessError):
        validate_partition_groups([("a", "g", "train"), ("b", "g", "held-out")])
    with pytest.raises(ReadinessError):
        validate_partition_groups([("a", "g", "train"), ("a", "g", "train")])


def test_existing_manifest_schemas_and_observable_contract() -> None:
    output = render(prepare([row()]), "a" * 64, "b" * 40)
    repository = Path(__file__).resolve().parents[2]
    for name in ("processing", "split"):
        schema = json.loads((repository / "schemas" / f"{name}-manifest.schema.json").read_text())
        Draft202012Validator(schema).validate(json.loads(output[f"{name}-manifest.json"]))
    candidate = json.loads(output["candidates.json"])[0]
    assert set(candidate) == {"candidate_id", "complex_id", "mutations", "cleaned_mutations"}
    assert len(json.loads(output["features.json"])[0]["values"]) == 41
    assert json.loads(output["split-manifest.json"])["held_out_partitions"] == []


def test_original_cleaned_mapping_and_wildtype(tmp_path: Path) -> None:
    archive = tmp_path / "structures.tgz"
    members = {
        "PDBs/1ABC.mapping": b"TRP A  10  9\nLYS A 12 11\n",
        "PDBs/1ABC.pdb": (
            b"ATOM      1  CA  TRP A   9      16.783  48.812  26.416  1.00 30.15           C  \n"
            b"ATOM      2  CA  ARG A  11      16.783  48.812  26.416  1.00 30.15           C  \n"
        ),
    }
    with tarfile.open(archive, "w:gz") as bundle:
        for name, data in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
    mappings = load_residue_map(archive)
    assert mappings == {("1ABC", "A", "10"): ("W", "9")}
    result = prepare(
        [row(), row(**{"Mutation(s)_PDB": "KA12R", "Mutation(s)_cleaned": "KA11R"})], mappings
    )
    assert len(result.candidates) == 1
    assert result.report["excluded_rows"] == 1
    assert result.report["structure_mapping"] == "PUBLISHER_MAPPING_AND_WILDTYPE_CHECKED"


def test_accepted_manifest_required_before_data_read(tmp_path: Path) -> None:
    from pcdb.datasets.skempi_readiness import build

    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"raw_files": []}')
    with pytest.raises(ReadinessError, match="accepted SKEMPI"):
        build(tmp_path / "does-not-exist", manifest, "a" * 40)
