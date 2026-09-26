"""Regression checks for public, outcome-blind v4.7 invariants."""
from __future__ import annotations

import frozen_v4_7 as frozen


def test_safe_authorization_identifier() -> None:
    assert frozen._safe_id("example-run-001", "run_id") == "example-run-001"
    for value in ("", "..", "bad/id", "bad id"):
        try:
            frozen._safe_id(value, "run_id")
        except RuntimeError:
            continue
        raise AssertionError(value)


def test_absolute_path_guard_rejects_traversal() -> None:
    try:
        frozen._canonical_absolute("/tmp/../unsafe", "test")
    except RuntimeError:
        return
    raise AssertionError("path traversal was not rejected")


def test_base_covariate_contract_is_stable() -> None:
    assert "AGE" in frozen.BASE
    assert "YEAR" in frozen.BASE
    assert "HOSP_UR_TEACH" in frozen.BASE
