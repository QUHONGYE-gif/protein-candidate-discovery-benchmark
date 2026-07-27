"""Reproduce the bounded, non-production Issue #28 Schema v2 design evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "experiment-v2.prototype.schema.json"
CONFIG_PATHS = {
    "alpha": ROOT / "alpha.prototype.json",
    "ired": ROOT / "ired.prototype.json",
}
FIXTURE_PATH = ROOT / "synthetic-fixtures.json"
EXPECTED_PATH = ROOT / "expected-results.json"

MANIFEST_KEYS = (
    "dataset_manifest_sha256",
    "processing_manifest_sha256",
    "split_sha256",
)
EXPECTED_TASK_BINDINGS = {
    "alpha": {
        "task_id": "alpha-amylase",
        "dataset_manifest_sha256": (
            "c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac"
        ),
        "processing_manifest_sha256": (
            "cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec"
        ),
        "split_sha256": "c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a",
        "candidate_role": "candidate-pool",
        "frozen_candidate_count": 2262,
    },
    "ired": {
        "task_id": "ired",
        "dataset_manifest_sha256": (
            "37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643"
        ),
        "processing_manifest_sha256": (
            "929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba"
        ),
        "split_sha256": "9bff199ec81b9b5b1e0548c237b923b2ac7db1bf36382377ed36d30c042c725e",
        "candidate_role": "candidate-pool",
        "frozen_candidate_count": 2070,
    },
}


def load_json(path: Path) -> Any:
    """Load one exact UTF-8 JSON file while rejecting non-standard constants."""

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant is forbidden: {value}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)


def canonical_json_bytes(value: Any) -> bytes:
    """Return the proposal's explicit canonical semantic representation."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def validate_definition_domain(config: dict[str, Any]) -> None:
    evaluation = cast(dict[str, Any], config["evaluation"])
    definitions = cast(list[dict[str, Any]], evaluation["high_value_definitions"])
    definition_ids = [str(item["definition_id"]) for item in definitions]
    if len(definition_ids) != len(set(definition_ids)):
        raise ValueError("high-value definition IDs must be unique")

    primary_id = str(evaluation["primary_high_value_definition_id"])
    primary_definitions = [item for item in definitions if item["purpose"] == "primary"]
    if len(primary_definitions) != 1:
        raise ValueError("exactly one high-value definition must have purpose=primary")
    if str(primary_definitions[0]["definition_id"]) != primary_id:
        raise ValueError("the unique primary definition must match the primary ID")

    candidate_universes = {
        (str(item["candidate_role"]), int(item["frozen_candidate_count"])) for item in definitions
    }
    if len(candidate_universes) != 1:
        raise ValueError("all high-value definitions must use one frozen candidate universe")

    for definition in definitions:
        derivation = cast(dict[str, Any], definition.get("derivation", {}))
        candidate_count = int(definition["frozen_candidate_count"])
        if definition["kind"] == "quantile":
            fraction = float(derivation["fraction"])
            nominal_k = int(derivation["nominal_k"])
            if float(definition["value"]) != fraction:
                raise ValueError("quantile value must equal derivation fraction")
            if math.ceil(fraction * candidate_count) != nominal_k:
                raise ValueError("quantile nominal K does not match ceil derivation")
        elif definition["kind"] == "top_k":
            nominal_k = int(derivation["nominal_k"])
            if int(definition["value"]) != nominal_k:
                raise ValueError("fixed Top-k value must equal nominal K")
            if nominal_k > candidate_count:
                raise ValueError("fixed Top-k cannot exceed the frozen candidate count")


