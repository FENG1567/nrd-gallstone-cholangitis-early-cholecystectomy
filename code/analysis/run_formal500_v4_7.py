#!/usr/bin/env python3
"""CLI wrapper for the frozen v4.7 NRD analysis.

The wrapper intentionally requires every data/output path.  An authorized
HCUP user must supply a private, local NRD-derived input root; this repository
never contains or downloads those files.
"""
from __future__ import annotations

import argparse

from frozen_v4_7 import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--public-out", required=True)
    parser.add_argument("--private-out", required=True)
    parser.add_argument("--sap", required=True)
    parser.add_argument("--hash-allowlist", required=True)
    parser.add_argument("--mode", choices=("gate-only", "dryrun", "formal"), required=True)
    parser.add_argument("--boot", type=int, default=500)
    parser.add_argument("--threads", type=int, default=8)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
