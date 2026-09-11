# All planned endpoints

All40 states; 99.375% descriptive intervals, 20,000 paired bootstrap draws, PCG64 seed18001.
No global simultaneous coverage, equivalence or causal mediation claim.

## xgb: pair_high minus pair_low (primary_A)

| Section / metric | Estimate [interval] |
|---|---|
| cells / AA | 0.5 [0.15, 0.975] |
| cells / AB | 0.625 [0.175, 1.2] |
| cells / BA | 0.5 [0.1, 1.05] |
| cells / BB | 0.6 [0.175, 1.15] |
| effects / interaction | -0.025 [-0.375, 0.25] |
| effects / pool | -0.0125 [-0.1125, 0.0625] |
| effects / pool_at_new_rank | -0.025 [-0.225, 0.15] |
| effects / pool_at_old_rank | 0 [-0.15, 0.2] |
| effects / pool_minus_ranking | -0.125 [-0.275, 0] |
| effects / ranking | 0.1125 [-0.0125, 0.26878906] |
| effects / ranking_in_new_pool | 0.1 [-0.1, 0.325] |
| effects / ranking_in_old_pool | 0.125 [-0.05, 0.35] |
| effects / total | 0.1 [-0.075, 0.3] |
| changes / capacity | 0 [-0.675, 0.45] |
| changes / fixed_hits_16 | -0.2 [-0.525, 0.025] |
| changes / fixed_selection_overlap | 0.05 [-0.5, 0.575] |
| changes / global16_outside_reference | -0.05 [-0.575, 0.5] |
| changes / global_minus_fixed | 0.3 [0.025, 0.625] |
| changes / hits_16 | 0.1 [-0.075, 0.3] |
| changes / hits_64 | 0 [-0.675, 0.45] |
| changes / unused_capacity | -0.1 [-0.7, 0.325] |
| pool_overlap | 43.075 [34.087266, 51.5] |

State counts: {"negative": 2, "positive": 6, "same_pool": 11, "zero": 32, "zero_interaction": 35}

## xgb: composition_matched minus original_support (primary_B)

| Section / metric | Estimate [interval] |
|---|---|
| cells / AA | 0.525 [0.175, 0.975] |
| cells / AB | 0.4 [0.1, 0.875] |
| cells / BA | 0.375 [0.125, 0.75] |
| cells / BB | 0.475 [0.15, 0.95] |
| effects / interaction | 0.225 [-0.075, 0.625] |
| effects / pool | -0.0375 [-0.2, 0.0875] |
| effects / pool_at_new_rank | 0.075 [-0.1, 0.275] |
| effects / pool_at_old_rank | -0.15 [-0.475, 0.05] |
| effects / pool_minus_ranking | -0.025 [-0.175, 0.125] |
| effects / ranking | -0.0125 [-0.2375, 0.175] |
| effects / ranking_in_new_pool | 0.1 [-0.075, 0.3] |
| effects / ranking_in_old_pool | -0.125 [-0.5, 0.16257812] |
| effects / total | -0.05 [-0.4, 0.225] |
| changes / capacity | -0.075 [-0.7, 0.525] |
| changes / fixed_hits_16 | 0.175 [0.025, 0.4] |
| changes / fixed_selection_overlap | -0.7 [-1.625, 0.05] |
| changes / global16_outside_reference | 0.7 [-0.05, 1.625] |
| changes / global_minus_fixed | -0.225 [-0.6, 0.075] |
| changes / hits_16 | -0.05 [-0.4, 0.225] |
| changes / hits_64 | -0.075 [-0.7, 0.525] |
| changes / unused_capacity | -0.025 [-0.55, 0.5] |
| pool_overlap | 32.85 [24.975, 40.6] |

State counts: {"negative": 5, "positive": 6, "same_pool": 3, "zero": 29, "zero_interaction": 30}

## xgb: composition_matched minus original_random (auxiliary)

