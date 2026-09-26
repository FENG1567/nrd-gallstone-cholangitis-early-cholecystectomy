"""Static execution-barrier checks; no licensed inputs are opened."""
from __future__ import annotations

import re
from pathlib import Path


def test_runner_requires_explicit_data_and_hash_arguments() -> None:
    text = Path(__file__).with_name("run_formal500_v4_7.py").read_text(encoding="utf-8")
    for token in ("--private-root", "--hash-allowlist", "--sap", "--mode"):
        assert token in text


def test_frozen_runner_has_no_embedded_server_path() -> None:
    text = Path(__file__).with_name("frozen_v4_7.py").read_text(encoding="utf-8")
    private_path_patterns = (
        re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+", re.IGNORECASE),
        re.compile("/" + "home" + r"/(?:[^/\s]+/){1,2}"),
    )
    assert not any(pattern.search(text) for pattern in private_path_patterns)