def validate_task_binding(task: str, config: dict[str, Any]) -> None:
    """Bind one prototype file to its exact task, manifest chain, role, and pool size."""

    expected = EXPECTED_TASK_BINDINGS[task]
    for key in ("task_id", *MANIFEST_KEYS):
        if config[key] != expected[key]:
            raise ValueError(f"{task} {key} does not match the accepted task binding")

    definitions = cast(
        list[dict[str, Any]],
        cast(dict[str, Any], config["evaluation"])["high_value_definitions"],
    )
    expected_universe = (
        str(expected["candidate_role"]),
        int(expected["frozen_candidate_count"]),
    )
    actual_universes = {
        (str(item["candidate_role"]), int(item["frozen_candidate_count"])) for item in definitions
    }
    if actual_universes != {expected_universe}:
        raise ValueError(f"{task} definitions do not match the accepted candidate universe")


def normalized_rows(rows: list[list[Any]]) -> list[tuple[str, float]]:
    parsed: list[tuple[str, float]] = []
    seen: set[str] = set()
    for raw_candidate_id, raw_value in rows:
        candidate_id = str(raw_candidate_id)
        if candidate_id in seen:
            raise ValueError("duplicate candidate ID")
        seen.add(candidate_id)
        if raw_value is None or isinstance(raw_value, str):
            raise ValueError("missing or non-finite label")
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError("missing or non-finite label")
        parsed.append((candidate_id, value))
    return parsed


def top_k_tie_inclusive(rows: list[list[Any]], nominal_k: int) -> dict[str, Any]:
    parsed = normalized_rows(rows)
    if nominal_k < 1 or nominal_k > len(parsed):
        raise ValueError("nominal K must be within the candidate universe")
    ranked = sorted(parsed, key=lambda item: (-item[1], item[0]))
    boundary_score = ranked[nominal_k - 1][1]
    selected = [item for item in ranked if item[1] >= boundary_score]
    boundary_tie_count = sum(item[1] == boundary_score for item in ranked)
    return {
        "nominal_k": nominal_k,
        "actual_high_value_count": len(selected),
        "boundary_score": boundary_score,
        "boundary_tie_count": boundary_tie_count,
        "selected_candidate_ids": [item[0] for item in selected],
    }


def threshold_members(
    rows: list[list[Any]],
    *,
    kind: str,
    value: float,
    wt_reference: float = 0.0,
) -> list[str]:
    parsed = normalized_rows(rows)
    optimum = max(label for _, label in parsed)
    if kind == "absolute":
        threshold = value
    elif kind == "wt_relative":
        threshold = wt_reference + value
    elif kind == "near_optimal":
        threshold = optimum - value
    else:
        raise ValueError(f"unsupported threshold kind: {kind}")
    return sorted(candidate_id for candidate_id, label in parsed if label >= threshold)


def hit_probability(candidate_count: int, positive_count: int, budget: int) -> float:
    if not 0 < positive_count <= candidate_count:
        raise ValueError("positive count must be within candidate universe")
    if not 0 <= budget <= candidate_count:
        raise ValueError("budget must be within candidate universe")
    if budget > candidate_count - positive_count:
        return 1.0
    miss = math.comb(candidate_count - positive_count, budget) / math.comb(candidate_count, budget)
    return 1.0 - miss


