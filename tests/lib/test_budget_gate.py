"""The budget gate must actually refuse to spend.

Regression: `CostTracker` had no caller anywhere outside its own tests. The
`cap` mode documented in ARCHITECTURE.md as rejecting operations that exceed
the remaining budget could not fire, because nothing called `reserve()`.

These tests drive a fake paid tool through the real `BaseTool` wrapper, so
they exercise the same path a Veo or Kling call takes.
"""

from __future__ import annotations

import json

import pytest

from lib import budget, env_names
from lib.checkpoint import init_project, write_checkpoint
from tools.base_tool import (
    BaseTool,
    ToolResult,
    ToolRuntime,
    ToolTier,
)
from tools.cost_tracker import ApprovalRequiredError, BudgetExceededError


class FakePaidTool(BaseTool):
    """Stands in for any ToolRuntime.API provider."""

    name = "fake_paid_tool"
    tier = ToolTier.GENERATE
    runtime = ToolRuntime.API
    unit_cost = 0.25

    def estimate_cost(self, inputs: dict) -> float:
        return float(inputs.get("cost", self.unit_cost))

    def execute(self, inputs: dict) -> ToolResult:
        return ToolResult(success=True, cost_usd=self.estimate_cost(inputs))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Re-root the canonical projects/ dir into tmp_path.

    A tool call is only attributable to a project when its path resolves under
    the canonical projects root (lib/events.infer_project_dir) — a deliberate
    rule so an arbitrary output_path cannot spawn a ghost project. The budget
    gate inherits that same boundary, so these tests must re-root it rather
    than point at an unrelated tmp dir.
    """
    import lib.events

    root = tmp_path / "projects"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(lib.events, "PROJECTS_DIR", root)
    budget.reset_trackers()
    for name in ("BUDGET_DISABLED", "APPROVE_TOOLS", "BUDGET_MODE", "BUDGET_TOTAL_USD", "SINGLE_ACTION_USD"):
        for var in env_names.names(name):
            monkeypatch.delenv(var, raising=False)
    yield
    budget.reset_trackers()


def _project(tmp_path):
    """A project dir the wrapper will attribute the call to."""
    return init_project("proj", title="Budget", pipeline_type="animated-explainer",
                        pipeline_dir=tmp_path / "projects",
                        style_playbook="clean-professional")


def test_cap_refuses_paid_call_outside_a_project(tmp_path, monkeypatch):
    """No project means no cost_log.json to account against. In cap mode the
    gate cannot prove the call fits the ceiling, so it must refuse rather than
    wave it through (an agent writing to /tmp used to spend without limit)."""
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "cap")
    monkeypatch.setenv("CONTRECHAMP_BUDGET_TOTAL_USD", "0.0")
    with pytest.raises(BudgetExceededError):
        FakePaidTool().execute({"cost": 5.0})
    with pytest.raises(BudgetExceededError):
        FakePaidTool().execute({"cost": 5.0, "output_path": str(tmp_path / "x.png")})


def test_outside_a_project_free_or_non_cap_calls_pass(tmp_path, monkeypatch):
    """Silent side: a free call in cap mode, and any call in warn mode, still
    run outside a project."""
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "cap")
    monkeypatch.setenv("CONTRECHAMP_BUDGET_TOTAL_USD", "0.0")
    assert FakePaidTool().execute({"cost": 0.0}).success
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "warn")
    assert FakePaidTool().execute({"cost": 5.0}).success


def test_cap_mode_refuses_to_exceed_budget(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "cap")
    monkeypatch.setenv("CONTRECHAMP_BUDGET_TOTAL_USD", "1.00")
    monkeypatch.setenv("CONTRECHAMP_APPROVE_TOOLS", "*")
    # Isolate the budget ceiling from the single-action approval threshold,
    # which would otherwise fire first on the large call.
    monkeypatch.setenv("CONTRECHAMP_SINGLE_ACTION_USD", "999")

    tool = FakePaidTool()
    # Well inside the budget: allowed, and recorded.
    assert tool.execute({"project_dir": str(project), "cost": 0.20}).success

    # Over the remaining usable budget (1.00 total, 10% holdback): refused.
    with pytest.raises(BudgetExceededError):
        tool.execute({"project_dir": str(project), "cost": 5.00})


def test_first_paid_use_requires_approval(tmp_path, monkeypatch):
    """AGENT_GUIDE.md asks the agent to announce cost before every first paid
    call. Nothing enforced it; now the gate does."""
    project = _project(tmp_path)
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "warn")
    monkeypatch.setenv("CONTRECHAMP_BUDGET_TOTAL_USD", "100.0")

    with pytest.raises(ApprovalRequiredError):
        FakePaidTool().execute({"project_dir": str(project), "cost": 0.20})


def test_spend_is_recorded_and_reconciled(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "observe")
    monkeypatch.setenv("CONTRECHAMP_BUDGET_TOTAL_USD", "10.0")
    monkeypatch.setenv("CONTRECHAMP_APPROVE_TOOLS", "*")

    tool = FakePaidTool()
    tool.execute({"project_dir": str(project), "cost": 0.30})
    tool.execute({"project_dir": str(project), "cost": 0.15})

    log = project / "cost_log.json"
    assert log.is_file(), "cost_log.json was never written"
    entries = json.loads(log.read_text())
    entries = entries["entries"] if isinstance(entries, dict) else entries
    completed = [e for e in entries if e["status"] == "completed"]
    assert len(completed) == 2
    assert round(sum(e["actual_usd"] for e in completed), 2) == 0.45


def test_failed_call_is_refunded_not_charged(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "observe")

    class Exploding(FakePaidTool):
        name = "exploding_paid_tool"

        def execute(self, inputs: dict) -> ToolResult:
            raise RuntimeError("provider 500")

    with pytest.raises(RuntimeError):
        Exploding().execute({"project_dir": str(project), "cost": 2.0})

    tracker = budget.tracker_for(project)
    assert tracker is not None
    assert tracker.budget_spent_usd == 0.0
    assert tracker.budget_reserved_usd == 0.0


def test_checkpoint_backfills_cost_snapshot(tmp_path, monkeypatch):
    """Backlot's cost meter reads checkpoint['cost_snapshot']; nothing wrote it."""
    pipeline_dir = tmp_path / "projects"
    project = _project(tmp_path)
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "observe")

    FakePaidTool().execute({"project_dir": str(project), "cost": 0.40})

    path = write_checkpoint(
        pipeline_dir, "proj", "research", "in_progress", artifacts={},
        pipeline_type="animated-explainer", style_playbook="clean-professional",
    )
    checkpoint = json.loads(path.read_text())
    snapshot = checkpoint.get("cost_snapshot")
    assert snapshot is not None, "cost_snapshot was not backfilled"
    assert snapshot["total_spent_usd"] == pytest.approx(0.40)


