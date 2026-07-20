"""Command-line entry point for repository smoke checks."""

from __future__ import annotations

import argparse

from pcdb import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(prog="pcdb")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main() -> int:
    """Run the command-line interface."""
    build_parser().parse_args()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
