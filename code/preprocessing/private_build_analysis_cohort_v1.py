#!/usr/bin/env python3
"""Build the server-private NRD analysis cohort and aggregate audit summary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# Both roots are configured by the authorized user at run time.  The public
# package contains no server or home-directory path.
PRIVATE_ROOT = Path(os.environ.get("NRD_PRIVATE_ROOT", ".")).resolve()
PROJECT_ROOT = Path(os.environ.get("NRD_PROJECT_ROOT", ".")).resolve()


def private_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == PRIVATE_ROOT or PRIVATE_ROOT not in resolved.parents:
        raise RuntimeError(f"patient-level path must be a child of {PRIVATE_ROOT}")
    if resolved == PROJECT_ROOT or PROJECT_ROOT in resolved.parents:
        raise RuntimeError("patient-level path is forbidden inside project tree")
    return resolved


def aggregate_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == PROJECT_ROOT or PROJECT_ROOT not in resolved.parents:
        raise RuntimeError("aggregate summary must be a child of the project tree")
    return resolved


def numeric(frame: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_numeric(frame[name], errors="coerce")


def normalized(value: Any) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()


def biliary_principal(code: str) -> bool:
    if code == "K8301":
        return False
    return code.startswith(("K80", "K81", "K82", "K851")) or code in {
        "K830",
        "K8309",
        "K831",
    }


def merge_aux(index: pd.DataFrame, path: Path, key: str, suffix: str) -> pd.DataFrame:
    aux = pd.read_parquet(path)
    aux.columns = [name.upper() for name in aux.columns]
    if key not in aux.columns:
        raise RuntimeError(f"{key} missing from {path}")
    aux = aux.drop_duplicates(subset=[key], keep="first")
    collisions = [name for name in aux.columns if name != key and name in index.columns]
    aux = aux.rename(columns={name: f"{name}_{suffix}" for name in collisions})
    return index.merge(aux, on=key, how="left", validate="many_to_one")


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(pending, path)


def public_count(value: int) -> int | str:
    """Apply the HCUP small-cell rule before an aggregate leaves private storage."""
    return "SUPPRESSED_LE_10" if int(value) <= 10 else int(value)


def public_aggregate(private_manifest: dict[str, Any]) -> dict[str, Any]:
    """Create a DUA-safe summary with no directly or complementarily small arm cells."""
    aggregate = {
        key: value
        for key, value in private_manifest.items()
        if key not in {"private_output_sha256", "treatment_counts", "outcome_counts", "same_day_treated", "funnel", "rows"}
    }
    aggregate["rows"] = public_count(int(private_manifest["rows"]))
    treatment_counts = private_manifest["treatment_counts"]
    if any(int(value) <= 10 for value in treatment_counts.values()):
        # Suppress both arms: a total plus one arm would disclose the other by subtraction.
        aggregate["treatment_counts"] = "SUPPRESSED_DUE_TO_SMALL_ARM_CELL"
    else:
        aggregate["treatment_counts"] = {str(key): int(value) for key, value in treatment_counts.items()}
    aggregate["outcome_counts"] = {
        str(key): public_count(int(value)) for key, value in private_manifest["outcome_counts"].items()
    }
    aggregate["same_day_treated"] = public_count(int(private_manifest["same_day_treated"]))
    aggregate["funnel"] = {
        str(key): public_count(int(value)) for key, value in private_manifest["funnel"].items()
    }
    aggregate["small_cell_policy"] = (
        "HCUP DUA: all counts <=10 are suppressed; both treatment arms are suppressed when either arm is <=10 "
        "to prevent complementary disclosure."
    )
    return aggregate


def run(args: argparse.Namespace) -> dict[str, Any]:
    private_year = private_path(Path(args.private_root) / str(args.year))
    index_path = private_path(private_year / f"index_candidates_{args.year}.parquet")
    linked_path = private_path(private_year / f"linked_discharges_{args.year}.parquet")
    severity_path = private_path(private_year / f"severity_linked_{args.year}.parquet")
    hospital_path = private_path(private_year / f"hospital_linked_{args.year}.parquet")
    for required in (index_path, linked_path, severity_path, hospital_path):
        if not required.is_file():
            raise RuntimeError(f"required private input missing: {required}")

    index = pd.read_parquet(index_path)
    linked = pd.read_parquet(linked_path)
    index.columns = [name.upper() for name in index.columns]
    linked.columns = [name.upper() for name in linked.columns]
    required_columns = {
        "KEY_NRD", "NRD_VISITLINK", "NRD_DAYSTOEVENT", "LOS", "DIED", "RESIDENT",
        "PHENOTYPE_STRICT", "MALIGNANCY_EXCLUSION", "PRIOR_CHOLE_EXCLUSION",
        "PREGNANCY_EXCLUSION", "ERCP_DAY_MIN", "ERCP_DAY_OBSERVED",
        "CHOLE_COMPLETE_PRESENT", "CHOLE_COMPLETE_DAY_MIN", "CHOLE_PARTIAL_PRESENT",
    }
    missing = required_columns - set(index.columns)
    if missing:
        raise RuntimeError(f"index input missing required columns: {sorted(missing)}")

    funnel: dict[str, int] = {"candidate_ercp_broad": int(len(index))}
    mask = numeric(index, "PHENOTYPE_STRICT").eq(1)
    index = index.loc[mask].copy()
    funnel["strict_phenotype"] = int(len(index))
    index = index.loc[numeric(index, "RESIDENT").eq(1)].copy()
    funnel["state_resident"] = int(len(index))
    index = index.loc[numeric(index, "DIED").eq(0)].copy()
    funnel["survived_index_discharge"] = int(len(index))
    exclusion = (
        numeric(index, "MALIGNANCY_EXCLUSION").eq(1)
        | numeric(index, "PRIOR_CHOLE_EXCLUSION").eq(1)
        | numeric(index, "PREGNANCY_EXCLUSION").eq(1)
    )
    index = index.loc[~exclusion].copy()
    funnel["clinical_exclusions_removed"] = int(len(index))
    index = index.loc[numeric(index, "ERCP_DAY_OBSERVED").eq(1)].copy()
    funnel["ercp_day_observed"] = int(len(index))
    index = index.loc[numeric(index, "CHOLE_PARTIAL_PRESENT").fillna(0).eq(0)].copy()
    funnel["partial_chole_excluded"] = int(len(index))

    day = numeric(index, "NRD_DAYSTOEVENT")
    los = numeric(index, "LOS")
    month = numeric(index, "DMONTH")
    index = index.loc[day.notna() & los.notna() & day.ge(0) & los.ge(0) & month.between(1, 11, inclusive="both")].copy()
    funnel["complete_30d_followup"] = int(len(index))

    ercp_day = numeric(index, "ERCP_DAY_MIN")
    chole_day = numeric(index, "CHOLE_COMPLETE_DAY_MIN")
    chole_present = numeric(index, "CHOLE_COMPLETE_PRESENT").eq(1)
    valid_treated = chole_present & chole_day.notna() & ercp_day.notna() & chole_day.ge(ercp_day)
    valid_control = ~chole_present
    index["TREATMENT_CHOLE"] = np.where(valid_treated, 1, np.where(valid_control, 0, np.nan))
    index = index.loc[index["TREATMENT_CHOLE"].notna()].copy()
    index["TREATMENT_CHOLE"] = index["TREATMENT_CHOLE"].astype("int8")
    funnel["valid_treatment_sequence"] = int(len(index))

    index = index.sort_values(["NRD_VISITLINK", "NRD_DAYSTOEVENT", "KEY_NRD"], kind="mergesort")
    index = index.drop_duplicates(subset=["NRD_VISITLINK"], keep="first").copy()
    funnel["earliest_patient_year_index"] = int(len(index))
    if not len(index) or index["TREATMENT_CHOLE"].nunique() != 2:
        raise RuntimeError("primary cohort lacks two treatment arms")

    linked["NRD_VISITLINK"] = linked["NRD_VISITLINK"].astype("string")
    linked["KEY_NRD"] = linked["KEY_NRD"].astype("string")
    linked_groups = {key: group for key, group in linked.groupby("NRD_VISITLINK", sort=False)}
    dx1 = next((name for name in linked.columns if name.upper() == "I10_DX1"), None)
    if dx1 is None:
        raise RuntimeError("I10_DX1 missing from linked discharges")

    outcomes: list[dict[str, Any]] = []
    for row in index.itertuples(index=False):
        record = row._asdict()
        visit = str(record["NRD_VISITLINK"])
        key = str(record["KEY_NRD"])
        discharge_day = float(record["NRD_DAYSTOEVENT"]) + float(record["LOS"])
        future = linked_groups.get(visit)
        any30 = biliary30 = any90 = biliary90 = 0
        death30 = 0
        readmit_days30 = 0.0
        readmit_charges30 = 0.0
        first_delta = np.nan
        if future is not None:
            future = future.loc[future["KEY_NRD"].astype("string") != key].copy()
            future["_DELTA"] = numeric(future, "NRD_DAYSTOEVENT") - discharge_day
            future = future.loc[future["_DELTA"].between(1, 90, inclusive="both")].copy()
            if "ELECTIVE" in future.columns:
                future = future.loc[numeric(future, "ELECTIVE").eq(0)].copy()
            future = future.sort_values(["_DELTA", "KEY_NRD"], kind="mergesort")
            if len(future):
                first_delta = float(future.iloc[0]["_DELTA"])
                within30 = future.loc[future["_DELTA"].le(30)]
                any90 = 1
                biliary90 = int(any(biliary_principal(normalized(value)) for value in future[dx1]))
                if len(within30):
                    any30 = 1
                    biliary30 = int(any(biliary_principal(normalized(value)) for value in within30[dx1]))
                    death30 = int(numeric(within30, "DIED").fillna(0).eq(1).any())
                    readmit_days30 = float(numeric(within30, "LOS").fillna(0).sum())
                    readmit_charges30 = float(numeric(within30, "TOTCHG").fillna(0).sum())
        outcomes.append(
            {
                "KEY_NRD": record["KEY_NRD"],
                "READMIT30_ALL": any30,
                "READMIT30_BILIARY": biliary30,
                "READMIT30_DEATH": death30,
                "READMIT90_ALL": any90,
                "READMIT90_BILIARY": biliary90,
                "FIRST_UNPLANNED_READMIT_DAYS": first_delta,
                "READMIT_LOS30_TOTAL": readmit_days30,
                "READMIT_TOTCHG30_TOTAL": readmit_charges30,
            }
        )
    index = index.merge(pd.DataFrame(outcomes), on="KEY_NRD", how="left", validate="one_to_one")
    index = merge_aux(index, severity_path, "KEY_NRD", "SEVERITY")
    index = merge_aux(index, hospital_path, "HOSP_NRD", "HOSPITAL")
    dxpr_path = private_path(private_year / f"dxpr_linked_{args.year}.parquet")
    if dxpr_path.is_file():
        index = merge_aux(index, dxpr_path, "KEY_NRD", "DXPR")

    final = private_path(private_year / f"analysis_cohort_{args.year}.parquet")
    pending = final.with_suffix(".parquet.tmp")
    if final.exists() or pending.exists():
        raise RuntimeError(f"refusing to overwrite {final}")
    index.to_parquet(pending, index=False, compression="zstd")
    os.chmod(pending, 0o600)
    os.replace(pending, final)
    private_manifest = {
        "status": "PASS",
        "year": args.year,
        "rows": int(len(index)),
        "treatment_counts": {str(k): int(v) for k, v in index["TREATMENT_CHOLE"].value_counts().sort_index().items()},
        "outcome_counts": {
            name: int(numeric(index, name).fillna(0).sum())
            for name in ("READMIT30_BILIARY", "READMIT30_ALL", "READMIT90_BILIARY", "READMIT90_ALL")
        },
        "same_day_treated": int(
            (numeric(index, "SAME_DAY_ERCP_CHOLE").fillna(0).eq(1) & index["TREATMENT_CHOLE"].eq(1)).sum()
        ),
        "funnel": funnel,
        "private_output_sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        "patient_level_output_exported": False,
    }
    atomic_json(private_year / f"analysis_cohort_manifest_{args.year}.json", private_manifest)

    aggregate = public_aggregate(private_manifest)
    aggregate_dir = aggregate_path(Path(args.aggregate_output_dir))
    aggregate_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(aggregate_dir / f"cohort_summary_{args.year}.json", aggregate)
    return aggregate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, choices=[2018, 2019, 2020], required=True)
    parser.add_argument("--private-root", required=True)
    parser.add_argument(
        "--aggregate-output-dir",
        required=True,
    )
    args = parser.parse_args()
    global PRIVATE_ROOT, PROJECT_ROOT
    PRIVATE_ROOT = Path(args.private_root).resolve()
    PROJECT_ROOT = Path(args.aggregate_output_dir).resolve().parent
    result = run(args)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
