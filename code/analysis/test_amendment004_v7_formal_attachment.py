"""Checks that public results are aggregate-only and disclosure-gated."""
from __future__ import annotations

import json
from pathlib import Path


def test_public_disclosure_gates_pass() -> None:
    root = Path(__file__).parents[2]
    for relative in (
        "data/processed/DISCLOSURE_GATE.json",
        "data/processed/amendment005/DISCLOSURE_GATE.json",
        "data/processed/amendment006/DISCLOSURE_GATE.json",
    ):
        gate = json.loads((root / relative).read_text(encoding="utf-8"))
        assert gate.get("status") == "PASS"


def test_aggregate_outputs_do_not_expose_row_identifiers() -> None:
    root = Path(__file__).parents[2]
    forbidden = ("KEY_NRD", "NRD_VISITLINK", "HOSP_NRD")
    for path in (root / "data/processed").rglob("*.csv"):
        header = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
        assert not any(token in header for token in forbidden), path
