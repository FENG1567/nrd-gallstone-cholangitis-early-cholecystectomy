"""Authorization/allowlist shape checks for the public generator."""
from __future__ import annotations

import re
from pathlib import Path


def test_generator_is_explicitly_candidate_only_by_default() -> None:
    text = Path(__file__).parents[2].joinpath("scripts", "generate_v4_7_allowlist.py").read_text(encoding="utf-8")
    assert "CANDIDATE_NOT_APPROVED" in text
    assert "--approve" in text


def test_no_obvious_password_literal_in_public_code() -> None:
    root = Path(__file__).parents[2]
    for path in root.rglob("*.py"):
        assert re.search(r"(?i)master\\s*2333", path.read_text(encoding="utf-8", errors="ignore")) is None
