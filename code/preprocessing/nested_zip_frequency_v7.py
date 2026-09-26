#!/usr/bin/env python3
"""Read a doubly encrypted NRD CORE archive and emit aggregate-only evidence.

The annual password is requested from a TTY with getpass and is never accepted
on the command line or stored in a file.  The inner ZIP is held in memory; the
patient-level CSV is streamed directly from that in-memory ZIP into PyArrow.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import datetime as dt
import getpass
import hashlib
import io
import json
import os
import fcntl
import pty
import resource
import re
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import termios
import zipfile
from pathlib import Path
from typing import BinaryIO

import pyzipper

from stream_nrd_frequency import (
    Aggregator,
    atomic_csv,
    atomic_json,
    atomic_text,
    build_layout,
    load_ontology,
)


ALLOWED_OUTPUTS = {
    "README.md",
    "SHA256SUMS",
    "code_counts_{year}.csv",
    "cohort_funnel_{year}.csv",
    "frequency_{year}.json",
    "run_manifest_{year}.json",
    "variable_availability_{year}.csv",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def set_runtime_limits(memory_gib: int, threads: int) -> None:
    if not 1 <= threads <= 8:
        raise ValueError("threads must be between 1 and 8")
    if memory_gib < 4 or memory_gib > 20:
        raise ValueError("memory-gib must be between 4 and 20")
    byte_limit = memory_gib * 1024**3
    resource.setrlimit(resource.RLIMIT_AS, (byte_limit, byte_limit))
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "ARROW_NUM_THREADS",
    ):
        os.environ[key] = str(threads)


def choose_member(names: list[str], preferred: str | None, suffix: str) -> str:
    files = [name for name in names if not name.endswith("/")]
    if preferred:
        exact = [name for name in files if name == preferred]
        if len(exact) == 1:
            return exact[0]
        folded = [name for name in files if name.casefold() == preferred.casefold()]
        if len(folded) == 1:
            return folded[0]
        raise RuntimeError(f"preferred member not found uniquely: {preferred!r}")
    candidates = [name for name in files if "core" in Path(name).name.casefold() and name.casefold().endswith(suffix)]
    if len(candidates) != 1:
        raise RuntimeError(f"CORE member selection is not unique: {candidates!r}")
    return candidates[0]


def parse_sas_input_layout(path: Path) -> list[str]:
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
    upper = {name.upper() for name in names}
    required = {"AGE", "I10_DX1", "I10_PR1", "PRDAY1", "NRD_VISITLINK", "NRD_DAYSTOEVENT"}
    if len(names) < 100 or not required.issubset(upper) or len(names) != len(upper):
        raise RuntimeError(
            f"invalid SAS INPUT layout: fields={len(names)}, missing={sorted(required - upper)}, "
            f"duplicates={len(names) - len(upper)}"
        )
    return names


def aggregate_stream(
    stream: BinaryIO,
    year: int,
    ontology_path: Path,
    threads: int,
    header: list[str],
) -> Aggregator:
    import pyarrow as pa
    import pyarrow.csv as pacsv

    pa.set_cpu_count(threads)
    pa.set_io_thread_count(threads)
    layout = build_layout(header)
    ontology = load_ontology(ontology_path)
    useful = list(
        dict.fromkeys(
            layout["dx"]
            + layout["pr"]
            + list(layout["prday"].values())
            + [value for value in layout["variables"].values() if value]
        )
    )
    missing_required = [name for name in ("I10_DX1",) if name not in {x.upper() for x in header}]
    if missing_required or not layout["pr"]:
        raise RuntimeError(f"required CORE fields absent: {missing_required}; procedure_count={len(layout['pr'])}")
    convert = pacsv.ConvertOptions(
        include_columns=useful,
        column_types={name: pa.string() for name in useful},
        strings_can_be_null=True,
        null_values=["", "NA", "N/A", "NULL"],
    )
    reader = pacsv.open_csv(
        pa.input_stream(stream),
        read_options=pacsv.ReadOptions(column_names=header, block_size=8 * 1024 * 1024, use_threads=True),
        parse_options=pacsv.ParseOptions(delimiter=",", newlines_in_values=False),
        convert_options=convert,
    )
    aggregator = Aggregator(year, layout, ontology, "7zip_memfd+pyarrow_headerless_stream")
    started = time.monotonic()
    last_report = started
    for batch in reader:
        arrays = {
            name: batch.column(batch.schema.get_field_index(name)).to_pylist()
            for name in useful
        }
        for index in range(batch.num_rows):
            aggregator.process({name: values[index] for name, values in arrays.items()})
        now = time.monotonic()
        if now - last_report >= 60:
            elapsed = max(now - started, 1.0)
            print(
                f"PROGRESS year={year} rows={aggregator.rows} rate={int(aggregator.rows / elapsed)}/s",
                flush=True,
            )
            last_report = now
    return aggregator


def write_outputs(
    output_dir: Path,
    year: int,
    aggregator: Aggregator,
    archive_path: Path,
    outer_member: str,
    inner_member: str,
    inner_zip_sha256: str,
    inner_zip_size: int,
    expected_csv_bytes: int,
    sas_layout_path: Path,
    sas_layout_sha256: str,
    sas_layout_field_count: int,
    started_at: str,
    memory_gib: int,
    threads: int,
) -> None:
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    frequency, code_rows, variable_rows, funnel_rows = aggregator.outputs()
    atomic_json(output_dir / f"frequency_{year}.json", frequency)
    atomic_csv(
        output_dir / f"code_counts_{year}.csv",
        ["group", "code", "definition", "any_row_count", "principal_row_count", "denominator_rows"],
        code_rows,
    )
    atomic_csv(
        output_dir / f"variable_availability_{year}.csv",
        ["requested_field", "actual_column", "present", "total_rows", "nonmissing", "missing", "missing_rate"],
        variable_rows,
    )
    atomic_csv(
        output_dir / f"cohort_funnel_{year}.csv",
        ["stage", "definition", "count", "denominator"],
        funnel_rows,
    )
    archive_stat = archive_path.stat()
    manifest = {
        "year": year,
        "status": "PASS" if frequency["estimand_gate"]["decision"] != "BLOCKED" else "BLOCKED",
        "started_at_utc": started_at,
        "completed_at_utc": utc_now(),
        "source": {
            "outer_archive": str(archive_path),
            "outer_size_bytes": archive_stat.st_size,
            "outer_mtime_ns": archive_stat.st_mtime_ns,
            "outer_member": outer_member,
            "inner_member": inner_member,
            "inner_zip_sha256_in_memory": inner_zip_sha256,
            "inner_zip_size_bytes_in_memory": inner_zip_size,
            "inner_csv_expected_uncompressed_bytes": expected_csv_bytes,
            "sas_input_layout": str(sas_layout_path),
            "sas_input_layout_sha256": sas_layout_sha256,
            "sas_input_layout_field_count": sas_layout_field_count,
        },
        "runtime": {
            "python": sys.version.split()[0],
            "pyzipper": getattr(pyzipper, "__version__", "unknown"),
            "threads": threads,
            "address_space_limit_gib": memory_gib,
        },
        "row_count": frequency["row_count"],
        "estimand_gate": frequency["estimand_gate"],
        "password_storage": "getpass_only_not_persisted",
        "patient_level_rows_persisted": False,
        "inner_zip_persisted": False,
    }
    atomic_json(output_dir / f"run_manifest_{year}.json", manifest)
    atomic_text(
        output_dir / "README.md",
        "# Aggregate-only NRD CORE frequency evidence\n\n"
        "The doubly encrypted CORE archive was decrypted in memory and the CSV was streamed. "
        "This directory contains only aggregate counts, aggregate missingness, cohort-funnel "
        "counts, provenance, and checksums. No patient-level row or decrypted archive is retained.\n",
    )
    verify_and_hash(output_dir, year)


def verify_and_hash(output_dir: Path, year: int) -> None:
    expected = {name.format(year=year) for name in ALLOWED_OUTPUTS}
    before_hash = {path.name for path in output_dir.iterdir() if path.is_file()}
    if before_hash - {"SHA256SUMS"} != expected - {"SHA256SUMS"}:
        raise RuntimeError(f"unexpected output set before hashing: {sorted(before_hash)}")
    with (output_dir / f"frequency_{year}.json").open("r", encoding="utf-8") as handle:
        frequency = json.load(handle)
    with (output_dir / f"run_manifest_{year}.json").open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if frequency["row_count"] <= 0 or frequency["row_count"] != manifest["row_count"]:
        raise RuntimeError("non-positive or inconsistent row count")
    if manifest["status"] != "PASS":
        raise RuntimeError(f"estimand gate is not PASS: {manifest['estimand_gate']}")
    if manifest["patient_level_rows_persisted"] or manifest["inner_zip_persisted"]:
        raise RuntimeError("patient-level persistence invariant failed")
    csv_expected = {
        f"code_counts_{year}.csv",
        f"cohort_funnel_{year}.csv",
        f"variable_availability_{year}.csv",
    }
    for name in csv_expected:
        with (output_dir / name).open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise RuntimeError(f"empty aggregate CSV: {name}")
    lines = []
    for path in sorted(output_dir.iterdir(), key=lambda item: item.name):
        if path.name == "SHA256SUMS":
            continue
        if not path.is_file() or path.name not in expected:
            raise RuntimeError(f"unexpected artifact: {path}")
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    atomic_text(output_dir / "SHA256SUMS", "\n".join(lines) + "\n")
    for line in (output_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        if hashlib.sha256((output_dir / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"checksum mismatch: {name}")
    final_set = {path.name for path in output_dir.iterdir() if path.is_file()}
    if final_set != expected:
        raise RuntimeError(f"final output set mismatch: {sorted(final_set)}")


class SevenZipPTY:
    """A 7-Zip child whose password channel is a PTY and data channel a pipe."""

    def __init__(self, command: list[str], password: bytes, pass_fds: tuple[int, ...] = ()) -> None:
        master_fd, slave_fd = pty.openpty()
        terminal_settings = termios.tcgetattr(slave_fd)
        terminal_settings[3] &= ~termios.ECHO
        termios.tcsetattr(slave_fd, termios.TCSANOW, terminal_settings)
        self.master_fd = master_fd
        self.transcript = bytearray()
        self.prompt_seen = threading.Event()
        self.password_sent = threading.Event()
        self._send_lock = threading.Lock()
        def child_terminal_setup() -> None:
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self.process = subprocess.Popen(
            command,
            stdin=slave_fd,
            stdout=subprocess.PIPE,
            stderr=slave_fd,
            close_fds=True,
            pass_fds=pass_fds,
            preexec_fn=child_terminal_setup,
        )
        os.close(slave_fd)
        self._password = password
        self.thread = threading.Thread(target=self._drive_prompt, daemon=True)
        self.thread.start()

    def send_password(self) -> None:
        with self._send_lock:
            if self.password_sent.is_set():
                return
            os.write(self.master_fd, self._password + b"\n")
            self.password_sent.set()
            self._password = b""

    def _drive_prompt(self) -> None:
        rolling = bytearray()
        started = time.monotonic()
        try:
            while True:
                ready, _, _ = select.select([self.master_fd], [], [], 0.25)
                if not ready:
                    if self.process.poll() is not None:
                        break
                    # Some 7-Zip builds suppress the password prompt under
                    # ``-so``.  Wait until the child has entered its password
                    # read, then deliver once through the isolated no-echo PTY.
                    if time.monotonic() - started >= 1.0 and not self.password_sent.is_set():
                        self.send_password()
                    continue
                try:
                    chunk = os.read(self.master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                if len(self.transcript) < 65536:
                    self.transcript.extend(chunk[: 65536 - len(self.transcript)])
                rolling.extend(chunk)
                if len(rolling) > 8192:
                    del rolling[:-8192]
                lower = bytes(rolling).lower()
                if b"password" in lower and not self.password_sent.is_set():
                    self.prompt_seen.set()
                    self.send_password()
        finally:
            self._password = b""

    def finish(self) -> tuple[int, bool, str]:
        try:
            code = self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            if self.process.stdout is not None:
                self.process.stdout.close()
            self.process.terminate()
            try:
                code = self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                code = self.process.wait(timeout=5)
        self.thread.join(timeout=10)
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        clean = bytes(self.transcript).decode("utf-8", errors="replace")
        return code, self.prompt_seen.is_set(), clean


class SevenZipPayload(io.RawIOBase):
    """Strip a 7-Zip password prompt from stdout, then expose only payload."""

    PROMPT = re.compile(rb"(?i)(?:enter\s+)?password[^:\r\n]{0,120}:")

    def __init__(
        self,
        child: SevenZipPTY,
        magic: bytes | None = None,
        headerless_csv: bool = False,
        headerless_min_fields: int = 100,
    ) -> None:
        super().__init__()
        if child.process.stdout is None:
            raise RuntimeError("7-Zip stdout pipe unavailable")
        self.child = child
        self.raw = child.process.stdout
        self.prefix = bytearray()
        self.magic = magic
        self.headerless_csv = headerless_csv
        self.headerless_min_fields = headerless_min_fields
        self.bytes_delivered = 0
        self._prepare()

    def _prepare(self) -> None:
        observed = bytearray()
        prompt_end = None
        while len(observed) < 65536:
            chunk = os.read(self.raw.fileno(), 512)
            if not chunk:
                break
            observed.extend(chunk)
            match = self.PROMPT.search(bytes(observed))
            if match:
                prompt_end = match.end()
                self.child.prompt_seen.set()
                self.child.send_password()
                break
            if self.magic and bytes(observed).startswith(self.magic):
                self.prefix.extend(observed)
                return
            if self.magic is None and b"\n" in observed:
                first_line = bytes(observed).splitlines()[0]
                try:
                    fields = [item.strip().upper() for item in next(csv.reader([first_line.decode("utf-8-sig")]))]
                except (UnicodeDecodeError, csv.Error):
                    fields = []
                if "DX1" in fields and any(re.fullmatch(r"PR\d+", item) for item in fields):
                    self.prefix.extend(observed)
                    return
                if self.headerless_csv and len(fields) >= self.headerless_min_fields:
                    self.prefix.extend(observed)
                    return
        if prompt_end is None:
            child_status = self.child.process.poll()
            child_diagnostic = bytes(self.child.transcript[-2000:]).decode(
                "utf-8", errors="replace"
            )
            raise RuntimeError(
                "7-Zip password prompt was not isolated from stdout; "
                f"prefix={bytes(observed[:200])!r}; child_status={child_status}; "
                f"diagnostic={child_diagnostic!r}"
            )
        payload = bytearray(observed[prompt_end:])
        if self.magic:
            while True:
                position = payload.find(self.magic)
                if position >= 0:
                    self.prefix.extend(payload[position:])
                    return
                if len(payload) > 65536:
                    raise RuntimeError("7-Zip stdout did not transition to the expected binary payload")
                chunk = os.read(self.raw.fileno(), 512)
                if not chunk:
                    raise RuntimeError("7-Zip ended before emitting the expected payload")
                payload.extend(chunk)
        else:
            while not payload:
                chunk = os.read(self.raw.fileno(), 512)
                if not chunk:
                    raise RuntimeError("7-Zip ended before emitting the CSV payload")
                payload.extend(chunk)
            while payload[:1] in (b"\r", b"\n"):
                del payload[:1]
            self.prefix.extend(payload)

    def readable(self) -> bool:
        return True

    def readinto(self, target) -> int:
        view = memoryview(target).cast("B")
        written = 0
        if self.prefix:
            take = min(len(view), len(self.prefix))
            view[:take] = self.prefix[:take]
            del self.prefix[:take]
            written += take
        if written < len(view):
            chunk = os.read(self.raw.fileno(), len(view) - written)
            if chunk:
                view[written : written + len(chunk)] = chunk
                written += len(chunk)
        self.bytes_delivered += written
        return written


def seven_zip_binary() -> str:
    for name in ("7z", "7zz", "7za"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("7-Zip executable not found")


def extraction_command(archive: str, member: str) -> list[str]:
    return [seven_zip_binary(), "x", "-y", "-bd", "-bb0", "-so", archive, member]


def create_anonymous_memfd() -> int:
    if hasattr(os, "memfd_create"):
        return os.memfd_create("nrd_inner_zip", flags=getattr(os, "MFD_CLOEXEC", 1))
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "memfd_create"):
        raise RuntimeError("memfd_create is unavailable; refusing disk persistence fallback")
    libc.memfd_create.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    libc.memfd_create.restype = ctypes.c_int
    descriptor = libc.memfd_create(b"nrd_inner_zip", 1)
    if descriptor < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return descriptor


def extract_outer_to_memfd(archive: Path, member: str, password: bytes) -> tuple[int, str, int]:
    memfd = create_anonymous_memfd()
    child = SevenZipPTY(extraction_command(str(archive), member), password)
    digest = hashlib.sha256()
    size = 0
    try:
        payload = SevenZipPayload(child, magic=b"PK\x03\x04")
        while True:
            chunk = payload.read(8 * 1024 * 1024)
            if not chunk:
                break
            os.write(memfd, chunk)
            digest.update(chunk)
            size += len(chunk)
        code, prompt_seen, transcript = child.finish()
        if code != 0 or not child.password_sent.is_set():
            raise RuntimeError(
                f"outer 7-Zip extraction failed: exit={code}, prompt_seen={prompt_seen}, "
                f"diagnostic={transcript[-1000:]!r}"
            )
        if size < 4 or os.pread(memfd, 4, 0)[:2] != b"PK":
            raise RuntimeError("outer extraction did not produce a ZIP payload")
        os.lseek(memfd, 0, os.SEEK_SET)
        return memfd, digest.hexdigest(), size
    except BaseException:
        try:
            child.process.terminate()
        except Exception:
            pass
        try:
            child.finish()
        except Exception:
            pass
        os.close(memfd)
        raise


def list_archive_entries_memfd(memfd: int) -> list[dict[str, str]]:
    archive_ref = f"/proc/self/fd/{memfd}"
    completed = subprocess.run(
        [seven_zip_binary(), "l", "-ba", "-slt", archive_ref],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(memfd,),
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"inner archive listing failed: exit={completed.returncode}, "
            f"diagnostic={completed.stderr.decode('utf-8', errors='replace')[-1000:]!r}"
        )
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            if current.get("Path") and current["Path"] != archive_ref:
                entries.append(current)
            current = {}
            continue
        if " = " in line:
            key, value = line.split(" = ", 1)
            current[key.strip()] = value.strip()
    if current.get("Path") and current["Path"] != archive_ref:
        entries.append(current)
    return entries


def list_archive_members_memfd(memfd: int) -> list[str]:
    return [entry["Path"] for entry in list_archive_entries_memfd(memfd)]


def start_inner_stream(memfd: int, member: str, password: bytes) -> SevenZipPTY:
    archive_ref = f"/proc/self/fd/{memfd}"
    return SevenZipPTY(extraction_command(archive_ref, member), password, pass_fds=(memfd,))


def run(args: argparse.Namespace) -> int:
    archive = Path(args.archive).resolve(strict=True)
    ontology = Path(args.ontology).resolve(strict=True)
    sas_layout = Path(args.sas_load_program).resolve(strict=True)
    header = parse_sas_input_layout(sas_layout)
    sas_layout_sha256 = hashlib.sha256(sas_layout.read_bytes()).hexdigest()
    output = Path(args.output_dir)
    if output.exists():
        raise RuntimeError(f"refusing to reuse output directory: {output}")
    set_runtime_limits(args.memory_gib, args.threads)
    password_text = getpass.getpass(f"NRD {args.year} archive password: ")
    if not password_text:
        raise RuntimeError("empty password refused")
    password = password_text.encode("utf-8")
    del password_text
    started_at = utc_now()
    outer_member = args.outer_member
    if not outer_member:
        raise RuntimeError("--outer-member is required for fail-closed selection")
    memfd, inner_hash, inner_size = extract_outer_to_memfd(archive, outer_member, password)
    try:
        entries = list_archive_entries_memfd(memfd)
        members = [entry["Path"] for entry in entries]
        inner_member = choose_member(members, args.inner_member, ".csv")
        selected_entry = next(entry for entry in entries if entry["Path"] == inner_member)
        expected_csv_bytes = int(selected_entry["Size"])
        child = start_inner_stream(memfd, inner_member, password)
        try:
            payload = SevenZipPayload(child, headerless_csv=True)
            aggregator = aggregate_stream(
                io.BufferedReader(payload, 8 * 1024 * 1024),
                args.year,
                ontology,
                args.threads,
                header,
            )
            print(
                f"STREAM_RETURN year={args.year} rows={aggregator.rows} "
                f"bytes_delivered={payload.bytes_delivered} expected_bytes={expected_csv_bytes} "
                f"child_poll={child.process.poll()}",
                flush=True,
            )
            if payload.bytes_delivered != expected_csv_bytes:
                raise RuntimeError(
                    f"CORE stream byte-count mismatch: delivered={payload.bytes_delivered}, "
                    f"expected={expected_csv_bytes}"
                )
            code, prompt_seen, transcript = child.finish()
            if code != 0 or not child.password_sent.is_set():
                raise RuntimeError(
                    f"inner 7-Zip stream failed: exit={code}, prompt_seen={prompt_seen}, "
                    f"diagnostic={transcript[-1000:]!r}"
                )
        except BaseException:
            try:
                child.process.terminate()
            except Exception:
                pass
            try:
                child.finish()
            except Exception:
                pass
            raise
    finally:
        os.close(memfd)
        password = b""
    write_outputs(
        output,
        args.year,
        aggregator,
        archive,
        outer_member,
        inner_member,
        inner_hash,
        inner_size,
        expected_csv_bytes,
        sas_layout,
        sas_layout_sha256,
        len(header),
        started_at,
        args.memory_gib,
        args.threads,
    )
    print(f"PASS year={args.year} rows={aggregator.rows} output={output}", flush=True)
    return 0


def build_encrypted_zip(payload_name: str, payload: bytes, password: bytes) -> bytes:
    buffer = io.BytesIO()
    with pyzipper.AESZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as archive:
        archive.setpassword(password)
        archive.writestr(payload_name, payload)
    return buffer.getvalue()


def self_test(_: argparse.Namespace) -> int:
    from stream_nrd_frequency import fixture_test

    fixture_test(argparse.Namespace())
    correct = b"synthetic-password"
    inner_csv = b"DX1,PR1,PRDAY1\nK83.01,0FC98ZZ,1\n"
    inner_zip = build_encrypted_zip("NRD_2019_Core.CSV", inner_csv, correct)
    outer_zip = build_encrypted_zip("NRD_2019_CORE.zip", inner_zip, correct)
    with tempfile.TemporaryDirectory(prefix="nrd_double_aes_fixture_") as temporary:
        pyzipper_outer_path = Path(temporary) / "pyzipper_outer.zip"
        pyzipper_outer_path.write_bytes(outer_zip)
        with pyzipper.AESZipFile(pyzipper_outer_path, "r") as outer:
            outer.setpassword(correct)
            assert outer.read("NRD_2019_CORE.zip") == inner_zip
        failed_closed = False
        try:
            with pyzipper.AESZipFile(pyzipper_outer_path, "r") as outer:
                outer.setpassword(b"wrong-password")
                outer.read("NRD_2019_CORE.zip")
        except (RuntimeError, ValueError, zipfile.BadZipFile):
            failed_closed = True
        assert failed_closed, "wrong password unexpectedly succeeded"

        plain_csv = Path(temporary) / "NRD_2019_Core.CSV"
        plain_csv.write_bytes(inner_csv)
        inner_path = Path(temporary) / "NRD_2019_CORE.zip"
        outer_path = Path(temporary) / "NRD_2019.zip"
        synthetic_arg = "-psynthetic-password"
        create_inner = subprocess.run(
            [seven_zip_binary(), "a", "-tzip", "-mem=AES256", synthetic_arg, str(inner_path), str(plain_csv)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if create_inner.returncode:
            raise RuntimeError(create_inner.stderr.decode("utf-8", errors="replace"))
        create_outer = subprocess.run(
            [seven_zip_binary(), "a", "-tzip", "-mem=AES256", synthetic_arg, str(outer_path), str(inner_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if create_outer.returncode:
            raise RuntimeError(create_outer.stderr.decode("utf-8", errors="replace"))
        plain_csv.unlink()
        inner_path.unlink()
        memfd, digest, size = extract_outer_to_memfd(outer_path, "NRD_2019_CORE.zip", correct)
        try:
            members = list_archive_members_memfd(memfd)
            member = choose_member(members, "NRD_2019_Core.CSV", ".csv")
            child = start_inner_stream(memfd, member, correct)
            payload = SevenZipPayload(child)
            recovered = payload.read()
            code, prompt_seen, _ = child.finish()
            assert code == 0 and child.password_sent.is_set() and recovered == inner_csv
            assert len(digest) == 64 and size > len(inner_csv)
        finally:
            os.close(memfd)
        seven_zip_failed_closed = False
        try:
            bad_fd, _, _ = extract_outer_to_memfd(outer_path, "NRD_2019_CORE.zip", b"wrong-password")
            os.close(bad_fd)
        except RuntimeError:
            seven_zip_failed_closed = True
        assert seven_zip_failed_closed, "7-Zip wrong password unexpectedly succeeded"
        remaining = {path.name for path in Path(temporary).iterdir()}
        assert remaining == {"pyzipper_outer.zip", "NRD_2019.zip"}
    print("PASS double-AES fixtures, PTY separation, and wrong-password fail-closed tests", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("self-test")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--year", type=int, required=True)
    run_parser.add_argument("--archive", required=True)
    run_parser.add_argument("--outer-member")
    run_parser.add_argument("--inner-member")
    run_parser.add_argument("--ontology", required=True)
    run_parser.add_argument("--sas-load-program", required=True)
    run_parser.add_argument("--output-dir", required=True)
    run_parser.add_argument("--threads", type=int, default=8)
    run_parser.add_argument("--memory-gib", type=int, default=20)
    args = parser.parse_args()
    if args.command == "self-test":
        return self_test(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
