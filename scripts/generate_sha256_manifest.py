#!/usr/bin/env python3
"""Generate a relative-path SHA256 manifest for a public release."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


# These directories are reproducible local caches and are already excluded by
# the repository .gitignore.  They must not enter the public release manifest,
# otherwise a local test run would make the manifest differ from the files
# that GitHub will actually track.
EXCLUDED_DIRS = {".git", ".pytest_cache", "__pycache__"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("SHA256_MANIFEST.json"))
    args = parser.parse_args()
    root = args.repo.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.resolve() == output.resolve():
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        files.append({"file": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)})
    payload = {"status": "PASS", "self_excluded": True, "files": files}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"manifest_entries={len(files)} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
