"""Human decisions the MCP client must never be able to take by itself.

Two decisions belong to the human: approving a gated pipeline stage, and
approving the first paid use of a tool on a project. The MCP server exposes
no argument that carries either decision. It reaches the human in one of two
ways:

- **elicitation**: the server asks through the MCP client, which shows the
  question to its user (Claude Code opens a dialog). The model driving the
  client does not answer it.
- **this module's command line**, run by the human in a terminal, for
  clients that cannot relay a question:

      python -m contrechamp_mcp approve-stage <project_id> <stage>
      python -m contrechamp_mcp approve-tool <project_id> <tool_name>

Known limit: an agent that also has a shell on this machine can run that
command itself. The MCP boundary protects against an MCP client; it cannot
protect against a process with the same user rights as the human.
"""

from __future__ import annotations

import json
from typing import Any

from lib.checkpoint import PROJECT_MARKER_FILENAME, read_checkpoint, write_checkpoint

from contrechamp_mcp.confine import project_dir, projects_root


def pipeline_type(project_id: str) -> str | None:
    marker = project_dir(project_id) / PROJECT_MARKER_FILENAME
    try:
        return json.loads(marker.read_text(encoding="utf-8")).get("pipeline_type")
    except (OSError, ValueError):
        return None


def approve_stage(project_id: str, stage: str) -> dict[str, Any]:
    """Turn an `awaiting_human` checkpoint into `completed`, approved."""
    cp = read_checkpoint(projects_root(), project_id, stage)
    if cp is None:
        raise ValueError(f"no checkpoint for stage {stage!r} in project {project_id!r}")
    if cp.get("status") != "awaiting_human":
        raise ValueError(
            f"stage {stage!r} is {cp.get('status')!r}, not awaiting_human; nothing to approve"
        )
    pipeline = pipeline_type(project_id)
    if pipeline is None:
        raise ValueError(f"project {project_id!r} has no readable pipeline_type; refusing to approve")
    write_checkpoint(
        projects_root(),
        project_id,
        stage,
        "completed",
        cp.get("artifacts") or {},
        pipeline_type=pipeline,
        human_approval_required=True,
        human_approved=True,
    )
    return {"project_id": project_id, "stage": stage, "status": "completed", "human_approved": True}


def approve_paid_tool(project_id: str, tool_name: str) -> dict[str, Any]:
    """Record, in the project's cost_log.json, that this tool may spend."""
    from lib import budget

    tracker = budget.tracker_for(project_dir(project_id))
    if tracker is None:
        raise RuntimeError("the budget gate is disabled; there is nothing to approve")
    tracker.approve_tool(tool_name)
    return {"project_id": project_id, "tool_name": tool_name, "approved": True}
