#!/usr/bin/env python3
"""Run the public-package safety and manifest checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


# Detect private workstation/server locations by form rather than embedding any
# real username, host name, or project credential in this public validator.
PRIVATE_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+", re.IGNORECASE),
    re.compile("/" + "home" + r"/(?:[^/\s]+/){1,2}"),
)
PRIVATE_CONNECTION_PATTERNS = (
    re.compile(r"\b(?:ssh|scp)://[^\s]+", re.IGNORECASE),
    re.compile(r"\b[^\s/@]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b"),
)
FORBIDDEN_SUFFIXES = {".zip", ".parquet", ".sav", ".dta", ".rds", ".pkl", ".key", ".pem"}
EXCLUDED_DIRS = {".git", ".pytest_cache", "__pycache__"}


def public_files(root: Path) -> list[Path]:
    """Return files that would be eligible for the public GitHub package."""
    return [
        p for p in root.rglob("*")
        if p.is_file() and not any(part in EXCLUDED_DIRS for part in p.relative_to(root).parts)
    ]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    root = args.repo.resolve()
    violations: list[str] = []
    files = public_files(root)
    for path in files:
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            violations.append(f"restricted extension: {path.relative_to(root)}")
        if path.stat().st_size > 100 * 1024 * 1024:
            violations.append(f"file exceeds 100 MB: {path.relative_to(root)}")
        if path.suffix.lower() in {".py", ".r", ".md", ".json", ".yaml", ".yml", ".sh", ".cff", ".txt", ".csv", ".gitignore", ".gitattributes"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in PRIVATE_PATH_PATTERNS + PRIVATE_CONNECTION_PATTERNS:
                if pattern.search(text):
                    violations.append(
                        f"private path/connection pattern {pattern.pattern}: {path.relative_to(root)}"
                    )

    manifest_path = root / "SHA256_MANIFEST.json"
    if not manifest_path.is_file():
        violations.append("missing SHA256_MANIFEST.json")
    else:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("self_excluded") is not True:
            violations.append("root manifest must exclude itself")
        expected = {row["file"]: row for row in payload.get("files", [])}
        for relative, row in expected.items():
            path = root / relative
            if not path.is_file() or path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]:
                violations.append(f"manifest mismatch: {relative}")
        actual = {p.relative_to(root).as_posix() for p in files if p != manifest_path}
        if actual != set(expected):
            violations.append("manifest file set does not match repository")

    if violations:
        print(json.dumps({"status": "FAIL", "violations": violations}, indent=2, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "PASS", "files": len(files)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