| Section / metric | Estimate [interval] |
|---|---|
| cells / AA | 0.3 [0.05, 0.675] |
| cells / AB | 0.35 [0.05, 0.825] |
| cells / BA | 0.4 [0.1, 0.8] |
| cells / BB | 0.475 [0.15, 0.95] |
| effects / interaction | 0.025 [-0.4, 0.375] |
| effects / pool | 0.1125 [-0.0375, 0.3125] |
| effects / pool_at_new_rank | 0.125 [-0.15, 0.4] |
| effects / pool_at_old_rank | 0.1 [-0.125, 0.375] |
| effects / pool_minus_ranking | 0.05 [-0.125, 0.275] |
| effects / ranking | 0.0625 [-0.0875, 0.225] |
| effects / ranking_in_new_pool | 0.075 [-0.225, 0.35] |
| effects / ranking_in_old_pool | 0.05 [-0.15, 0.275] |
| effects / total | 0.175 [-0.075, 0.45] |
| changes / capacity | 0.175 [-0.6, 0.95] |
| changes / fixed_hits_16 | 0.275 [0.025, 0.6] |
| changes / fixed_selection_overlap | 0.45 [-0.375, 1.525] |
| changes / global16_outside_reference | -0.45 [-1.525, 0.375] |
| changes / global_minus_fixed | -0.1 [-0.4, 0.2] |
| changes / hits_16 | 0.175 [-0.075, 0.45] |
| changes / hits_64 | 0.175 [-0.6, 0.95] |
| changes / unused_capacity | 0 [-0.75, 0.625] |
| pool_overlap | 22.1 [13.262422, 31.95] |

State counts: {"negative": 3, "positive": 8, "same_pool": 2, "zero": 29, "zero_interaction": 28}

## xgb: original_support minus original_random (historical_reconstruction)

| Section / metric | Estimate [interval] |
|---|---|
| cells / AA | 0.3 [0.05, 0.675] |
| cells / AB | 0.4 [0.1, 0.825] |
| cells / BA | 0.5 [0.15, 0.975] |
| cells / BB | 0.525 [0.175, 0.975] |
| effects / interaction | -0.075 [-0.55, 0.325] |
| effects / pool | 0.1625 [-0.025, 0.4625] |
| effects / pool_at_new_rank | 0.125 [-0.175, 0.45] |
| effects / pool_at_old_rank | 0.2 [-0.075, 0.6] |
| effects / pool_minus_ranking | 0.1 [-0.2, 0.525] |
| effects / ranking | 0.0625 [-0.1625, 0.3125] |
| effects / ranking_in_new_pool | 0.025 [-0.35, 0.4] |
| effects / ranking_in_old_pool | 0.1 [-0.125, 0.375] |
| effects / total | 0.225 [-0.1, 0.6] |
| changes / capacity | 0.25 [-0.325, 0.925] |
| changes / fixed_hits_16 | 0.1 [-0.1, 0.3] |
| changes / fixed_selection_overlap | 1.15 [0.125, 2.525] |
| changes / global16_outside_reference | -1.15 [-2.525, -0.125] |
| changes / global_minus_fixed | 0.125 [-0.2, 0.5] |
| changes / hits_16 | 0.225 [-0.1, 0.6] |
| changes / hits_64 | 0.25 [-0.325, 0.925] |
| changes / unused_capacity | 0.025 [-0.45, 0.575] |
| pool_overlap | 26.525 [17.8, 35.687578] |

State counts: {"negative": 4, "positive": 8, "same_pool": 2, "zero": 28, "zero_interaction": 29}

## ridge: pair_high minus pair_low (primary_A)

| Section / metric | Estimate [interval] |
|---|---|
| cells / AA | 0.9 [0.375, 1.625] |
| cells / AB | 0.8 [0.3, 1.5] |
| cells / BA | 0.875 [0.33742188, 1.6] |
| cells / BB | 0.8 [0.3, 1.5] |
| effects / interaction | 0.025 [-0.1, 0.15] |
| effects / pool | -0.0125 [-0.075, 0.05] |
| effects / pool_at_new_rank | 0 [-0.1, 0.1] |
| effects / pool_at_old_rank | -0.025 [-0.1, 0] |
| effects / pool_minus_ranking | 0.075 [-0.1, 0.3] |
| effects / ranking | -0.0875 [-0.3, 0.075] |
| effects / ranking_in_new_pool | -0.075 [-0.275, 0.075] |
| effects / ranking_in_old_pool | -0.1 [-0.325, 0.1] |
| effects / total | -0.1 [-0.325, 0.075] |
| changes / capacity | -0.1 [-0.5, 0.225] |
| changes / fixed_hits_16 | -0.075 [-0.25, 0] |
| changes / fixed_selection_overlap | 0.825 [0.175, 1.6] |
| changes / global16_outside_reference | -0.825 [-1.6, -0.175] |
| changes / global_minus_fixed | -0.025 [-0.15, 0.075] |
| changes / hits_16 | -0.1 [-0.325, 0.075] |
| changes / hits_64 | -0.1 [-0.5, 0.225] |
| changes / unused_capacity | 0 [-0.375, 0.3] |
| pool_overlap | 50.625 [44.9, 55.775] |

