#!/usr/bin/env python3
"""Validate released aggregate outputs without opening licensed NRD data."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


FORBIDDEN = ("KEY_NRD", "NRD_VISITLINK", "HOSP_NRD")
PRIVATE_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+", re.IGNORECASE),
    re.compile("/" + "home" + r"/(?:[^/\s]+/){1,2}"),
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def validate_gate(path: Path) -> None:
    payload = json.loads(read_text(path))
    if payload.get("status") != "PASS":
        raise AssertionError(f"disclosure gate is not PASS: {path}")
    if payload.get("forbidden_tokens_found"):
        raise AssertionError(f"disclosure gate reports tokens: {path}")


def validate_csv(path: Path) -> tuple[int, list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise AssertionError(f"empty aggregate CSV: {path}")
    header = rows[0]
    if any(token in "\n".join(rows[0]) for token in FORBIDDEN):
        raise AssertionError(f"row-level identifier in header: {path}")
    raw = "\n".join(",".join(row) for row in rows)
    if any(token.lower() in raw.lower() for token in FORBIDDEN):
        raise AssertionError(f"forbidden token in aggregate CSV: {path}")
    if any(pattern.search(raw) for pattern in PRIVATE_PATH_PATTERNS):
        raise AssertionError(f"private path in aggregate CSV: {path}")
    return len(rows) - 1, header


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    root = args.repo.resolve()
    processed = root / "data" / "processed"
    if not processed.is_dir():
        raise SystemExit(f"missing aggregate directory: {processed}")

    for relative in (
        "DISCLOSURE_GATE.json",
        "amendment005/DISCLOSURE_GATE.json",
        "amendment006/DISCLOSURE_GATE.json",
    ):
        validate_gate(processed / relative)

    csv_paths = sorted(processed.rglob("*.csv"))
    if not csv_paths:
        raise SystemExit("no aggregate CSV files found")
    summary = {}
    for path in csv_paths:
        summary[str(path.relative_to(root))] = validate_csv(path)[0]

    for path in processed.rglob("*.json"):
        text = read_text(path)
        if any(token.lower() in text.lower() for token in FORBIDDEN):
            raise AssertionError(f"forbidden token in aggregate JSON: {path}")
        if any(pattern.search(text) for pattern in PRIVATE_PATH_PATTERNS):
            raise AssertionError(f"private path in aggregate JSON: {path}")

    primary = processed / "primary_results.csv"
    if not primary.is_file():
        raise SystemExit("primary_results.csv is required")
    print(json.dumps({"status": "PASS", "csv_rows": summary}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
