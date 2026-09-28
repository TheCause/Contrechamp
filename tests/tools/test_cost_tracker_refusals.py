"""A refused reservation must say WHY, and must not stay open in the log.

Measured on a real run (lot 2, 28 Sept 2026): with mode=cap and a $0 ceiling,
the first paid call was refused with "requires approval" — an invitation to
approve something that the ceiling would refuse anyway — and every refusal left
an entry at status "estimated" that nothing ever closed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib.config_model import BudgetMode
from tools.cost_tracker import ApprovalRequiredError, BudgetExceededError, CostTracker


def _tracker(tmp_path: Path, *, total: float, mode: BudgetMode = BudgetMode.CAP) -> CostTracker:
    return CostTracker(
        budget_total_usd=total,
        reserve_pct=0.0,
        single_action_approval_usd=0.5,
        require_approval_for_new_paid_tool=True,
        mode=mode,
        cost_log_path=tmp_path / "cost_log.json",
    )


def test_cap_refusal_names_the_ceiling_not_an_approval(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path, total=0.0)
    entry_id = tracker.estimate("image_gen", "generate", 0.05)
    with pytest.raises(BudgetExceededError, match="exceeds usable budget"):
        tracker.reserve(entry_id)


def test_cap_with_room_still_asks_for_first_use_approval(tmp_path: Path) -> None:
    # Healthy case: the ceiling allows the spend, so the approval gate speaks.
    tracker = _tracker(tmp_path, total=10.0)
    entry_id = tracker.estimate("image_gen", "generate", 0.05)
    with pytest.raises(ApprovalRequiredError, match="requires approval"):
        tracker.reserve(entry_id)


@pytest.mark.parametrize("total", [0.0, 10.0])
def test_refused_reservation_is_closed_in_the_log(tmp_path: Path, total: float) -> None:
    tracker = _tracker(tmp_path, total=total)
    entry_id = tracker.estimate("image_gen", "generate", 0.05)
    with pytest.raises((BudgetExceededError, ApprovalRequiredError)):
        tracker.reserve(entry_id)
    persisted = json.loads((tmp_path / "cost_log.json").read_text())
    entry = persisted["entries"][0]
    assert entry["status"] == "refused"
    assert entry["refusal"]
    assert entry.get("reserved_usd", 0.0) == 0.0


def test_accepted_reservation_is_not_marked_refused(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path, total=10.0)
    tracker.approve_tool_for_session("image_gen")
    entry_id = tracker.estimate("image_gen", "generate", 0.05)
    tracker.reserve(entry_id)
    assert tracker.entries[0]["status"] == "reserved"
    assert "refusal" not in tracker.entries[0]
