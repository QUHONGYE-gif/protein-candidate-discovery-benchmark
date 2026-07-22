#!/usr/bin/env python3
"""Build one immutable FLIP2 IRED processing bundle outside Git."""

from _processing_cli import run_processing_cli

from pcdb.datasets.ired import build_ired_processing_bundle, load_ired_processing_config

if __name__ == "__main__":
    raise SystemExit(
        run_processing_cli(
            description=__doc__ or "Process FLIP2 IRED",
            load_config=load_ired_processing_config,
            build_bundle=build_ired_processing_bundle,
        )
    )
