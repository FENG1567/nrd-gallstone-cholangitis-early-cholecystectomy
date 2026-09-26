#!/usr/bin/env python3
"""Aggregate-only streaming frequency audit for NRD CORE archives.

This program accepts a CSV stream on stdin.  It never emits or persists source
rows; all files it writes are aggregate tables or aggregate JSON.  The wrapper
script is responsible for archive access and supplies only numeric exit codes
to ``finalize`` after the pipe has closed.
"""

from __future__ import annotations

import argparse
import collections
import csv
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable


REQUIRED_CONCEPTS = {
    "acute_cholangitis_strict",
    "cholangitis_unspecified_with_duct_stone",
    "other_cholangitis",
    "gallstone_evidence_broad",
    "therapeutic_biliary_ercp_core",
    "cholecystectomy_complete",
    "cholecystectomy_partial",
}
REQUESTED_VARIABLES = [
    "AGE", "ELECTIVE", "FEMALE", "PAY1", "HOSP_NRD", "NRD_VISITLINK",
    "NRD_DAYSTOEVENT", "DIED", "DISPUNIFORM", "LOS", "DISCWT", "NRD_STRATUM",
]
MALIGNANCY_PREFIXES = tuple(f"C{i}" for i in range(22, 27))
PREGNANCY_PREFIXES = tuple([f"O{i:02d}" for i in range(0, 10)] + ["O9A"])


def atomic_text(path: Path, text: str) -> None:
    pending = path.with_name(path.name + ".tmp")
    with pending.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    with path.open("r", encoding="utf-8") as handle:
        json.load(handle)


def atomic_csv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    pending = path.with_name(path.name + ".tmp")
    with pending.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        parsed = csv.DictReader(handle)
        if parsed.fieldnames != fields:
            raise RuntimeError(f"CSV schema mismatch for {path.name}")
        for _ in parsed:
            pass


def normalized(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(value).strip().upper())


