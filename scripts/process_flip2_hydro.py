#!/usr/bin/env python3
"""Build one immutable FLIP2 Hydro processing bundle outside Git."""

from _processing_cli import run_processing_cli

from pcdb.datasets.hydro import (
    build_hydro_processing_bundle,
    load_hydro_processing_config,
)

if __name__ == "__main__":
    raise SystemExit(
        run_processing_cli(
            description=__doc__ or "Process FLIP2 Hydro",
            load_config=load_hydro_processing_config,
            build_bundle=build_hydro_processing_bundle,
        )
    )