State counts: {"negative": 4, "positive": 1, "same_pool": 11, "zero": 35, "zero_interaction": 37}

## ridge: composition_matched minus original_support (primary_B)

| Section / metric | Estimate [interval] |
|---|---|
| cells / AA | 1.175 [0.45, 2.15] |
| cells / AB | 1.15 [0.4, 2.125] |
| cells / BA | 1.25 [0.5, 2.2375781] |
| cells / BB | 1.1 [0.35, 2.1] |
| effects / interaction | -0.125 [-0.28757812, 0] |
| effects / pool | 0.0125 [-0.1, 0.1] |
| effects / pool_at_new_rank | -0.05 [-0.175, 0] |
| effects / pool_at_old_rank | 0.075 [-0.075, 0.225] |
| effects / pool_minus_ranking | 0.1 [-0.05, 0.275] |
| effects / ranking | -0.0875 [-0.3, 0.0625] |
| effects / ranking_in_new_pool | -0.15 [-0.36257812, 0] |
| effects / ranking_in_old_pool | -0.025 [-0.25, 0.175] |
| effects / total | -0.075 [-0.375, 0.125] |
| changes / capacity | -0.025 [-0.5, 0.525] |
| changes / fixed_hits_16 | -0.025 [-0.225, 0.125] |
| changes / fixed_selection_overlap | -0.15 [-1.125, 0.825] |
| changes / global16_outside_reference | 0.15 [-0.825, 1.125] |
| changes / global_minus_fixed | -0.05 [-0.25, 0.15] |
| changes / hits_16 | -0.075 [-0.375, 0.125] |
| changes / hits_64 | -0.025 [-0.5, 0.525] |
| changes / unused_capacity | 0.05 [-0.5, 0.625] |
| pool_overlap | 46.45 [41.462266, 50.787578] |

State counts: {"negative": 3, "positive": 2, "same_pool": 3, "zero": 35, "zero_interaction": 35}

## ridge: composition_matched minus original_random (auxiliary)

| Section / metric | Estimate [interval] |
|---|---|
| cells / AA | 0.825 [0.25, 1.7] |
| cells / AB | 0.85 [0.25, 1.75] |
| cells / BA | 0.775 [0.225, 1.65] |
| cells / BB | 1.1 [0.35, 2.1] |
| effects / interaction | 0.3 [-0.15, 0.85] |
| effects / pool | 0.1 [-0.175, 0.3875] |
| effects / pool_at_new_rank | 0.25 [-0.175, 0.8] |
| effects / pool_at_old_rank | -0.05 [-0.3, 0.125] |
| effects / pool_minus_ranking | -0.075 [-0.225, 0.075] |
| effects / ranking | 0.175 [-0.1, 0.5375] |
| effects / ranking_in_new_pool | 0.325 [-0.125, 0.925] |
| effects / ranking_in_old_pool | 0.025 [-0.225, 0.225] |
| effects / total | 0.275 [-0.275, 0.925] |
| changes / capacity | 0.525 [-0.6, 2.0375781] |
| changes / fixed_hits_16 | 0.325 [-0.2, 0.96257812] |
| changes / fixed_selection_overlap | 2.4 [0.825, 4.125] |
| changes / global16_outside_reference | -2.4 [-4.125, -0.825] |
| changes / global_minus_fixed | -0.05 [-0.325, 0.2] |
| changes / hits_16 | 0.275 [-0.275, 0.925] |
| changes / hits_64 | 0.525 [-0.6, 2.0375781] |
| changes / unused_capacity | 0.25 [-0.55, 1.2] |
| pool_overlap | 30.625 [22.775, 38.45] |

State counts: {"negative": 5, "positive": 10, "same_pool": 2, "zero": 25, "zero_interaction": 28}

## ridge: original_support minus original_random (historical_reconstruction)

