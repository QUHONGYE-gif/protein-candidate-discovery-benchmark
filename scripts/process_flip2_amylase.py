#!/usr/bin/env python3
"""Build one immutable FLIP2 Alpha Amylase processing bundle outside Git."""

from _processing_cli import run_processing_cli

from pcdb.datasets.amylase import build_amylase_processing_bundle, load_amylase_processing_config

if __name__ == "__main__":
    raise SystemExit(
        run_processing_cli(
            description=__doc__ or "Process FLIP2 Alpha Amylase",
            load_config=load_amylase_processing_config,
            build_bundle=build_amylase_processing_bundle,
        )
    )
