#!/usr/bin/env python3
"""Validate official annual HCUP hospital-frame CSVs supplied by an owner."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path


EXPECTED = ["YEAR", "HOSP_NRD", "NRD_STRATUM"]


def validate(path: Path, year: int) -> dict[str, int]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or list(rows[0]) != EXPECTED:
        raise AssertionError(f"{path} must have exactly {EXPECTED} columns")
    if any(str(row["YEAR"]) != str(year) or not row["HOSP_NRD"] or not row["NRD_STRATUM"] for row in rows):
        raise AssertionError(f"invalid year or missing value in {path}")
    keys = [(row["YEAR"], row["HOSP_NRD"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise AssertionError(f"duplicate YEAR × HOSP_NRD rows in {path}")
    strata = {}
    for row in rows:
        strata.setdefault(row["NRD_STRATUM"], set()).add(row["HOSP_NRD"])
    if any(len(hospitals) < 2 for hospitals in strata.values()):
        raise AssertionError(f"singleton NRD stratum in {path}")
    return {"rows": len(rows), "strata": len(strata)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    result = {}
    for year in (2018, 2019, 2020):
        path = args.root / str(year) / f"official_nrd_hospital_frame_{year}.csv"
        result[str(year)] = validate(path, year)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
