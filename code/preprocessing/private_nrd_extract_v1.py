#!/usr/bin/env python3
"""Create server-only NRD index and linked-follow-up Parquet files.

Patient-level outputs are permitted only beneath PRIVATE_ROOT. Nothing from the
private Parquet files is printed. Passwords are read with getpass by the reused
double-AES wrapper and are never accepted as arguments or environment values.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO, Iterator

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from nested_zip_frequency_v7 import (
    SevenZipPayload,
    choose_member,
    extract_outer_to_memfd,
    list_archive_entries_memfd,
    parse_sas_input_layout,
    set_runtime_limits,
    start_inner_stream,
)
from stream_nrd_frequency import as_int, build_layout, load_ontology, normalized


# Both roots are configured by the authorized user at run time.  No server or
# home-directory path is embedded in this public package.
PRIVATE_ROOT = Path(os.environ.get("NRD_PRIVATE_ROOT", ".")).resolve()
PROJECT_ROOT = Path(os.environ.get("NRD_PROJECT_ROOT", ".")).resolve()
BASE_FIELDS = [
    "AGE", "AWEEKEND", "DIED", "DISCWT", "DISPUNIFORM", "DMONTH", "DQTR",
    "DRG", "DRGVER", "DRG_NOPOA", "ELECTIVE", "FEMALE", "HCUP_ED", "HOSP_NRD",
    "I10_BIRTH", "I10_DELIVERY", "I10_INJURY", "I10_MULTINJURY", "I10_NDX",
    "I10_NPR", "I10_SERVICELINE", "KEY_NRD", "LOS", "MDC", "MDC_NOPOA",
    "NRD_DAYSTOEVENT", "NRD_STRATUM", "NRD_VISITLINK", "PAY1", "PCLASS_ORPROC",
    "PL_NCHS", "REHABTRANSFER", "RESIDENT", "SAMEDAYEVENT", "TOTCHG", "YEAR",
    "ZIPINC_QRTL",
]
MALIGNANCY_PREFIXES = tuple(f"C{i}" for i in range(22, 27))
PREGNANCY_PREFIXES = tuple([f"O{i:02d}" for i in range(10)] + ["O9A"])


def safe_private_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == PRIVATE_ROOT or PRIVATE_ROOT not in resolved.parents:
        raise RuntimeError(f"patient-level output must be a child of {PRIVATE_ROOT}")
    if resolved == PROJECT_ROOT or PROJECT_ROOT in resolved.parents:
        raise RuntimeError("patient-level output is forbidden inside the project tree")
    return resolved


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def batched_core_stream(
    archive: Path,
    outer_member: str,
    password: bytes,
    header: list[str],
    include_columns: list[str],
    threads: int,
) -> Iterator[tuple[pa.RecordBatch, SevenZipPayload, Any, int, int, str]]:
    memfd, inner_hash, inner_size = extract_outer_to_memfd(archive, outer_member, password)
    child = None
    try:
        entries = list_archive_entries_memfd(memfd)
        members = [entry["Path"] for entry in entries]
        inner_member = choose_member(members, None, ".csv")
        selected = next(entry for entry in entries if entry["Path"] == inner_member)
        expected_bytes = int(selected["Size"])
        child = start_inner_stream(memfd, inner_member, password)
        payload = SevenZipPayload(child, headerless_csv=True)
        convert = pacsv.ConvertOptions(
            include_columns=include_columns,
            column_types={name: pa.string() for name in include_columns},
            strings_can_be_null=True,
            null_values=["", "NA", "N/A", "NULL"],
        )
        reader = pacsv.open_csv(
            pa.input_stream(io.BufferedReader(payload, 8 * 1024 * 1024)),
            read_options=pacsv.ReadOptions(column_names=header, block_size=8 * 1024 * 1024, use_threads=True),
            parse_options=pacsv.ParseOptions(delimiter=",", newlines_in_values=False),
            convert_options=convert,
        )
        for batch in reader:
            yield batch, payload, child, expected_bytes, inner_size, inner_hash
        if payload.bytes_delivered != expected_bytes:
            raise RuntimeError(f"CORE byte mismatch: {payload.bytes_delivered} != {expected_bytes}")
        code, prompt_seen, transcript = child.finish()
        if code != 0 or not child.password_sent.is_set():
            raise RuntimeError(f"inner stream failure exit={code} prompt={prompt_seen} diagnostic={transcript[-500:]!r}")
        child = None
    finally:
        if child is not None:
            try:
                child.process.terminate()
                child.finish()
            except Exception:
                pass
        os.close(memfd)


def actual_fields(header: list[str], requested: list[str]) -> list[str]:
    lookup = {name.upper(): name for name in header}
    return [lookup[name] for name in requested if name in lookup]


def index_pass(args: argparse.Namespace, password: bytes) -> dict[str, Any]:
    private_year = safe_private_path(Path(args.private_root) / str(args.year))
    private_year.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(private_year, 0o700)
    final = safe_private_path(private_year / f"index_candidates_{args.year}.parquet")
    if final.exists():
        raise RuntimeError(f"refusing to overwrite {final}")
    pending = final.with_suffix(".parquet.tmp")

    header = parse_sas_input_layout(Path(args.sas_load_program))
    layout = build_layout(header)
    ontology = load_ontology(Path(args.ontology))
    selected = list(dict.fromkeys(
        actual_fields(header, BASE_FIELDS)
        + layout["dx"] + layout["pr"] + list(layout["prday"].values())
    ))
    # Do not infer a batch-local schema: a first batch may have an entirely
    # missing procedure-day field and infer ``null``, while a later batch has
    # integer values.  A fixed schema keeps the streamed Parquet output stable.
    derived_columns = [
        "PHENOTYPE_STRICT", "PHENOTYPE_MAIN", "PHENOTYPE_BROAD",
        "MALIGNANCY_EXCLUSION", "PRIOR_CHOLE_EXCLUSION", "PREGNANCY_EXCLUSION",
        "ERCP_DAY_MIN", "ERCP_DAY_OBSERVED", "CHOLE_COMPLETE_DAY_MIN",
        "CHOLE_COMPLETE_PRESENT", "CHOLE_PARTIAL_PRESENT", "SAME_DAY_ERCP_CHOLE",
        "CHOLE_AFTER_ERCP",
    ]
    index_schema = pa.schema(
        [pa.field(name, pa.string()) for name in selected]
        + [pa.field(name, pa.int64()) for name in derived_columns]
    )
    writer = None
    rows_seen = rows_written = strict_rows = broad_rows = 0
    started = time.monotonic()
    last = started
    source_meta: dict[str, Any] = {}
    try:
        for batch, payload, _child, expected, inner_size, inner_hash in batched_core_stream(
            Path(args.archive), args.outer_member, password, header, selected, args.threads
        ):
            source_meta = {"expected_core_bytes": expected, "inner_zip_bytes": inner_size, "inner_zip_sha256": inner_hash}
            arrays = {name: batch.column(batch.schema.get_field_index(name)).to_pylist() for name in selected}
            out_rows: list[dict[str, Any]] = []
            for i in range(batch.num_rows):
                rows_seen += 1
                dx = {normalized(arrays[name][i]) for name in layout["dx"]}
                dx.discard("")
                strict = bool(dx & ontology["acute_cholangitis_strict"])
                unspecified = bool(dx & ontology["cholangitis_unspecified_with_duct_stone"])
                other = bool(dx & ontology["other_cholangitis"])
                stone = bool(dx & ontology["gallstone_evidence_broad"])
                main = strict or unspecified
                broad = main or (other and stone)
                if not broad:
                    continue
                row = {name: arrays[name][i] for name in selected}
                age = as_int(row.get(layout["variables"].get("AGE")))
                elective = as_int(row.get(layout["variables"].get("ELECTIVE")))
                visit = row.get(layout["variables"].get("NRD_VISITLINK"))
                days = as_int(row.get(layout["variables"].get("NRD_DAYSTOEVENT")))
                los = as_int(row.get(layout["variables"].get("LOS")))
                if age is None or age < 18 or elective != 0 or not visit or days is None or days < 0 or los is None or los < 0:
                    continue
                procedures: list[tuple[str, int | None]] = []
                choles: list[tuple[str, int | None]] = []
                partials: list[tuple[str, int | None]] = []
                for col in layout["pr"]:
                    code = normalized(row.get(col))
                    paired = layout["paired"].get(col)
                    day = as_int(row.get(paired)) if paired else None
                    if code in ontology["therapeutic_biliary_ercp_core"]:
                        procedures.append((code, day))
                    if code in ontology["cholecystectomy_complete"]:
                        choles.append((code, day))
                    if code in ontology["cholecystectomy_partial"]:
                        partials.append((code, day))
                if not procedures:
                    continue
                ercp_days = [d for _, d in procedures if d is not None]
                chole_days = [d for _, d in choles if d is not None]
                row.update({
                    "PHENOTYPE_STRICT": int(strict),
                    "PHENOTYPE_MAIN": int(main),
                    "PHENOTYPE_BROAD": int(broad),
                    "MALIGNANCY_EXCLUSION": int(any(c.startswith(MALIGNANCY_PREFIXES) for c in dx)),
                    "PRIOR_CHOLE_EXCLUSION": int("Z9049" in dx),
                    "PREGNANCY_EXCLUSION": int(any(c.startswith(PREGNANCY_PREFIXES) for c in dx)),
                    "ERCP_DAY_MIN": min(ercp_days) if ercp_days else None,
                    "ERCP_DAY_OBSERVED": int(bool(ercp_days)),
                    "CHOLE_COMPLETE_DAY_MIN": min(chole_days) if chole_days else None,
                    "CHOLE_COMPLETE_PRESENT": int(bool(choles)),
                    "CHOLE_PARTIAL_PRESENT": int(bool(partials)),
                    "SAME_DAY_ERCP_CHOLE": int(bool(ercp_days and chole_days and min(ercp_days) == min(chole_days))),
                    "CHOLE_AFTER_ERCP": int(bool(ercp_days and chole_days and min(chole_days) > min(ercp_days))),
                })
                out_rows.append(row)
                rows_written += 1
                strict_rows += int(strict)
                broad_rows += int(broad)
            if out_rows:
                table = pa.Table.from_pylist(out_rows, schema=index_schema)
                if writer is None:
                    writer = pq.ParquetWriter(pending, table.schema, compression="zstd", use_dictionary=True)
                writer.write_table(table)
            now = time.monotonic()
            if now - last >= 60:
                print(f"INDEX_PROGRESS year={args.year} rows_seen={rows_seen} candidates={rows_written}", flush=True)
                last = now
        if writer is None:
            raise RuntimeError("index pass produced zero candidates")
        writer.close(); writer = None
        os.chmod(pending, 0o600)
        os.replace(pending, final)
    except BaseException:
        if writer is not None:
            writer.close()
        if pending.exists():
            pending.unlink()
        raise
    result = {
        "status": "PASS", "mode": "index", "year": args.year,
        "rows_seen": rows_seen, "candidate_rows": rows_written,
        "strict_candidate_rows": strict_rows, "broad_candidate_rows": broad_rows,
        "private_output_sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        "patient_level_output_exported": False, **source_meta,
    }
    atomic_json(private_year / f"index_manifest_{args.year}.json", result)
    return result


def followup_pass(args: argparse.Namespace, password: bytes) -> dict[str, Any]:
    private_year = safe_private_path(Path(args.private_root) / str(args.year))
    index_path = safe_private_path(private_year / f"index_candidates_{args.year}.parquet")
    final = safe_private_path(private_year / f"linked_discharges_{args.year}.parquet")
    if not index_path.is_file() or final.exists():
        raise RuntimeError("index file missing or linked-discharge output already exists")
    pending = final.with_suffix(".parquet.tmp")
    index_table = pq.read_table(index_path, columns=["NRD_VisitLink"])
    visitlinks = {str(x) for x in index_table.column(0).to_pylist() if x is not None and str(x)}
    if not visitlinks:
        raise RuntimeError("candidate VisitLink set is empty")

    header = parse_sas_input_layout(Path(args.sas_load_program))
    layout = build_layout(header)
    follow_fields = list(dict.fromkeys(
        actual_fields(header, BASE_FIELDS) + layout["dx"] + layout["pr"] + list(layout["prday"].values())
    ))
    visit_col = layout["variables"]["NRD_VISITLINK"]
    writer = None
    rows_seen = rows_written = 0
    last = time.monotonic()
    source_meta: dict[str, Any] = {}
    value_set = pa.array(sorted(visitlinks), type=pa.string())
    try:
        for batch, payload, _child, expected, inner_size, inner_hash in batched_core_stream(
            Path(args.archive), args.outer_member, password, header, follow_fields, args.threads
        ):
            source_meta = {"expected_core_bytes": expected, "inner_zip_bytes": inner_size, "inner_zip_sha256": inner_hash}
            rows_seen += batch.num_rows
            mask = pc.is_in(batch.column(batch.schema.get_field_index(visit_col)), value_set=value_set)
            selected_batch = batch.filter(mask)
            if selected_batch.num_rows:
                table = pa.Table.from_batches([selected_batch])
                if writer is None:
                    writer = pq.ParquetWriter(pending, table.schema, compression="zstd", use_dictionary=True)
                writer.write_table(table)
                rows_written += selected_batch.num_rows
            now = time.monotonic()
            if now - last >= 60:
                print(f"FOLLOWUP_PROGRESS year={args.year} rows_seen={rows_seen} linked={rows_written}", flush=True)
                last = now
        if writer is None:
            raise RuntimeError("follow-up pass produced zero linked discharges")
        writer.close(); writer = None
        os.chmod(pending, 0o600)
        os.replace(pending, final)
    except BaseException:
        if writer is not None:
            writer.close()
        if pending.exists():
            pending.unlink()
        raise
    result = {
        "status": "PASS", "mode": "followup", "year": args.year,
        "rows_seen": rows_seen, "candidate_visitlinks": len(visitlinks),
        "linked_discharge_rows": rows_written,
        "private_output_sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        "patient_level_output_exported": False, **source_meta,
    }
    atomic_json(private_year / f"followup_manifest_{args.year}.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["index", "followup"])
    parser.add_argument("--year", type=int, choices=[2018, 2019, 2020], required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--outer-member", required=True)
    parser.add_argument("--ontology", required=True)
    parser.add_argument("--sas-load-program", required=True)
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--memory-gib", type=int, default=20)
    args = parser.parse_args()
    global PRIVATE_ROOT, PROJECT_ROOT
    PRIVATE_ROOT = Path(args.private_root).resolve()
    PROJECT_ROOT = Path(args.project_root).resolve()
    safe_private_path(Path(args.private_root) / str(args.year))
    set_runtime_limits(args.memory_gib, args.threads)
    password_text = getpass.getpass(f"NRD {args.year} archive password: ")
    if not password_text:
        raise RuntimeError("empty password refused")
    password = password_text.encode("utf-8")
    del password_text
    try:
        result = index_pass(args, password) if args.mode == "index" else followup_pass(args, password)
        print(json.dumps({k: v for k, v in result.items() if k not in {"private_output_sha256"}}, sort_keys=True))
    finally:
        password = b""
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