def missing(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def as_int(value: Any) -> int | None:
    if missing(value):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not number.is_integer():
        return None
    return int(number)


def code_set(text: str) -> set[str]:
    return {normalized(item) for item in text.split(";") if normalized(item)}


def load_ontology(path: Path) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            concept = str(row.get("concept", "")).strip()
            if concept in REQUIRED_CONCEPTS:
                found[concept] = code_set(str(row.get("exact_codes", "")))
    missing = sorted(concept for concept in REQUIRED_CONCEPTS if not found.get(concept))
    if missing:
        raise ValueError(f"Ontology missing required nonempty concepts: {missing}")
    if "K8301" in found["other_cholangitis"] or "K8301" in found["acute_cholangitis_strict"]:
        raise ValueError("K83.01/primary sclerosing cholangitis must not enter an acute phenotype")
    if found["cholecystectomy_complete"] != {"0FT40ZZ", "0FT44ZZ"}:
        raise ValueError("Primary cholecystectomy codes must be complete gallbladder resection")
    if any(code.startswith(("0F7D", "0F9D", "0FCD", "0FHD", "0FPD")) for code in found["therapeutic_biliary_ercp_core"]):
        raise ValueError("Pancreatic-duct codes must not enter therapeutic biliary ERCP")
    return found


def sort_numbered(columns: list[str], prefix: str) -> list[str]:
    return sorted(columns, key=lambda col: int(re.fullmatch(prefix + r"(\d+)", col).group(1)))


def build_layout(header: list[str]) -> dict[str, Any]:
    clean = [item.strip() for item in header]
    if not clean or any(not item for item in clean) or len(set(clean)) != len(clean):
        raise ValueError("CSV header is empty, contains blanks, or contains duplicate names")
    upper = {name.upper(): name for name in clean}
    dx = sort_numbered(
        [name for name in clean if re.fullmatch(r"(?:I10_)?DX\d+", name.upper())],
        r"(?:I10_)?DX",
    )
    pr = sort_numbered(
        [name for name in clean if re.fullmatch(r"(?:I10_)?PR\d+", name.upper())],
        r"(?:I10_)?PR",
    )
    prday = {re.fullmatch(r"PRDAY(\d+)", name.upper()).group(1): name
             for name in clean if re.fullmatch(r"PRDAY\d+", name.upper())}
    paired = {
        column: prday.get(re.fullmatch(r"(?:I10_)?PR(\d+)", column.upper()).group(1))
        for column in pr
    }
    variable_map = {wanted: upper.get(wanted) for wanted in REQUESTED_VARIABLES}
    return {
        "header": clean,
        "dx": dx,
        "principal": upper.get("I10_DX1") or upper.get("DX1"),
        "pr": pr,
        "prday": prday, "paired": paired, "variables": variable_map,
    }


class Aggregator:
    def __init__(self, year: int, layout: dict[str, Any], ontology: dict[str, set[str]], engine: str) -> None:
        self.year = year
        self.layout = layout
        self.ontology = ontology
        self.engine = engine
        self.rows = 0
        self.variable_nonmissing = collections.Counter()
        self.diag_any = collections.Counter()
        self.diag_principal = collections.Counter()
        self.proc_any = collections.Counter()
        self.counts = collections.Counter()
        self.funnel = collections.Counter()
        self.day = collections.Counter()

    def value(self, row: dict[str, Any], requested: str) -> Any:
        column = self.layout["variables"].get(requested)
        return row.get(column) if column else None

    def process(self, row: dict[str, Any]) -> None:
        self.rows += 1
        for wanted, actual in self.layout["variables"].items():
            if actual and not missing(row.get(actual)):
                self.variable_nonmissing[wanted] += 1

        dx_codes = {normalized(row.get(column)) for column in self.layout["dx"]}
        dx_codes.discard("")
        principal = normalized(row.get(self.layout["principal"])) if self.layout["principal"] else ""
        diagnosis_concepts = (
            "acute_cholangitis_strict", "cholangitis_unspecified_with_duct_stone",
            "other_cholangitis", "gallstone_evidence_broad",
        )
        for code in set().union(*(self.ontology[name] for name in diagnosis_concepts)):
            if code in dx_codes:
                self.diag_any[code] += 1
            if code == principal:
                self.diag_principal[code] += 1

        strict = bool(dx_codes & self.ontology["acute_cholangitis_strict"])
        unspecified = bool(dx_codes & self.ontology["cholangitis_unspecified_with_duct_stone"])
        other = bool(dx_codes & self.ontology["other_cholangitis"])
        stone = bool(dx_codes & self.ontology["gallstone_evidence_broad"])
        main = strict or unspecified
        broad = main or (other and stone)
        self.counts["strict_any"] += int(strict)
        self.counts["strict_principal"] += int(principal in self.ontology["acute_cholangitis_strict"])
        self.counts["unspecified_cholangitis_stone_any"] += int(unspecified)
        self.counts["main_any"] += int(main)
        self.counts["other_cholangitis_any"] += int(other)
        self.counts["gallstone_any"] += int(stone)
        self.counts["broad_any"] += int(broad)
        self.counts["malignancy_proxy_any"] += int(any(code.startswith(MALIGNANCY_PREFIXES) for code in dx_codes))
        self.counts["prior_chole_proxy_any"] += int("Z9049" in dx_codes)
        self.counts["pregnancy_proxy_any"] += int(any(code.startswith(PREGNANCY_PREFIXES) for code in dx_codes))

        ercp_positions: list[tuple[str, int | None]] = []
        chole_positions: list[tuple[str, int | None]] = []
        for column in self.layout["pr"]:
            code = normalized(row.get(column))
            if not code:
                continue
            paired_day = self.layout["paired"].get(column)
            day_value = as_int(row.get(paired_day)) if paired_day else None
            if code in self.ontology["therapeutic_biliary_ercp_core"]:
                self.proc_any[code] += 1
                ercp_positions.append((column, day_value))
            if code in self.ontology["cholecystectomy_complete"]:
                self.proc_any[code] += 1
                chole_positions.append((column, day_value))
            if code in self.ontology["cholecystectomy_partial"]:
                self.proc_any[code] += 1
                self.counts["partial_chole_any"] += 1

        ercp = bool(ercp_positions)
        chole = bool(chole_positions)
        self.counts["therapeutic_biliary_ercp_any"] += int(ercp)
        self.counts["complete_chole_any"] += int(chole)
        self.counts["ercp_chole_overlap"] += int(ercp and chole)
        if strict:
            self.counts["strict_ercp"] += int(ercp)
            self.counts["strict_chole"] += int(chole)
            self.counts["strict_ercp_chole_overlap"] += int(ercp and chole)
        if broad:
            self.counts["broad_ercp"] += int(ercp)
            self.counts["broad_chole"] += int(chole)
            self.counts["broad_ercp_chole_overlap"] += int(ercp and chole)

        self._day_stats("ercp", ercp_positions)
        self._day_stats("chole", chole_positions)
        if ercp and chole:
            self.day["overlap_rows"] += 1
            ercp_days = [day for _, day in ercp_positions if day is not None]
            chole_days = [day for _, day in chole_positions if day is not None]
            if ercp_days and chole_days:
                self.day["overlap_both_days"] += 1
                first_ercp, first_chole = min(ercp_days), min(chole_days)
                if first_ercp == first_chole:
                    self.day["same_day"] += 1
                elif first_ercp < first_chole:
                    self.day["ercp_before_chole"] += 1
                else:
                    self.day["chole_before_ercp"] += 1

        self._funnel(row)

    def _day_stats(self, label: str, positions: list[tuple[str, int | None]]) -> None:
        if not positions:
            return
        self.day[f"{label}_procedure_rows"] += 1
        available = [self.layout["paired"].get(column) is not None for column, _ in positions]
        values = [value for _, value in positions if value is not None]
        self.day[f"{label}_rows_with_paired_field"] += int(any(available))
        self.day[f"{label}_rows_with_nonmissing_day"] += int(bool(values))
        self.day[f"{label}_rows_missing_day"] += int(not values)

    def _funnel(self, row: dict[str, Any]) -> None:
        self.funnel["all_rows"] += 1
        age = as_int(self.value(row, "AGE"))
        if age is None:
            return
        self.funnel["age_known"] += 1
        if age < 18:
            return
        self.funnel["adult"] += 1
        elective = as_int(self.value(row, "ELECTIVE"))
        if elective != 0:
            return
        self.funnel["adult_non_elective"] += 1
        visit = self.value(row, "NRD_VISITLINK")
        days = as_int(self.value(row, "NRD_DAYSTOEVENT"))
        los = as_int(self.value(row, "LOS"))
        if missing(visit) or days is None or days < 0 or los is None or los < 0:
            return
        self.funnel["valid_linkage_time"] += 1
        discharge_day = days + los
        if discharge_day + 30 <= 365:
            self.funnel["complete_30d_structural"] += 1
        if discharge_day + 90 <= 365:
            self.funnel["complete_90d_structural"] += 1

    def outputs(self) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        variables: list[dict[str, Any]] = []
        for wanted in REQUESTED_VARIABLES:
            actual = self.layout["variables"].get(wanted)
            nonmissing = self.variable_nonmissing[wanted] if actual else 0
            variables.append({"requested_field": wanted, "actual_column": actual or "", "present": int(bool(actual)),
                              "total_rows": self.rows, "nonmissing": nonmissing, "missing": self.rows - nonmissing,
                              "missing_rate": (self.rows - nonmissing) / self.rows if self.rows else 0.0})
        variables.extend([
            {"requested_field": "DX_COLUMNS", "actual_column": ";".join(self.layout["dx"]), "present": int(bool(self.layout["dx"])), "total_rows": self.rows, "nonmissing": "", "missing": "", "missing_rate": ""},
            {"requested_field": "PR_COLUMNS", "actual_column": ";".join(self.layout["pr"]), "present": int(bool(self.layout["pr"])), "total_rows": self.rows, "nonmissing": "", "missing": "", "missing_rate": ""},
            {"requested_field": "PRDAY_COLUMNS", "actual_column": ";".join(self.layout["prday"].values()), "present": int(bool(self.layout["prday"])), "total_rows": self.rows, "nonmissing": "", "missing": "", "missing_rate": ""},
        ])
        code_rows: list[dict[str, Any]] = []
        for concept in ("acute_cholangitis_strict", "cholangitis_unspecified_with_duct_stone", "other_cholangitis", "gallstone_evidence_broad"):
            for code in sorted(self.ontology[concept]):
                code_rows.append({"group": concept, "code": code, "definition": "discharge row with normalized exact diagnosis code", "any_row_count": self.diag_any[code], "principal_row_count": self.diag_principal[code], "denominator_rows": self.rows})
        for concept in ("therapeutic_biliary_ercp_core", "cholecystectomy_complete", "cholecystectomy_partial"):
            for code in sorted(self.ontology[concept]):
                code_rows.append({"group": concept, "code": code, "definition": "discharge row with normalized exact procedure code", "any_row_count": self.proc_any[code], "principal_row_count": "", "denominator_rows": self.rows})
        for label, value in sorted(self.counts.items()):
            code_rows.append({"group": "derived", "code": label, "definition": "row-level pre-specified aggregate", "any_row_count": value, "principal_row_count": "", "denominator_rows": self.rows})
        funnel_defs = {
            "all_rows": "all streamed CORE rows", "age_known": "AGE nonmissing and integer", "adult": "AGE >= 18",
            "adult_non_elective": "adult and ELECTIVE == 0", "valid_linkage_time": "adult non-elective with nonmissing VisitLink, nonnegative DaysToEvent and LOS",
            "complete_30d_structural": "valid linkage/time and DaysToEvent + LOS + 30 <= 365", "complete_90d_structural": "valid linkage/time and DaysToEvent + LOS + 90 <= 365",
        }
        funnel_rows = [{"stage": key, "definition": funnel_defs[key], "count": self.funnel[key], "denominator": self.rows} for key in funnel_defs]
        frequency = {
            "year": self.year, "engine": self.engine, "row_count": self.rows,
            "denominator_definitions": {"code_counts": "discharge rows after normalization; one row contributes at most once per exact code", "funnel": "each stage definition is supplied in cohort_funnel CSV", "followup": "calendar structural eligibility uses index DaysToEvent + LOS and a 365-day year"},
            "layout": {"diagnosis_columns": self.layout["dx"], "principal_diagnosis_column": self.layout["principal"], "procedure_columns": self.layout["pr"], "procedure_day_pair_count": sum(value is not None for value in self.layout["paired"].values()), "procedure_day_column_count": len(self.layout["prday"])},
            "funnel": dict(self.funnel), "derived_counts": dict(self.counts), "procedure_day_stats": dict(self.day),
            "estimand_gate": self.gate(), "ontology_exact_codes": {key: sorted(value) for key, value in self.ontology.items()},
        }
        return frequency, code_rows, variables, funnel_rows

    def gate(self) -> dict[str, Any]:
        absent = []
        if not self.layout["principal"] or not self.layout["dx"]:
            absent.append("DX1/diagnosis columns")
        if not self.layout["pr"]:
            absent.append("procedure columns")
        for field in ("NRD_VISITLINK", "NRD_DAYSTOEVENT", "HOSP_NRD", "DISCWT", "NRD_STRATUM"):
            if not self.layout["variables"].get(field):
                absent.append(field)
        if absent or self.rows == 0 or self.counts["strict_any"] == 0 or self.counts["therapeutic_biliary_ercp_any"] == 0 or self.counts["complete_chole_any"] == 0:
            return {"decision": "BLOCKED", "reasons": {"missing_required_columns": absent, "row_count": self.rows, "strict_any": self.counts["strict_any"], "therapeutic_biliary_ercp_any": self.counts["therapeutic_biliary_ercp_any"], "complete_chole_any": self.counts["complete_chole_any"]}}
        if not self.layout["prday"] or self.day["overlap_both_days"] == 0:
            return {"decision": "DOWNGRADE_T1", "reasons": {"procedure_day_columns": len(self.layout["prday"]), "overlap_rows_with_both_days": self.day["overlap_both_days"], "interpretation": "treatment prevalence is auditable but within-admission ordering cannot be established"}}
        return {"decision": "PASS", "reasons": {"row_count": self.rows, "strict_any": self.counts["strict_any"], "broad_any": self.counts["broad_any"], "therapeutic_biliary_ercp_any": self.counts["therapeutic_biliary_ercp_any"], "complete_chole_any": self.counts["complete_chole_any"], "overlap_rows_with_both_days": self.day["overlap_both_days"]}}


def header_from_stdin() -> list[str]:
    line = sys.stdin.buffer.readline()
    if not line:
        raise ValueError("CSV stream ended before the header")
    return next(csv.reader([line.decode("utf-8-sig").rstrip("\r\n")]))


def aggregate(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    ontology = load_ontology(Path(args.ontology))
    header = header_from_stdin()
    layout = build_layout(header)
    engine = "pyarrow"
    try:
        import pyarrow as pa
        import pyarrow.csv as pacsv
        pa.set_cpu_count(8)
        pa.set_io_thread_count(8)
        reader = pacsv.open_csv(pa.input_stream(sys.stdin.buffer), read_options=pacsv.ReadOptions(column_names=header, block_size=16 * 1024 * 1024), parse_options=pacsv.ParseOptions(delimiter=","))
        agg = Aggregator(args.year, layout, ontology, engine)
        useful = list(dict.fromkeys(layout["dx"] + layout["pr"] + list(layout["prday"].values()) + [item for item in layout["variables"].values() if item]))
        for batch in reader:
            arrays = {name: batch.column(batch.schema.get_field_index(name)).to_pylist() for name in useful}
            for index in range(batch.num_rows):
                agg.process({name: values[index] for name, values in arrays.items()})
    except ImportError:
        engine = "stdlib_csv_fallback"
        agg = Aggregator(args.year, layout, ontology, engine)
        text_stream = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8-sig", newline="")
        for row in csv.DictReader(text_stream, fieldnames=header):
            agg.process(row)
    frequency, code_rows, variable_rows, funnel_rows = agg.outputs()
    atomic_json(output / f"frequency_{args.year}.json", frequency)
    atomic_csv(output / f"code_counts_{args.year}.csv", ["group", "code", "definition", "any_row_count", "principal_row_count", "denominator_rows"], code_rows)
    atomic_csv(output / f"variable_availability_{args.year}.csv", ["requested_field", "actual_column", "present", "total_rows", "nonmissing", "missing", "missing_rate"], variable_rows)
    atomic_csv(output / f"cohort_funnel_{args.year}.csv", ["stage", "definition", "count", "denominator"], funnel_rows)
    if agg.rows <= 0:
        raise RuntimeError("row_count must be positive")
    return 0


def finalize(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    frequency_path = output / f"frequency_{args.year}.json"
    if frequency_path.exists():
        with frequency_path.open("r", encoding="utf-8") as handle:
            frequency = json.load(handle)
        gate = frequency["estimand_gate"]
        rows = frequency["row_count"]
    else:
        gate, rows = {"decision": "BLOCKED", "reasons": {"aggregation_output": "missing"}}, 0
    statuses = {"outer_7z_exit": args.outer_exit, "inner_7z_exit": args.inner_exit, "prompt_filter_exit": args.prompt_filter_exit, "aggregator_exit": args.aggregator_exit}
    status = "PASS" if all(value == 0 for value in statuses.values()) and gate["decision"] != "BLOCKED" and rows > 0 else "BLOCKED"
    manifest = {"year": args.year, "status": status, "pipeline_exit_codes": statuses, "row_count": rows, "estimand_gate": gate, "patient_level_rows_persisted": False, "inner_zip_persisted_after_run": False}
    atomic_json(output / f"run_manifest_{args.year}.json", manifest)
    atomic_text(output / "README.md", "# NRD aggregate-only frequency pilot\n\nThis directory contains only aggregate counts, variable availability, funnel definitions, and a pipeline manifest. No patient-level CSV is retained.\n")
    return 0 if status == "PASS" else 2


def hash_outputs(args: argparse.Namespace) -> int:
    import hashlib
    output = Path(args.output_dir)
    allowed = [f"frequency_{args.year}.json", f"code_counts_{args.year}.csv", f"variable_availability_{args.year}.csv", f"cohort_funnel_{args.year}.csv", f"run_manifest_{args.year}.json", "README.md"]
    for name in allowed:
        if not (output / name).is_file():
            raise RuntimeError(f"required output missing: {name}")
    lines = []
    for name in allowed:
        digest = hashlib.sha256((output / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
    atomic_text(output / "SHA256SUMS", "\n".join(lines) + "\n")
    return 0


def verify(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    with (output / f"frequency_{args.year}.json").open("r", encoding="utf-8") as handle:
        frequency = json.load(handle)
    with (output / f"run_manifest_{args.year}.json").open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if frequency["row_count"] <= 0 or manifest["pipeline_exit_codes"] != {"outer_7z_exit": 0, "inner_7z_exit": 0, "prompt_filter_exit": 0, "aggregator_exit": 0}:
        raise RuntimeError("row count or pipeline invariants failed")
    for filename in (f"code_counts_{args.year}.csv", f"variable_availability_{args.year}.csv", f"cohort_funnel_{args.year}.csv"):
        with (output / filename).open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                for field in ("any_row_count", "principal_row_count", "denominator_rows", "total_rows", "nonmissing", "missing", "count", "denominator"):
                    if field in row and row[field] not in (None, "") and int(float(row[field])) < 0:
                        raise RuntimeError(f"negative aggregate count in {filename}")
    return 0


def fixture_test(_: argparse.Namespace) -> int:
    ontology = {
        "acute_cholangitis_strict": {"K8032", "K8033", "K8036", "K8037"},
        "cholangitis_unspecified_with_duct_stone": {"K8030", "K8031"},
        "other_cholangitis": {"K830", "K8309"},
        "gallstone_evidence_broad": {"K8032", "K8030", "K8050"},
        "therapeutic_biliary_ercp_core": {"0FC98ZZ", "0F798DZ"},
        "cholecystectomy_complete": {"0FT40ZZ", "0FT44ZZ"},
        "cholecystectomy_partial": {"0FB40ZZ", "0FB44ZZ"},
    }
    header = ["AGE", "ELECTIVE", "FEMALE", "PAY1", "HOSP_NRD", "NRD_VisitLink", "NRD_DaysToEvent", "DIED", "DISPUNIFORM", "LOS", "DISCWT", "NRD_STRATUM", "DX1", "DX2", "PR1", "PR2", "PRDAY1", "PRDAY2"]
    layout = build_layout(header)
    agg = Aggregator(2019, layout, ontology, "fixture")
    rows = [
        dict(zip(header, [55, 0, 1, 1, 10, "A", 100, 0, 1, 3, 1.0, 1, "K80.32", "", "0FC98ZZ", "0FT44ZZ", 1, 2])),
        dict(zip(header, [60, 0, 0, 2, 20, "B", 300, 0, 1, 2, 2.0, 2, "K83.09", "K80.50", "0F7D8DZ", "0FB44ZZ", 6, 5])),
        dict(zip(header, [17, 0, 1, 1, 30, "C", 10, 0, 1, 1, 1.0, 3, "K83.01", "K80.50", "0FJB8ZZ", "", "", ""])),
        dict(zip(header, [45, 0, 1, 1, 40, "D", 20, 0, 1, 1, 1.0, 4, "K80.30", "", "0F798DZ", "0FT40ZZ", 6, 5])),
    ]
    for row in rows:
        agg.process(row)
    frequency, code_rows, variables, funnel = agg.outputs()
    assert normalized(" k83.01 ") == "K8301"
    assert frequency["row_count"] == 4
    assert frequency["derived_counts"]["strict_any"] == 1
    assert frequency["derived_counts"]["main_any"] == 2
    assert frequency["derived_counts"]["broad_any"] == 3
    assert frequency["derived_counts"]["therapeutic_biliary_ercp_any"] == 2
    assert frequency["derived_counts"]["partial_chole_any"] == 1
    assert frequency["derived_counts"]["ercp_chole_overlap"] == 2
    assert frequency["procedure_day_stats"]["ercp_before_chole"] == 1
    assert frequency["procedure_day_stats"]["chole_before_ercp"] == 1
    assert frequency["procedure_day_stats"].get("ercp_rows_missing_day", 0) == 0
    assert frequency["funnel"]["adult"] == 3
    assert frequency["funnel"]["complete_90d_structural"] == 2
    assert any(row["requested_field"] == "NRD_VISITLINK" and row["present"] == 1 for row in variables)
    assert len(code_rows) >= 16 and len(funnel) == 7
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("aggregate", "finalize", "hash-outputs", "verify"):
        item = sub.add_parser(name)
        item.add_argument("--year", type=int, required=True)
        item.add_argument("--output-dir", required=True)
    aggregate_parser = sub.choices["aggregate"]
    aggregate_parser.add_argument("--ontology", required=True)
    final_parser = sub.choices["finalize"]
    final_parser.add_argument("--outer-exit", type=int, required=True)
    final_parser.add_argument("--inner-exit", type=int, required=True)
    final_parser.add_argument("--prompt-filter-exit", type=int, required=True)
    final_parser.add_argument("--aggregator-exit", type=int, required=True)
    sub.add_parser("fixture-test")
    args = parser.parse_args()
    if args.command == "aggregate":
        return aggregate(args)
    if args.command == "finalize":
        return finalize(args)
    if args.command == "hash-outputs":
        return hash_outputs(args)
    if args.command == "verify":
        return verify(args)
    return fixture_test(args)


if __name__ == "__main__":
    raise SystemExit(main())