def test_kill_switch_disables_the_gate(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setenv("CONTRECHAMP_BUDGET_DISABLED", "1")
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "cap")
    monkeypatch.setenv("CONTRECHAMP_BUDGET_TOTAL_USD", "0.0")
    assert FakePaidTool().execute({"project_dir": str(project), "cost": 9.0}).success


# ---------------------------------------------------------------------------
# Repairs after an adversarial audit of the #601 cherry-pick. Each defect has
# a test that fails on it and a test that stays silent on the healthy case.
# ---------------------------------------------------------------------------

def _strict(monkeypatch, total="0.0"):
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "cap")
    monkeypatch.setenv("CONTRECHAMP_BUDGET_TOTAL_USD", total)
    monkeypatch.setenv("CONTRECHAMP_APPROVE_TOOLS", "*")
    monkeypatch.setenv("CONTRECHAMP_SINGLE_ACTION_USD", "999")


def test_current_ceiling_wins_over_ceiling_stored_in_log(tmp_path, monkeypatch):
    """Lowering the ceiling on a project that already has a cost_log.json must
    bite. The log used to overwrite the configured ceiling on load."""
    project = _project(tmp_path)
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "observe")
    monkeypatch.setenv("CONTRECHAMP_BUDGET_TOTAL_USD", "10.0")
    monkeypatch.setenv("CONTRECHAMP_APPROVE_TOOLS", "*")
    FakePaidTool().execute({"project_dir": str(project), "cost": 0.10})
    assert json.loads((project / "cost_log.json").read_text())["budget_total_usd"] == 10.0

    budget.reset_trackers()  # a new process
    _strict(monkeypatch, total="0.0")
    with pytest.raises(BudgetExceededError):
        FakePaidTool().execute({"project_dir": str(project), "cost": 0.10})