def assert_schema_rejects_drift(
    schema: dict[str, Any], validator: Draft202012Validator, config: dict[str, Any]
) -> dict[str, bool]:
    invalid_cases: dict[str, dict[str, Any]] = {}

    unknown_field = copy.deepcopy(config)
    unknown_field["evaluation"]["high_value_definitions"][0]["unknown"] = True
    invalid_cases["unknown_field"] = unknown_field

    production_marker = copy.deepcopy(config)
    production_marker["proposal_status"] = "accepted"
    invalid_cases["production_marker"] = production_marker

    held_out = copy.deepcopy(config)
    held_out["held_out_authorized"] = True
    invalid_cases["held_out_authorized"] = held_out

    lower_is_better = copy.deepcopy(config)
    lower_is_better["evaluation"]["high_value_definitions"][0]["ranking_policy"][
        "label_direction"
    ] = "lower_is_better"
    invalid_cases["lower_is_better"] = lower_is_better

    top_k_fraction = copy.deepcopy(config)
    definition = top_k_fraction["evaluation"]["high_value_definitions"][0]
    definition["kind"] = "top_k"
    definition["value"] = definition["derivation"]["nominal_k"]
    invalid_cases["top_k_with_fraction"] = top_k_fraction

    top_k_rounding = copy.deepcopy(top_k_fraction)
    del top_k_rounding["evaluation"]["high_value_definitions"][0]["derivation"]["fraction"]
    invalid_cases["top_k_with_rounding"] = top_k_rounding

    absolute_with_quantile_derivation = copy.deepcopy(config)
    definition = absolute_with_quantile_derivation["evaluation"]["high_value_definitions"][0]
    definition["kind"] = "absolute"
    definition["value"] = 1.0
    invalid_cases["absolute_with_quantile_derivation"] = absolute_with_quantile_derivation

    rejected: dict[str, bool] = {}
    for name, invalid in invalid_cases.items():
        try:
            validator.validate(invalid)
        except ValidationError:
            rejected[name] = True
            continue
        raise AssertionError(f"prototype Schema unexpectedly accepted {name}")

    Draft202012Validator.check_schema(schema)
    return rejected


def assert_domain_rejects_drift(
    validator: Draft202012Validator, config: dict[str, Any]
) -> dict[str, bool]:
    invalid_cases: dict[str, dict[str, Any]] = {}

    duplicate_id = copy.deepcopy(config)
    definitions = duplicate_id["evaluation"]["high_value_definitions"]
    definitions[1]["definition_id"] = definitions[0]["definition_id"]
    invalid_cases["duplicate_definition_id"] = duplicate_id

    wrong_nominal_k = copy.deepcopy(config)
    wrong_nominal_k["evaluation"]["high_value_definitions"][0]["derivation"]["nominal_k"] += 1
    invalid_cases["wrong_quantile_nominal_k"] = wrong_nominal_k

    primary_as_sensitivity = copy.deepcopy(config)
    primary_as_sensitivity["evaluation"]["high_value_definitions"][0]["purpose"] = "sensitivity"
    invalid_cases["missing_primary"] = primary_as_sensitivity

    multiple_primary = copy.deepcopy(config)
    multiple_primary["evaluation"]["high_value_definitions"][1]["purpose"] = "primary"
    invalid_cases["multiple_primary"] = multiple_primary

    sensitivity_replaces_primary = copy.deepcopy(config)
    sensitivity_replaces_primary["evaluation"]["primary_high_value_definition_id"] = (
        sensitivity_replaces_primary["evaluation"]["high_value_definitions"][1]["definition_id"]
    )
    invalid_cases["sensitivity_replaces_primary"] = sensitivity_replaces_primary

    definitions_use_different_pools = copy.deepcopy(config)
    definitions_use_different_pools["evaluation"]["high_value_definitions"][1][
        "frozen_candidate_count"
    ] = 999
    fraction = float(
        definitions_use_different_pools["evaluation"]["high_value_definitions"][1]["derivation"][
            "fraction"
        ]
    )
    definitions_use_different_pools["evaluation"]["high_value_definitions"][1]["derivation"][
        "nominal_k"
    ] = math.ceil(999 * fraction)
    invalid_cases["definitions_use_different_pools"] = definitions_use_different_pools

    top_k_exceeds_pool = copy.deepcopy(config)
    definition = top_k_exceeds_pool["evaluation"]["high_value_definitions"][0]
    definition["kind"] = "top_k"
    definition["value"] = int(definition["frozen_candidate_count"]) + 1
    definition["derivation"] = {"nominal_k": definition["value"]}
    invalid_cases["top_k_exceeds_pool"] = top_k_exceeds_pool

    rejected: dict[str, bool] = {}
    for name, invalid in invalid_cases.items():
        validator.validate(invalid)
        try:
            validate_definition_domain(invalid)
        except ValueError:
            rejected[name] = True
            continue
        raise AssertionError(f"prototype domain validator unexpectedly accepted {name}")
    return rejected