| Section / metric | Estimate [interval] |
|---|---|
| cells / AA | 0.825 [0.25, 1.7] |
| cells / AB | 0.875 [0.275, 1.775] |
| cells / BA | 0.825 [0.25, 1.7] |
| cells / BB | 1.175 [0.45, 2.15] |
| effects / interaction | 0.3 [-0.1, 0.825] |
| effects / pool | 0.15 [-0.05, 0.4125] |
| effects / pool_at_new_rank | 0.3 [-0.05, 0.8] |
| effects / pool_at_old_rank | 0 [-0.125, 0.15] |
| effects / pool_minus_ranking | -0.05 [-0.225, 0.125] |
| effects / ranking | 0.2 [-0.0625, 0.5625] |
| effects / ranking_in_new_pool | 0.35 [-0.075, 0.95] |
| effects / ranking_in_old_pool | 0.05 [-0.15, 0.25] |
| effects / total | 0.35 [-0.1, 0.975] |
| changes / capacity | 0.55 [-0.375, 1.95] |
| changes / fixed_hits_16 | 0.35 [-0.1, 0.96257812] |
| changes / fixed_selection_overlap | 2.55 [1.025, 4.275] |
| changes / global16_outside_reference | -2.55 [-4.275, -1.025] |
| changes / global_minus_fixed | 0 [-0.275, 0.275] |
| changes / hits_16 | 0.35 [-0.1, 0.975] |
| changes / hits_64 | 0.55 [-0.375, 1.95] |
| changes / unused_capacity | 0.2 [-0.4, 1.025] |
| pool_overlap | 32.775 [25.212422, 40.3] |

State counts: {"negative": 4, "positive": 11, "same_pool": 2, "zero": 25, "zero_interaction": 28}

## Arm ridge_composition_matched

| Metric | Mean [interval] |
|---|---|
| capacity | 2.6 [1.3, 4.25] |
| fixed_hits_16 | 1.175 [0.4, 2.225] |
| fixed_selection_overlap | 8.9 [6.975, 10.725] |
| global16_outside_reference | 7.1 [5.275, 9.025] |
| global_minus_fixed | -0.075 [-0.375, 0.2] |
| hits_16 | 1.1 [0.35, 2.1] |
| hits_64 | 2.6 [1.3, 4.25] |
| unused_capacity | 1.5 [0.8, 2.425] |

State counts: {"any_hit": 16, "high_in_pool_but_zero_hits": 12, "no_high_in_pool": 12}

## Arm ridge_original_random

| Metric | Mean [interval] |
|---|---|
| capacity | 2.075 [1.0624219, 3.375] |
| fixed_hits_16 | 0.85 [0.275, 1.725] |
| fixed_selection_overlap | 6.5 [4.45, 8.675] |
| global16_outside_reference | 9.5 [7.325, 11.55] |
| global_minus_fixed | -0.025 [-0.3, 0.275] |
| hits_16 | 0.825 [0.25, 1.7] |
| hits_64 | 2.075 [1.0624219, 3.375] |
| unused_capacity | 1.25 [0.65, 2] |

State counts: {"any_hit": 14, "high_in_pool_but_zero_hits": 13, "no_high_in_pool": 13}

## Arm ridge_original_support

| Metric | Mean [interval] |
|---|---|
| capacity | 2.625 [1.375, 4.225] |
| fixed_hits_16 | 1.2 [0.425, 2.225] |
| fixed_selection_overlap | 9.05 [7.075, 10.9] |
| global16_outside_reference | 6.95 [5.1, 8.925] |
| global_minus_fixed | -0.025 [-0.3, 0.25] |
| hits_16 | 1.175 [0.45, 2.15] |
| hits_64 | 2.625 [1.375, 4.225] |
| unused_capacity | 1.45 [0.725, 2.4] |

State counts: {"any_hit": 18, "high_in_pool_but_zero_hits": 10, "no_high_in_pool": 12}

## Arm ridge_pair_high

| Metric | Mean [interval] |
|---|---|
| capacity | 2.075 [1.05, 3.275] |
| fixed_hits_16 | 0.575 [0.15, 1.175] |
| fixed_selection_overlap | 7.375 [5.4124219, 9.3] |
| global16_outside_reference | 8.625 [6.7, 10.587578] |
| global_minus_fixed | 0.225 [-0.075, 0.6] |
| hits_16 | 0.8 [0.3, 1.5] |
| hits_64 | 2.075 [1.05, 3.275] |
| unused_capacity | 1.275 [0.6, 2.125] |

State counts: {"any_hit": 16, "high_in_pool_but_zero_hits": 8, "no_high_in_pool": 16}

