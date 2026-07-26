#!/usr/bin/env python3
"""Build one immutable FLIP2 NucB processing bundle outside Git."""

from _processing_cli import run_processing_cli

from pcdb.datasets.nucb import build_nucb_processing_bundle, load_nucb_processing_config

if __name__ == "__main__":
    raise SystemExit(
        run_processing_cli(
            description=__doc__ or "Process FLIP2 NucB",
            load_config=load_nucb_processing_config,
            build_bundle=build_nucb_processing_bundle,
        )
    )
