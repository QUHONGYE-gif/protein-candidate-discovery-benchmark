"""User-selected ProteinGym v1.3 iteration cohort, separate from formal M5C."""

ASSAY_IDS = (
    "PABP_YEAST_Melamed_2013",
    "HSP82_YEAST_Flynn_2019",
    "HXK4_HUMAN_Gersing_2022_activity",
)
COUNTS = dict(zip(ASSAY_IDS, (37708, 13294, 8570), strict=True))
METADATA_URL = (
    "https://raw.githubusercontent.com/OATML-Markslab/ProteinGym/"
    "62eab5b3c83a3727b5b57bfbe377b24ef7545ccc/reference_files/DMS_substitutions.csv"
)
METADATA_SHA256 = "a8f498011532a74aa9fe556a50555a75e928c5837d19c06a87592ae04049b308"
BASE_COMMIT = "5c0119df4d4fdef62ca616e7199b4cb8061eb4a9"
STUDY_ID = "m5c-proteingym-benign-iteration-20260908"