def test_existing_log_within_current_ceiling_still_passes(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _strict(monkeypatch, total="10.0")
    FakePaidTool().execute({"project_dir": str(project), "cost": 0.10})
    budget.reset_trackers()
    assert FakePaidTool().execute({"project_dir": str(project), "cost": 0.10}).success


@pytest.mark.parametrize("damage", ["corrupt", "directory"])
def test_unreadable_cost_log_refuses_paid_call(tmp_path, monkeypatch, damage):
    """An unreadable log used to switch the gate off in silence."""
    project = _project(tmp_path)
    log = project / "cost_log.json"
    if damage == "corrupt":
        log.write_text("{not json")
    else:
        log.mkdir()
    _strict(monkeypatch, total="100.0")
    with pytest.raises(ApprovalRequiredError, match="cost_log.json"):
        FakePaidTool().execute({"project_dir": str(project), "cost": 5.0})


def test_unreadable_cost_log_does_not_block_free_calls(tmp_path, monkeypatch):
    project = _project(tmp_path)
    (project / "cost_log.json").write_text("{not json")
    _strict(monkeypatch, total="100.0")
    assert FakePaidTool().execute({"project_dir": str(project), "cost": 0.0}).success


def test_unwritable_cost_log_is_a_governance_refusal(tmp_path, monkeypatch):
    """Same policy for an unwritable log: a refusal that says why, not a raw
    PermissionError from deep inside the tracker."""
    import os

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    project = _project(tmp_path)
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "observe")
    project.chmod(0o500)
    try:
        with pytest.raises(ApprovalRequiredError, match="cost_log.json"):
            FakePaidTool().execute({"project_dir": str(project), "cost": 1.0})
    finally:
        project.chmod(0o755)


@pytest.mark.parametrize("runtime", [ToolRuntime.HYBRID, ToolRuntime.LOCAL_GPU,
                                     ToolRuntime.LOCAL])
def test_paid_estimate_is_governed_whatever_the_runtime(tmp_path, monkeypatch, runtime):
    """image_gen (HYBRID) calls OpenAI/FAL itself; comfyui_video (LOCAL_GPU)
    drives paid partner nodes. Only API tools used to be governed."""
    project = _project(tmp_path)
    _strict(monkeypatch)

    class Paid(FakePaidTool):
        name = f"fake_paid_{runtime.value}"

    Paid.runtime = runtime
    with pytest.raises(BudgetExceededError):
        Paid().execute({"project_dir": str(project), "cost": 0.05})


def test_real_image_gen_openai_is_governed(tmp_path, monkeypatch):
    from tools.graphics.image_gen import ImageGen

    project = _project(tmp_path)
    _strict(monkeypatch)
    reached = []
    monkeypatch.setattr(ImageGen, "_generate_openai",
                        lambda self, inputs: reached.append(1) or ToolResult(success=True))
    with pytest.raises(BudgetExceededError):
        ImageGen().execute({"project_dir": str(project), "provider": "openai", "prompt": "x"})
    assert reached == [], "the paid provider was reached despite a 0 USD cap"


def test_real_comfyui_partner_node_is_governed(tmp_path, monkeypatch):
    from tools.video.comfyui_video import ComfyUIVideo

    class Offline(ComfyUIVideo):
        """Real runtime and estimate; execute never touches a server."""

        def execute(self, inputs):
            return ToolResult(success=True, cost_usd=self.estimate_cost(inputs))

    project = _project(tmp_path)
    _strict(monkeypatch)
    with pytest.raises(BudgetExceededError):
        Offline().execute({"project_dir": str(project),
                           "model_family": "seedance_2.5", "duration": 10})
    # Silent side: the free local family is not charged.
    assert Offline().execute({"project_dir": str(project), "model_family": "wan2.2"}).success


def test_selector_delegation_is_charged_once(tmp_path, monkeypatch):
    """A selector estimates its provider's cost; charging both would double
    count. Only the provider opens an entry."""
    project = _project(tmp_path)
    _strict(monkeypatch, total="10.0")

    class Selector(FakePaidTool):
        name = "fake_selector"
        runtime = ToolRuntime.HYBRID
        delegates_cost = True

        def execute(self, inputs):
            return FakePaidTool().execute(dict(inputs))

    assert Selector().execute({"project_dir": str(project), "cost": 0.20}).success
    entries = json.loads((project / "cost_log.json").read_text())["entries"]
    assert [e["tool"] for e in entries] == ["fake_paid_tool"]


