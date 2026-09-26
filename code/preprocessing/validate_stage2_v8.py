#!/usr/bin/env python3
"""Validate and summarize aggregate-only Stage 2 NRD frequency artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


YEARS = (2018, 2019, 2020)
EXPECTED = {
    "README.md",
    "SHA256SUMS",
    "code_counts_{year}.csv",
    "cohort_funnel_{year}.csv",
    "frequency_{year}.json",
    "run_manifest_{year}.json",
    "variable_availability_{year}.csv",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_year(root: Path, year: int) -> dict[str, object]:
    directory = root / str(year)
    expected = {name.format(year=year) for name in EXPECTED}
    actual = {item.name for item in directory.iterdir() if item.is_file()}
    if actual != expected:
        raise RuntimeError(
            f"{year}: output whitelist mismatch; missing={sorted(expected - actual)}; "
            f"unexpected={sorted(actual - expected)}"
        )

    checksum_lines = (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    checksum_names: set[str] = set()
    for line in checksum_lines:
        digest, name = line.split("  ", 1)
        checksum_names.add(name)
        observed = sha256(directory / name)
        if observed != digest:
            raise RuntimeError(f"{year}: checksum mismatch for {name}")
    if checksum_names != expected - {"SHA256SUMS"}:
        raise RuntimeError(f"{year}: checksum manifest coverage mismatch")

    frequency = json.loads((directory / f"frequency_{year}.json").read_text(encoding="utf-8"))
    manifest = json.loads((directory / f"run_manifest_{year}.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "PASS" or manifest.get("estimand_gate", {}).get("decision") != "PASS":
        raise RuntimeError(f"{year}: manifest or estimand gate is not PASS")
    if manifest.get("patient_level_rows_persisted") or manifest.get("inner_zip_persisted"):
        raise RuntimeError(f"{year}: persistence invariant failed")
    if frequency.get("row_count") != manifest.get("row_count") or not frequency.get("row_count"):
        raise RuntimeError(f"{year}: row-count inconsistency")
    for name in (f"code_counts_{year}.csv", f"cohort_funnel_{year}.csv", f"variable_availability_{year}.csv"):
        with (directory / name).open("r", encoding="utf-8", newline="") as handle:
            if not next(csv.DictReader(handle), None):
                raise RuntimeError(f"{year}: empty aggregate CSV {name}")

    counts = frequency["derived_counts"]
    days = frequency["procedure_day_stats"]
    return {
        "year": year,
        "row_count": frequency["row_count"],
        "strict_any": counts.get("strict_any", 0),
        "main_any": counts.get("main_any", 0),
        "broad_any": counts.get("broad_any", 0),
        "strict_ercp": counts.get("strict_ercp", 0),
        "broad_ercp": counts.get("broad_ercp", 0),
        "strict_ercp_chole_overlap": counts.get("strict_ercp_chole_overlap", 0),
        "broad_ercp_chole_overlap": counts.get("broad_ercp_chole_overlap", 0),
        "overlap_both_days": days.get("overlap_both_days", 0),
        "followup_structural_metric_status": "INVALID_NOT_USED_DTE_IS_NOT_ABSOLUTE_CALENDAR_DAY",
        "inner_zip_sha256": manifest.get("source", {}).get("inner_zip_sha256_in_memory"),
        "inner_zip_size": manifest.get("source", {}).get("inner_zip_size_bytes_in_memory"),
        "core_csv_bytes": manifest.get("source", {}).get("inner_csv_expected_uncompressed_bytes"),
        "status": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = [validate_year(args.root, year) for year in YEARS]
    result = {"status": "PASS", "years": rows}
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