## Arm ridge_pair_low

| Metric | Mean [interval] |
|---|---|
| capacity | 2.175 [1.1, 3.4125781] |
| fixed_hits_16 | 0.65 [0.2, 1.25] |
| fixed_selection_overlap | 6.55 [4.675, 8.45] |
| global16_outside_reference | 9.45 [7.55, 11.325] |
| global_minus_fixed | 0.25 [-0.05, 0.65] |
| hits_16 | 0.9 [0.375, 1.625] |
| hits_64 | 2.175 [1.1, 3.4125781] |
| unused_capacity | 1.275 [0.55, 2.15] |

State counts: {"any_hit": 18, "high_in_pool_but_zero_hits": 6, "no_high_in_pool": 16}

## Arm xgb_composition_matched

| Metric | Mean [interval] |
|---|---|
| capacity | 1.65 [0.825, 2.675] |
| fixed_hits_16 | 0.825 [0.375, 1.375] |
| fixed_selection_overlap | 2.225 [1.225, 3.45] |
| global16_outside_reference | 13.775 [12.55, 14.775] |
| global_minus_fixed | -0.35 [-0.8, 0.025] |
| hits_16 | 0.475 [0.15, 0.95] |
| hits_64 | 1.65 [0.825, 2.675] |
| unused_capacity | 1.175 [0.55, 1.9875781] |

State counts: {"any_hit": 12, "high_in_pool_but_zero_hits": 15, "no_high_in_pool": 13}

## Arm xgb_original_random

| Metric | Mean [interval] |
|---|---|
| capacity | 1.475 [0.55, 2.8] |
| fixed_hits_16 | 0.55 [0.2, 1] |
| fixed_selection_overlap | 1.775 [0.9, 2.975] |
| global16_outside_reference | 14.225 [13.025, 15.1] |
| global_minus_fixed | -0.25 [-0.525, 0] |
| hits_16 | 0.3 [0.05, 0.675] |
| hits_64 | 1.475 [0.55, 2.8] |
| unused_capacity | 1.175 [0.425, 2.2] |

State counts: {"any_hit": 8, "high_in_pool_but_zero_hits": 12, "no_high_in_pool": 20}

## Arm xgb_original_support

| Metric | Mean [interval] |
|---|---|
| capacity | 1.725 [0.825, 2.9] |
| fixed_hits_16 | 0.65 [0.25, 1.125] |
| fixed_selection_overlap | 2.925 [1.675, 4.475] |
| global16_outside_reference | 13.075 [11.525, 14.325] |
| global_minus_fixed | -0.125 [-0.55, 0.325] |
| hits_16 | 0.525 [0.175, 0.975] |
| hits_64 | 1.725 [0.825, 2.9] |
| unused_capacity | 1.2 [0.5, 2.1625781] |

State counts: {"any_hit": 12, "high_in_pool_but_zero_hits": 12, "no_high_in_pool": 16}

## Arm xgb_pair_high

| Metric | Mean [interval] |
|---|---|
| capacity | 1.825 [0.925, 2.875] |
| fixed_hits_16 | 0.3 [0.05, 0.725] |
| fixed_selection_overlap | 1.8 [0.95, 2.85] |
| global16_outside_reference | 14.2 [13.15, 15.05] |
| global_minus_fixed | 0.3 [0, 0.7] |
| hits_16 | 0.6 [0.175, 1.15] |
| hits_64 | 1.825 [0.925, 2.875] |
| unused_capacity | 1.225 [0.58742187, 1.975] |

State counts: {"any_hit": 12, "high_in_pool_but_zero_hits": 14, "no_high_in_pool": 14}

## Arm xgb_pair_low

| Metric | Mean [interval] |
|---|---|
| capacity | 1.825 [0.875, 3.05] |
| fixed_hits_16 | 0.5 [0.125, 0.975] |
| fixed_selection_overlap | 1.75 [0.9, 2.7375781] |
| global16_outside_reference | 14.25 [13.262422, 15.1] |
| global_minus_fixed | 0 [-0.35, 0.325] |
| hits_16 | 0.5 [0.15, 0.975] |
| hits_64 | 1.825 [0.875, 3.05] |
| unused_capacity | 1.325 [0.6, 2.3] |

State counts: {"any_hit": 11, "high_in_pool_but_zero_hits": 13, "no_high_in_pool": 16}
