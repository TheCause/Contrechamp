"""Per-project budget governance, wired into every tool call.

Why this module exists
----------------------
`tools/cost_tracker.py` implements estimate -> reserve -> reconcile, and
`docs/ARCHITECTURE.md` says it "enforces spending controls across the
pipeline". It did not. Before this module, `CostTracker` had no caller
anywhere outside its own tests: no skill documented the call protocol, no
pipeline manifest invoked it, and `write_checkpoint(cost_snapshot=...)` was an
optional dict the agent had to assemble by hand. Backlot's board read
`cp["cost_snapshot"]` to draw a cost meter that nothing ever wrote.

So the cap that was meant to stop a runaway spend was unreachable, on a
project whose own README advertises per-video costs to the cent.

How it is wired
---------------
`BaseTool.__init_subclass__` already wraps every concrete `execute()` for
Backlot event emission. That wrapper is the only place in the system that sees
every tool call regardless of what the agent remembers to do, so the gate goes
there rather than into a skill instruction.

Scope:

- **Any tool with a non-zero cost estimate is charged, whatever its runtime.**
  `image_gen` (HYBRID) calls OpenAI/FAL itself and `comfyui_video`
  (LOCAL_GPU) drives paid partner nodes, so gating on `ToolRuntime.API` alone
  let them spend unaccounted. Selectors set `delegates_cost = True`: they
  estimate their provider's price but the provider's own call is the one
  charged, so nothing is counted twice.
- **Calls attributable to a project are accounted in its `cost_log.json`**,
  the same boundary the event layer uses. A paid call that belongs to no
  project cannot be accounted: in `cap` mode it is refused, in `warn` and
  `observe` it runs unaccounted.
- **Free calls never touch the gate.** A zero estimate opens no entry.
- **The ceiling in force is the current config/env one.** A `cost_log.json`
  records spend and persisted approvals; the ceiling written in it is not
  read back, so lowering the ceiling bites on a project already started.

Failure policy
--------------
The gate fails closed. A paid call whose spend cannot be accounted — the log
is unreadable, or cannot be written — is refused with
`GovernanceUnavailableError` (an `ApprovalRequiredError`) that names the log.
A budget config that cannot be read, or names an unknown mode, falls back to
`cap`, the strictest mode, with a warning. Governance decisions
(`BudgetExceededError`, `ApprovalRequiredError`) propagate to the caller,
because refusing to spend is the entire point.

Escape hatches
--------------
- `OPENMONTAGE_BUDGET_MODE=observe|warn|cap` overrides config.yaml.
- `OPENMONTAGE_BUDGET_TOTAL_USD=<float>` overrides the ceiling.
- `OPENMONTAGE_SINGLE_ACTION_USD=<float>` overrides the per-call approval
  threshold.
- `OPENMONTAGE_APPROVE_TOOLS=name,name` (or `*`) pre-approves paid tools for
  unattended runs, standing in for the human who would otherwise be asked.
  These approvals last for the process only; they are not written to
  `cost_log.json`.
- `OPENMONTAGE_BUDGET_DISABLED=1` switches the gate off.

None of these is silent: every paid call made while one is set appends a
`budget_override` event (naming the variables) to the project's events.jsonl
and logs a warning.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from lib.config_model import BudgetMode
from tools.cost_tracker import (
    ApprovalRequiredError,
    BudgetExceededError,
    CostTracker,
)

__all__ = [
    "ApprovalRequiredError",
    "BudgetExceededError",
    "GovernanceUnavailableError",
    "active_overrides",
    "charge",
    "refuse_unattributed",
    "budget_enabled",
    "cost_snapshot",
    "reset_trackers",
    "tracker_for",
]

_LOCK = threading.RLock()
_TRACKERS: dict[str, CostTracker] = {}

_DISABLE_FLAG = "OPENMONTAGE_BUDGET_DISABLED"
_MODE_ENV = "OPENMONTAGE_BUDGET_MODE"
_TOTAL_ENV = "OPENMONTAGE_BUDGET_TOTAL_USD"
_SINGLE_ENV = "OPENMONTAGE_SINGLE_ACTION_USD"
_APPROVE_ENV = "OPENMONTAGE_APPROVE_TOOLS"

_REPO_ROOT = Path(__file__).resolve().parent.parent

_OVERRIDE_VARS = (_DISABLE_FLAG, _MODE_ENV, _TOTAL_ENV, _SINGLE_ENV, _APPROVE_ENV)

logger = logging.getLogger(__name__)


class GovernanceUnavailableError(ApprovalRequiredError):
    """The gate cannot account for a paid call, so it refuses it."""


def active_overrides() -> list[str]:
    """Names of the budget environment overrides currently set."""
    return [var for var in _OVERRIDE_VARS if os.environ.get(var, "").strip()]


def budget_enabled() -> bool:
    """False disables the gate entirely (kill switch for debugging)."""
    return os.environ.get(_DISABLE_FLAG, "").strip().lower() not in {"1", "true", "yes"}


def _budget_config() -> dict[str, Any]:
    """Read the `budget:` block from config.yaml, then apply env overrides."""
    cfg: dict[str, Any] = {}
    config_path = _REPO_ROOT / "config.yaml"
    if config_path.is_file():
        try:
            import yaml

            with open(config_path, encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle) or {}
            block = loaded.get("budget")
            if isinstance(block, dict):
                cfg = dict(block)
        except Exception as exc:
            logger.warning("budget: config.yaml unreadable (%s); falling back to cap mode", exc)
            cfg = {"mode": BudgetMode.CAP.value}

    mode_raw = os.environ.get(_MODE_ENV, "").strip().lower() or cfg.get("mode", "warn")
    try:
        mode = BudgetMode(str(mode_raw).strip().lower())
    except ValueError:
        logger.warning("budget: unknown mode %r; falling back to cap mode", mode_raw)
        mode = BudgetMode.CAP

    total_raw = os.environ.get(_TOTAL_ENV, "").strip() or cfg.get("total_usd", 10.0)
    try:
        total = float(total_raw)
    except (TypeError, ValueError):
        total = 10.0

    single_raw = (
        os.environ.get(_SINGLE_ENV, "").strip()
        or cfg.get("single_action_approval_usd", 0.50)
    )
    try:
        single = float(single_raw)
    except (TypeError, ValueError):
        single = 0.50

    return {
        "mode": mode,
        "budget_total_usd": total,
        "reserve_pct": float(cfg.get("reserve_pct", 0.10) or 0.0),
        "single_action_approval_usd": single,
        "require_approval_for_new_paid_tool": bool(
            cfg.get("require_approval_for_new_paid_tool", True)
        ),
    }


def _preapproved() -> set[str]:
    raw = os.environ.get(_APPROVE_ENV, "").strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def tracker_for(project_dir: Path) -> Optional[CostTracker]:
    """Return the cached CostTracker for a project, creating it on first use.

    Returns None when the gate is disabled. Raises GovernanceUnavailableError
    when the tracker cannot be built (typically an unreadable cost_log.json):
    a gate that cannot read its ledger must not wave paid calls through.
    """
    if not budget_enabled():
        return None
    try:
        key = str(Path(project_dir).resolve())
    except Exception:
        return None

    with _LOCK:
        existing = _TRACKERS.get(key)
        if existing is not None:
            return existing
        log_path = Path(key) / "cost_log.json"
        try:
            cfg = _budget_config()
            tracker = CostTracker(cost_log_path=log_path, **cfg)
        except Exception as exc:
            raise GovernanceUnavailableError(
                f"Budget gate cannot read {log_path} ({type(exc).__name__}: {exc}); "
                "refusing paid calls until it is repaired or removed"
            ) from exc
        # The ceiling in force is the configured one, not the one a previous
        # run wrote into the log (CostTracker._load reads it back).
        tracker.budget_total_usd = cfg["budget_total_usd"]
        approve = _preapproved()
        if "*" in approve:
            tracker.require_approval_for_new_paid_tool = False
        else:
            for name in approve:
                tracker.approve_tool_for_session(name)
        _TRACKERS[key] = tracker
        return tracker


def charge(tracker: CostTracker, tool: str, operation: str, estimated: float) -> str:
    """Open and reserve a cost entry; returns its id.

    Governance refusals propagate unchanged. Any other failure (the log cannot
    be written) becomes GovernanceUnavailableError: spend that cannot be
    recorded is refused, not let through.
    """
    try:
        entry_id = tracker.estimate(tool, operation, estimated)
        tracker.reserve(entry_id)
        return entry_id
    except (BudgetExceededError, ApprovalRequiredError):
        raise
    except Exception as exc:
        raise GovernanceUnavailableError(
            f"Budget gate cannot record spend in {tracker.cost_log_path} "
            f"({type(exc).__name__}: {exc}); refusing paid call to {tool!r}"
        ) from exc


def refuse_unattributed(tool: str, estimated: float) -> None:
    """In cap mode, refuse a paid call that belongs to no project.

    Without a project there is no cost_log.json to account against, so the
    ceiling cannot be enforced; cap mode refuses instead of guessing.
    """
    if not budget_enabled():
        return
    try:
        mode = _budget_config()["mode"]
    except Exception:
        mode = BudgetMode.CAP
    if mode == BudgetMode.CAP:
        raise BudgetExceededError(
            f"Paid call to {tool!r} (${estimated:.2f}) belongs to no project, so "
            "cap mode cannot account for it; pass a project_dir or an output "
            "path under projects/"
        )


def cost_snapshot(project_dir: Path) -> Optional[dict[str, float]]:
    """Live spend summary for a project, or None if it has no tracker yet.

    `lib.checkpoint.write_checkpoint` calls this to fill `cost_snapshot` when
    the caller did not supply one, which is what finally puts numbers behind
    Backlot's cost meter.
    """
    with _LOCK:
        try:
            key = str(Path(project_dir).resolve())
        except Exception:
            return None
        tracker = _TRACKERS.get(key)
    if tracker is None:
        log = Path(project_dir) / "cost_log.json"
        if not log.is_file():
            return None
        try:
            tracker = tracker_for(project_dir)
        except Exception:
            return None
        if tracker is None:
            return None
    try:
        return tracker.cost_snapshot()
    except Exception:
        return None


def reset_trackers() -> None:
    """Drop cached trackers. Tests use this to isolate projects."""
    with _LOCK:
        _TRACKERS.clear()