def assert_task_binding_rejects_drift(
    task: str, validator: Draft202012Validator, config: dict[str, Any]
) -> dict[str, bool]:
    invalid_cases: dict[str, dict[str, Any]] = {}
    other_task = "ired" if task == "alpha" else "alpha-amylase"

    wrong_task_id = copy.deepcopy(config)
    wrong_task_id["task_id"] = other_task
    invalid_cases[f"{task}_chain_declared_as_other_task"] = wrong_task_id

    for key in MANIFEST_KEYS:
        wrong_manifest = copy.deepcopy(config)
        wrong_manifest[key] = "0" * 64
        invalid_cases[f"wrong_{key}"] = wrong_manifest

    wrong_candidate_universe = copy.deepcopy(config)
    for definition in wrong_candidate_universe["evaluation"]["high_value_definitions"]:
        definition["frozen_candidate_count"] += 1
        if definition["kind"] == "quantile":
            definition["derivation"]["nominal_k"] = math.ceil(
                float(definition["derivation"]["fraction"])
                * int(definition["frozen_candidate_count"])
            )
    invalid_cases["wrong_frozen_candidate_universe"] = wrong_candidate_universe

    rejected: dict[str, bool] = {}
    for name, invalid in invalid_cases.items():
        validator.validate(invalid)
        validate_definition_domain(invalid)
        try:
            validate_task_binding(task, invalid)
        except ValueError:
            rejected[name] = True
            continue
        raise AssertionError(f"task binding unexpectedly accepted {name}")
    return rejected


def assert_option_family_coverage(
    validator: Draft202012Validator, config: dict[str, Any]
) -> dict[str, bool]:
    examples: dict[str, dict[str, Any]] = {}
    base = copy.deepcopy(config)

    top_k = copy.deepcopy(base)
    top_k_definition = top_k["evaluation"]["high_value_definitions"][0]
    top_k_definition["kind"] = "top_k"
    top_k_definition["value"] = top_k_definition["derivation"]["nominal_k"]
    del top_k_definition["derivation"]["fraction"]
    del top_k_definition["derivation"]["rounding"]
    examples["top_k"] = top_k

    absolute = copy.deepcopy(base)
    absolute_definition = absolute["evaluation"]["high_value_definitions"][0]
    absolute_definition["kind"] = "absolute"
    absolute_definition["value"] = 1.0
    del absolute_definition["derivation"]
    examples["absolute"] = absolute

    wt_relative = copy.deepcopy(base)
    wt_definition = wt_relative["evaluation"]["high_value_definitions"][0]
    wt_definition["kind"] = "wt_relative"
    wt_definition["value"] = 0.5
    del wt_definition["derivation"]
    wt_definition["reference_policy"] = {
        "reference_role": "wild-type",
        "reference_value_source": "hidden-oracle",
        "gap_interpretation": "additive",
    }
    examples["wt_relative"] = wt_relative

    near_optimal = copy.deepcopy(base)
    near_definition = near_optimal["evaluation"]["high_value_definitions"][0]
    near_definition["kind"] = "near_optimal"
    near_definition["value"] = 0.5
    del near_definition["derivation"]
    near_definition["reference_policy"] = {
        "reference_role": "task-optimum",
        "reference_value_source": "hidden-oracle",
        "gap_interpretation": "additive",
    }
    examples["near_optimal"] = near_optimal

    results = {"quantile": True}
    for name, example in examples.items():
        validator.validate(example)
        validate_definition_domain(example)
        results[name] = True
    return results


