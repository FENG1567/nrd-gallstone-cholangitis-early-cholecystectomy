#!/usr/bin/env python3
"""Create a source/input hash allowlist for an authorized v4.7 run.

The default output is deliberately candidate-only and cannot be
executed by the frozen runner.  The operator must inspect the hashes and rerun
this command with ``--approve`` plus an explicit one-time authorization
binding.  This script never reads or prints patient-level values; it hashes
only the required files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


YEARS = (2018, 2019, 2020)
SOURCE_FILES = {
    "runner": Path("code/analysis/frozen_v4_7.py"),
    "tests": Path("code/analysis/test_amendment004_v7.py"),
    "regression_tests": Path("code/analysis/test_amendment004_v7_regression.py"),
    "execution_barrier_tests": Path("code/analysis/test_amendment004_v7_execution_barriers.py"),
    "authorization_tests": Path("code/analysis/test_amendment004_v7_authorization.py"),
    "formal_attachment_tests": Path("code/analysis/test_amendment004_v7_formal_attachment.py"),
    "base_config": Path("config/V3_FROZEN_BASE_CONFIG.json"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_file(path: Path, label: str) -> str:
    if not path.is_file():
        raise SystemExit(f"missing {label}: {path}")
    return sha256(path)


def input_group(private_root: Path, kind: str) -> list[dict[str, str]]:
    names: list[str] = []
    for year in YEARS:
        if kind == "structural":
            names.extend(
                [
                    f"{year}/index_candidates_{year}.parquet",
                    f"{year}/hospital_linked_{year}.parquet",
                ]
            )
        elif kind == "official_frames":
            names.append(f"{year}/official_nrd_hospital_frame_{year}.csv")
        elif kind == "linked_outcomes":
            names.append(f"{year}/linked_discharges_{year}.parquet")
        else:
            raise ValueError(kind)
    rows = []
    for relative in names:
        path = private_root / relative
        rows.append({"relative_path": relative, "sha256": require_file(path, relative)})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="root of this repository")
    parser.add_argument("--private-root", type=Path, required=True, help="local licensed NRD-derived input root")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sap", type=Path, default=Path("protocol/SAP_v4_7.md"))
    parser.add_argument("--execution-authorization-id", default="")
    parser.add_argument("--authorized-run-id", default="")
    parser.add_argument("--authorized-run-root", type=Path)
    parser.add_argument("--mode", choices=("gate-only", "dryrun", "formal"), default="formal")
    parser.add_argument("--boot", type=int, default=500)
    parser.add_argument("--approve", action="store_true", help="emit an explicitly bound approved allowlist")
    args = parser.parse_args()

    repo = args.repo.resolve()
    private_root = args.private_root.resolve()
    sap = (repo / args.sap).resolve() if not args.sap.is_absolute() else args.sap.resolve()
    source_hashes = {
        key: require_file(repo / relative, key) for key, relative in SOURCE_FILES.items()
    }
    source_hashes["sap"] = require_file(sap, "SAP")

    if args.approve:
        if not args.execution_authorization_id or not args.authorized_run_id or args.authorized_run_root is None:
            raise SystemExit("--approve requires authorization id, run id, and run root")
        run_root = args.authorized_run_root.resolve()
        if not run_root.is_dir() or run_root.is_symlink():
            raise SystemExit("authorized run root must pre-exist and must not be a symlink")
        if run_root.name != args.authorized_run_id:
            raise SystemExit("run root basename must equal authorized run id")
        receipt_parent = private_root / ".authorization_receipts"
        if not receipt_parent.is_dir() or receipt_parent.is_symlink():
            raise SystemExit("private-root/.authorization_receipts must pre-exist and not be a symlink")
        auth = {
            "approval_status": "APPROVED",
            "candidate_only": False,
            "execution_authorization": "APPROVED_ONE_TIME",
            "execution_authorization_id": args.execution_authorization_id,
            "authorized_run_id": args.authorized_run_id,
            "authorized_mode": args.mode,
            "authorized_boot": args.boot,
            "authorized_run_root": str(run_root),
            "authorization_receipt_path": str((receipt_parent / f"{args.execution_authorization_id}.json").resolve()),
            "deployment_prohibited": False,
        }
    else:
        auth = {
            "approval_status": "CANDIDATE_NOT_APPROVED",
            "candidate_only": True,
            "execution_authorization": "NONE",
            "execution_authorization_id": "",
            "authorized_run_id": "",
            "authorized_mode": None,
            "authorized_boot": None,
            "authorized_run_root": "",
            "authorization_receipt_path": "",
            "deployment_prohibited": True,
        }

    payload = {
        **auth,
        "source_hashes": source_hashes,
        "input_hashes": {
            "structural": input_group(private_root, "structural"),
            "official_frames": input_group(private_root, "official_frames"),
            "linked_outcomes": input_group(private_root, "linked_outcomes"),
        },
        "statement": "Hashes bind the frozen public source and the separately licensed local inputs; patient-level files are not included in this repository.",
        "dependency_rule": "The runner must be invoked with the same source files, SAP, input hashes, and one-time output binding.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
