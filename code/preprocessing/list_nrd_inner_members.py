#!/usr/bin/env python3
"""List encrypted NRD inner-archive metadata without persisting its payload."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

from nested_zip_frequency_v7 import extract_outer_to_memfd, list_archive_entries_memfd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--outer-member", required=True)
    args = parser.parse_args()
    password_text = getpass.getpass("NRD archive password: ")
    if not password_text:
        raise RuntimeError("empty password refused")
    password = password_text.encode("utf-8")
    del password_text
    descriptor = None
    try:
        descriptor, digest, size = extract_outer_to_memfd(
            Path(args.archive), args.outer_member, password
        )
        entries = list_archive_entries_memfd(descriptor)
        print(
            json.dumps(
                {
                    "outer_member": args.outer_member,
                    "inner_zip_sha256": digest,
                    "inner_zip_bytes": size,
                    "entries": [
                        {"path": entry.get("Path"), "size": entry.get("Size")}
                        for entry in entries
                    ],
                },
                sort_keys=True,
            )
        )
    finally:
        password = b""
        if descriptor is not None:
            os.close(descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