def calculate_results() -> dict[str, Any]:
    schema = cast(dict[str, Any], load_json(SCHEMA_PATH))
    fixtures = cast(dict[str, Any], load_json(FIXTURE_PATH))
    validator = Draft202012Validator(schema)

    configs: dict[str, dict[str, Any]] = {}
    file_hashes = {
        "schema": sha256(SCHEMA_PATH.read_bytes()),
        "fixtures": sha256(FIXTURE_PATH.read_bytes()),
        "verifier": sha256(Path(__file__).read_bytes()),
    }
    canonical_hashes: dict[str, str] = {}
    derivations: dict[str, dict[str, int]] = {}
    option_family_coverage: dict[str, bool] = {}
    fail_closed_mutations: dict[str, dict[str, dict[str, bool]]] = {}
    for task, path in CONFIG_PATHS.items():
        config = cast(dict[str, Any], load_json(path))
        validator.validate(config)
        validate_definition_domain(config)
        validate_task_binding(task, config)
        fail_closed_mutations[task] = {
            "schema": assert_schema_rejects_drift(schema, validator, config),
            "domain": assert_domain_rejects_drift(validator, config),
            "task_binding": assert_task_binding_rejects_drift(task, validator, config),
        }
        configs[task] = config
        file_hashes[f"{task}_config"] = sha256(path.read_bytes())
        canonical_hashes[task] = sha256(canonical_json_bytes(config))
        derivations[task] = {
            str(item["definition_id"]): int(item["derivation"]["nominal_k"])
            for item in config["evaluation"]["high_value_definitions"]
        }
        if task == "alpha":
            option_family_coverage = assert_option_family_coverage(validator, config)

    rank_results: dict[str, Any] = {}
    for name, fixture in fixtures["rank_fixtures"].items():
        forward = top_k_tie_inclusive(fixture["rows"], int(fixture["nominal_k"]))
        reverse = top_k_tie_inclusive(list(reversed(fixture["rows"])), int(fixture["nominal_k"]))
        if forward != reverse:
            raise AssertionError(f"{name} is not row-order invariant")
        rank_results[name] = forward

    invalid_fail_closed: dict[str, bool] = {}
    for name, rows in fixtures["invalid_rows"].items():
        try:
            top_k_tie_inclusive(rows, 1)
        except ValueError:
            invalid_fail_closed[name] = True
        else:
            raise AssertionError(f"{name} did not fail closed")

    pool_size_results = []
    for item in fixtures["pool_size_cases"]:
        candidate_count = int(item["candidate_count"])
        fraction = float(item["fraction"])
        pool_size_results.append(
            {
                "candidate_count": candidate_count,
                "quantile_nominal_k": math.ceil(candidate_count * fraction),
                "fixed_top_k": int(item["fixed_top_k"]),
            }
        )

    scale = fixtures["scale_fixture"]
    original_rows = scale["rows"]
    scaled_rows = [
        [candidate_id, float(label) * float(scale["positive_scale"])]
        for candidate_id, label in original_rows
    ]
    shifted_rows = [
        [candidate_id, float(label) + float(scale["positive_shift"])]
        for candidate_id, label in original_rows
    ]
    monotonic_rows = [
        [candidate_id, float(label) ** int(scale["strict_monotonic_power"])]
        for candidate_id, label in original_rows
    ]
    nominal_k = int(scale["nominal_k"])
    rank_original = top_k_tie_inclusive(original_rows, nominal_k)["selected_candidate_ids"]
    rank_scaled = top_k_tie_inclusive(scaled_rows, nominal_k)["selected_candidate_ids"]
    rank_shifted = top_k_tie_inclusive(shifted_rows, nominal_k)["selected_candidate_ids"]
    rank_monotonic = top_k_tie_inclusive(monotonic_rows, nominal_k)["selected_candidate_ids"]
    if (
        rank_original != rank_scaled
        or rank_original != rank_shifted
        or rank_original != rank_monotonic
    ):
        raise AssertionError("rank selection changed under a strict monotonic transform")

    absolute_original = threshold_members(
        original_rows,
        kind="absolute",
        value=float(scale["absolute_threshold"]),
    )
    absolute_scaled = threshold_members(
        scaled_rows,
        kind="absolute",
        value=float(scale["absolute_threshold"]),
    )
    wt_original = threshold_members(
        original_rows,
        kind="wt_relative",
        value=float(scale["wt_delta"]),
        wt_reference=float(scale["wt_reference"]),
    )
    wt_shifted = threshold_members(
        shifted_rows,
        kind="wt_relative",
        value=float(scale["wt_delta"]),
        wt_reference=float(scale["wt_reference"]) + float(scale["positive_shift"]),
    )
    wt_scaled = threshold_members(
        scaled_rows,
        kind="wt_relative",
        value=float(scale["wt_delta"]),
        wt_reference=float(scale["wt_reference"]) * float(scale["positive_scale"]),
    )
    near_original = threshold_members(
        original_rows,
        kind="near_optimal",
        value=float(scale["near_optimal_epsilon"]),
    )
    near_scaled = threshold_members(
        scaled_rows,
        kind="near_optimal",
        value=float(scale["near_optimal_epsilon"]),
    )
    near_shifted = threshold_members(
        shifted_rows,
        kind="near_optimal",
        value=float(scale["near_optimal_epsilon"]),
    )

    task_first = fixtures["task_first_fixture"]
    task_means = [float(item["success"]) for item in task_first.values()]
    all_seed_values = [
        float(item["success"])
        for item in task_first.values()
        for _ in range(int(item["seed_count"]))
    ]

    probability_results: dict[str, Any] = {}
    budgets = fixtures["nominal_probability_cases"]["budgets"]
    for task, item in fixtures["nominal_probability_cases"]["tasks"].items():
        candidate_count = int(item["candidate_count"])
        positive_count = int(item["nominal_k"])
        probability_results[task] = {
            str(budget): round(
                hit_probability(candidate_count, positive_count, int(budget)),
                6,
            )
            for budget in budgets
        }
        probability_results[task]["tie_expansion_bounds"] = {
            "positive_count": [positive_count, candidate_count],
            "hit_probability_budget_32": [
                round(hit_probability(candidate_count, positive_count, 32), 6),
                1.0,
            ],
        }

    return {
        "file_sha256": file_hashes,
        "canonical_config_sha256": canonical_hashes,
        "task_bindings": EXPECTED_TASK_BINDINGS,
        "nominal_k_derivations": derivations,
        "option_family_coverage": option_family_coverage,
        "fail_closed_mutations": fail_closed_mutations,
        "rank_fixtures": rank_results,
        "invalid_rows_fail_closed": invalid_fail_closed,
        "pool_size_contracts": pool_size_results,
        "scale_behavior": {
            "rank_invariant": True,
            "absolute_scale_invariant": absolute_original == absolute_scaled,
            "wt_translation_invariant": wt_original == wt_shifted,
            "wt_scale_invariant": wt_original == wt_scaled,
            "near_optimal_translation_invariant": near_original == near_shifted,
            "near_optimal_scale_invariant": near_original == near_scaled,
        },
        "task_first": {
            "pooled_seed_mean": round(sum(all_seed_values) / len(all_seed_values), 6),
            "task_first_mean": round(sum(task_means) / len(task_means), 6),
        },
        "nominal_k_hit_probability": probability_results,
        "evidence_boundary": {
            "real_labels_read": 0,
            "held_out_access": 0,
            "cos_read_bytes": 0,
            "cos_write_bytes": 0,
            "models_or_replay_run": 0,
            "top_1_percent_accepted": False,
            "production_schema_modified": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--emit-results",
        action="store_true",
        help="print calculated JSON instead of comparing it with expected-results.json",
    )
    args = parser.parse_args()
    results = calculate_results()
    if args.emit_results:
        print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    expected = load_json(EXPECTED_PATH)
    if results != expected:
        raise AssertionError(
            "calculated evidence differs from expected-results.json; "
            "run with --emit-results and review the exact diff"
        )
    print("Issue #28 prototype verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