def test_real_selectors_declare_delegation():
    from tools.audio.tts_selector import TTSSelector
    from tools.graphics.image_selector import ImageSelector
    from tools.video.video_selector import VideoSelector

    for cls in (ImageSelector, TTSSelector, VideoSelector):
        assert getattr(cls, "delegates_cost", False) is True, cls.__name__


def _events(project):
    path = project / "events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.parametrize("var,value", [
    ("CONTRECHAMP_BUDGET_DISABLED", "1"),
    ("CONTRECHAMP_APPROVE_TOOLS", "*"),
    ("CONTRECHAMP_SINGLE_ACTION_USD", "999"),
])
def test_escape_hatch_leaves_a_trace(tmp_path, monkeypatch, var, value):
    """An escape hatch used to be invisible: no log, no event, nothing."""
    project = _project(tmp_path)
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "observe")
    monkeypatch.delenv("CONTRECHAMP_BUDGET_TOTAL_USD", raising=False)
    monkeypatch.delenv("CONTRECHAMP_SINGLE_ACTION_USD", raising=False)
    monkeypatch.setenv(var, value)
    FakePaidTool().execute({"project_dir": str(project), "cost": 0.30})
    overrides = [e for e in _events(project) if e.get("event") == "budget_override"]
    assert overrides, "paid call under an escape hatch left no budget_override event"
    assert var in overrides[0]["overrides"]


def test_no_override_event_without_escape_hatch(tmp_path, monkeypatch):
    project = _project(tmp_path)
    for var in ("CONTRECHAMP_BUDGET_MODE", "CONTRECHAMP_BUDGET_TOTAL_USD",
                "CONTRECHAMP_SINGLE_ACTION_USD", "CONTRECHAMP_APPROVE_TOOLS",
                "CONTRECHAMP_BUDGET_DISABLED"):
        monkeypatch.delenv(var, raising=False)
    tracker = budget.tracker_for(project)
    tracker.approve_tool("fake_paid_tool")  # a real, recorded human approval
    FakePaidTool().execute({"project_dir": str(project), "cost": 0.30})
    assert not [e for e in _events(project) if e.get("event") == "budget_override"]


def test_env_approval_is_not_persisted(tmp_path, monkeypatch):
    """An approval taken from the environment must end with the environment."""
    project = _project(tmp_path)
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "warn")
    monkeypatch.setenv("CONTRECHAMP_BUDGET_TOTAL_USD", "100.0")
    monkeypatch.setenv("CONTRECHAMP_APPROVE_TOOLS", "fake_paid_tool")
    assert FakePaidTool().execute({"project_dir": str(project), "cost": 0.20}).success
    assert "fake_paid_tool" not in json.loads(
        (project / "cost_log.json").read_text())["approved_tools"]

    budget.reset_trackers()
    monkeypatch.delenv("CONTRECHAMP_APPROVE_TOOLS")
    with pytest.raises(ApprovalRequiredError):
        FakePaidTool().execute({"project_dir": str(project), "cost": 0.20})


def _config(tmp_path, monkeypatch, text):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    if text is not None:
        (root / "config.yaml").write_text(text)
    monkeypatch.setattr(budget, "_REPO_ROOT", root)
    monkeypatch.delenv("CONTRECHAMP_BUDGET_MODE", raising=False)
    return budget._budget_config()


@pytest.mark.parametrize("text", [
    "budget:\n  mode: capp\n  total_usd: 0\n",
    "budget: [mode: cap\n  total_usd: 0\n",
])
def test_unreadable_budget_config_falls_back_to_cap(tmp_path, monkeypatch, text):
    """A typo in the owner's budget block used to relax the gate to warn."""
    from lib.config_model import BudgetMode

    assert _config(tmp_path, monkeypatch, text)["mode"] == BudgetMode.CAP


@pytest.mark.parametrize("text,expected", [
    ("budget:\n  mode: warn\n", "warn"),
    ("budget:\n  mode: observe\n", "observe"),
    (None, "warn"),  # no config.yaml at all: upstream default
])
def test_readable_budget_config_is_respected(tmp_path, monkeypatch, text, expected):
    assert _config(tmp_path, monkeypatch, text)["mode"].value == expected
