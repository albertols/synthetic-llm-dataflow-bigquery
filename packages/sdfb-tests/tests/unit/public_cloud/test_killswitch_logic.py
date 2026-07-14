"""Unit tests for the billing killswitch decision logic (pure python)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
LOGIC = REPO_ROOT / "public_cloud" / "deploy" / "gcp" / "killswitch" / "logic.py"

spec = importlib.util.spec_from_file_location("killswitch_logic", LOGIC)
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)


def test_kills_at_or_above_threshold():
    assert logic.should_kill({"costAmount": 22.5, "budgetAmount": 25.0}, 0.9) is True
    assert logic.should_kill({"costAmount": 25.0, "budgetAmount": 25.0}, 0.9) is True


def test_does_not_kill_below_threshold():
    assert logic.should_kill({"costAmount": 12.0, "budgetAmount": 25.0}, 0.9) is False


def test_defensive_on_malformed_payload():
    assert logic.should_kill({}, 0.9) is False
    assert logic.should_kill({"budgetAmount": 0}, 0.9) is False       # div-by-zero guard
    assert logic.should_kill({"costAmount": None, "budgetAmount": 25.0}, 0.9) is False
