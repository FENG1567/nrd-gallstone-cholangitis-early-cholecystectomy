#!/usr/bin/env python3
"""Extract candidate-linked NRD Severity or Hospital rows to private storage.

No patient-level value is printed. Decrypted archives and CSV rows are streamed
in memory; only filtered Parquet files beneath PRIVATE_ROOT are persisted.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import json
import os
import re
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from nested_zip_frequency_v7 import (
    SevenZipPayload,
    choose_member,
    extract_outer_to_memfd,
    list_archive_entries_memfd,
    set_runtime_limits,
    start_inner_stream,
)


# Both roots are configured by the authorized user at run time.  No server or
# home-directory path is embedded in this public package.
PRIVATE_ROOT = Path(os.environ.get("NRD_PRIVATE_ROOT", ".")).resolve()
PROJECT_ROOT = Path(os.environ.get("NRD_PROJECT_ROOT", ".")).resolve()


def safe_private_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == PRIVATE_ROOT or PRIVATE_ROOT not in resolved.parents:
        raise RuntimeError(f"patient-level output must be a child of {PRIVATE_ROOT}")
    if resolved == PROJECT_ROOT or PROJECT_ROOT in resolved.parents:
        raise RuntimeError("patient-level output is forbidden inside the project tree")
    return resolved


def parse_sas_columns(path: Path) -> list[str]:
    names: list[str] = []
    in_input = False
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not in_input and stripped.upper() == "INPUT":
            in_input = True
            continue
        if not in_input:
            continue
        if stripped == ";":
            break
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s+:", stripped)
        if match:
            names.append(match.group(1))
    folded = {name.upper() for name in names}
    if not names or len(names) != len(folded):
        raise RuntimeError(f"invalid auxiliary SAS INPUT layout: fields={len(names)}")
    return names


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(pending, 0o600)
    os.replace(pending, path)


def source_ids(private_year: Path, mode: str) -> tuple[str, set[str]]:
    linked = safe_private_path(private_year / f"linked_discharges_{private_year.name}.parquet")
    if not linked.is_file():
        raise RuntimeError(f"linked CORE file is missing: {linked}")
    column = "KEY_NRD" if mode in {"severity", "dxpr"} else "HOSP_NRD"
    schema = pq.read_schema(linked)
    actual = next((name for name in schema.names if name.upper() == column), None)
    if actual is None:
        raise RuntimeError(f"{column} not found in linked CORE schema")
    values = pq.read_table(linked, columns=[actual]).column(0).to_pylist()
    selected = {str(value) for value in values if value is not None and str(value)}
    if not selected:
        raise RuntimeError(f"no {column} identifiers available")
    return column, selected


def run(args: argparse.Namespace, password: bytes) -> dict[str, Any]:
    private_year = safe_private_path(Path(args.private_root) / str(args.year))
    private_year.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(private_year, 0o700)
    final = safe_private_path(private_year / f"{args.mode}_linked_{args.year}.parquet")
    pending = final.with_suffix(".parquet.tmp")
    manifest_path = safe_private_path(private_year / f"{args.mode}_manifest_{args.year}.json")
    if final.exists() or pending.exists() or manifest_path.exists():
        raise RuntimeError("refusing to overwrite an existing auxiliary extraction")

    full_header = parse_sas_columns(Path(args.sas_load_program))
    key_name, wanted = source_ids(private_year, args.mode)
    key_actual = next((name for name in full_header if name.upper() == key_name), None)
    if key_actual is None:
        raise RuntimeError(f"{key_name} absent from {args.sas_load_program}")
    if args.mode == "dxpr":
        header = [
            name
            for name in full_header
            if name.upper() in {"KEY_NRD", "HOSP_NRD", "CMR_VERSION", "PCLASS_VERSION", "DXCCSR_DEFAULT_DX1"}
            or name.upper().startswith("CMR_")
        ]
        if key_actual not in header:
            raise RuntimeError("DX/PR selected-column set lost KEY_NRD")
    else:
        header = full_header

    memfd, inner_hash, inner_size = extract_outer_to_memfd(
        Path(args.archive), args.outer_member, password
    )
    child = None
    writer = None
    rows_seen = rows_written = 0
    try:
        entries = list_archive_entries_memfd(memfd)
        members = [entry["Path"] for entry in entries]
        inner_member = choose_member(members, args.inner_member, ".csv")
        selected_entry = next(entry for entry in entries if entry["Path"] == inner_member)
        expected_bytes = int(selected_entry["Size"])
        child = start_inner_stream(memfd, inner_member, password)
        payload = SevenZipPayload(
            child,
            headerless_csv=True,
            headerless_min_fields=max(2, min(len(full_header), 5)),
        )
        convert = pacsv.ConvertOptions(
            include_columns=header,
            column_types={name: pa.string() for name in header},
            strings_can_be_null=True,
            null_values=["", "NA", "N/A", "NULL"],
        )
        reader = pacsv.open_csv(
            pa.input_stream(io.BufferedReader(payload, 8 * 1024 * 1024)),
            read_options=pacsv.ReadOptions(
                column_names=full_header,
                block_size=8 * 1024 * 1024,
                use_threads=True,
            ),
            parse_options=pacsv.ParseOptions(delimiter=",", newlines_in_values=False),
            convert_options=convert,
        )
        value_set = pa.array(sorted(wanted), type=pa.string())
        for batch in reader:
            rows_seen += batch.num_rows
            mask = pc.is_in(batch.column(batch.schema.get_field_index(key_actual)), value_set=value_set)
            filtered = batch.filter(mask)
            if filtered.num_rows:
                table = pa.Table.from_batches([filtered])
                if writer is None:
                    writer = pq.ParquetWriter(
                        pending, table.schema, compression="zstd", use_dictionary=True
                    )
                writer.write_table(table)
                rows_written += filtered.num_rows
        if payload.bytes_delivered != expected_bytes:
            raise RuntimeError(
                f"auxiliary CSV byte mismatch: {payload.bytes_delivered} != {expected_bytes}"
            )
        code, prompt_seen, transcript = child.finish()
        if code != 0 or not child.password_sent.is_set():
            raise RuntimeError(
                f"inner stream failure exit={code} prompt={prompt_seen} "
                f"diagnostic={transcript[-500:]!r}"
            )
        child = None
        if writer is None or rows_written == 0:
            raise RuntimeError("auxiliary extraction produced zero linked rows")
        writer.close()
        writer = None
        os.chmod(pending, 0o600)
        os.replace(pending, final)
    except BaseException:
        if writer is not None:
            writer.close()
        if pending.exists():
            pending.unlink()
        raise
    finally:
        if child is not None:
            try:
                child.process.terminate()
                child.finish()
            except Exception:
                pass
        os.close(memfd)

    result = {
        "status": "PASS",
        "mode": args.mode,
        "year": args.year,
        "rows_seen": rows_seen,
        "linked_rows": rows_written,
        "requested_identifiers": len(wanted),
        "expected_csv_bytes": expected_bytes,
        "inner_zip_bytes": inner_size,
        "inner_zip_sha256": inner_hash,
        "private_output_sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        "patient_level_output_exported": False,
    }
    atomic_json(manifest_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["severity", "hospital", "dxpr"])
    parser.add_argument("--year", type=int, choices=[2018, 2019, 2020], required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--outer-member", required=True)
    parser.add_argument("--inner-member")
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
        result = run(args, password)
        print(
            json.dumps(
                {k: v for k, v in result.items() if k != "private_output_sha256"},
                sort_keys=True,
            )
        )
    finally:
        password = b""
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
