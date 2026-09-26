"""Public synthetic checks for the frozen v4.7 implementation."""
from __future__ import annotations

import tempfile
from pathlib import Path

import frozen_v4_7 as frozen


def test_mode_contract() -> None:
    frozen.validate_execution_mode("gate-only", 0)
    frozen.validate_execution_mode("dryrun", 1)
    frozen.validate_execution_mode("formal", 500)


def test_mode_contract_rejects_non_frozen_bootstrap() -> None:
    for mode, boot in (("gate-only", 1), ("dryrun", 2), ("formal", 100)):
        try:
            frozen.validate_execution_mode(mode, boot)
        except RuntimeError:
            continue
        raise AssertionError((mode, boot))


def test_public_helpers() -> None:
    assert frozen.sup(10) == "SUPPRESSED_LE_10"
    assert frozen.sup(11) == 11
    assert frozen.bil(" K80.32 ") is True
    assert frozen.bil("K83.01") is False
    assert frozen.BASE_CONFIG.is_file()
