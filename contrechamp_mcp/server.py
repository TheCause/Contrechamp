"""Contrechamp as an MCP server (stdio).

    python -m contrechamp_mcp            # serve over stdio

What an MCP client can and cannot do here:

- It works inside ONE project at a time: every call names a `project_id`,
  and every path it sends is confined to `projects/<project_id>/`
  (`confine.py`).
- It spends only through the budget gate every tool call already crosses
  (`lib/budget.py`), forced to `cap` mode for the whole server process. A paid
  call is always attributed to its project, so the ceiling always applies.
- It cannot approve anything. A gated stage and the first paid use of a tool
  are approved by the human, through elicitation or a terminal command
  (`approvals.py`). No tool argument carries an approval.
- It cannot publish: tools with the `publish` capability are not exposed.
- Renders and long generations run in background threads; the client polls
  them (`jobs.py`).

Credit: the shape of this server (business-level tools, a job tracker,
`run_tool` over the registry) follows the MCP server of the openmontage-zh-mcp
fork by noah-1106 (AGPL-3.0). This implementation is rewritten for mcp 2.x
and closes the gaps listed above.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any

import anyio
import jsonschema
from pydantic import BaseModel, Field

from mcp.server.elicitation import AcceptedElicitation, ElicitationResult
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resolve import Elicit, Resolve

from lib.checkpoint import (
    CheckpointValidationError,
    _stage_requires_approval,
    get_completed_stages,
    get_next_stage,
    get_pipeline_stages,
    init_project,
    read_checkpoint,
    write_checkpoint as _write_checkpoint,
)
from lib import env_names
from lib.pipeline_loader import list_pipelines
from tools.cost_tracker import ApprovalRequiredError, BudgetExceededError

from contrechamp_mcp import approvals
from contrechamp_mcp.confine import ConfinementError, confine_inputs, confine_path, project_dir, projects_root
from contrechamp_mcp.jobs import JobLimitError, JobTracker

# Tools estimated above this many seconds run as background jobs.
INLINE_MAX_SECONDS = 5.0
# Not exposed: publishing (leaves the machine) and screen capture (records
# the user's screen and audio into a project a paid tool can then upload).
HIDDEN_CAPABILITIES = frozenset({"publish", "screen_capture"})
# Short calls run inline, off the event loop; this bounds how many at once.
INLINE_MAX_CONCURRENT = 4
_OUTPUT_KEYS = frozenset({"output_path", "output_dir", "output_file"})
_REFUSED_BUDGET_ENV = ("BUDGET_DISABLED", "APPROVE_TOOLS")  # under both spellings, see lib/env_names.py


class StartupRefused(RuntimeError):
    """The environment would let an MCP client spend without the human."""


def enforce_budget_env() -> None:
    """Fail closed on switches that disable the gate; force `cap` mode.

    `CONTRECHAMP_BUDGET_DISABLED` and `CONTRECHAMP_APPROVE_TOOLS`, under either
    spelling (legacy `OPENMONTAGE_*` too), stand in for the human who approves
    spend. Under MCP the caller is an agent, so the
    server refuses to start with either set rather than silently dropping it.
    `cap` makes the gate refuse spend beyond the ceiling instead of warning.
    """
    set_vars = [var for name in _REFUSED_BUDGET_ENV for var in env_names.set_names(name)]
    if set_vars:
        raise StartupRefused(
            f"{', '.join(set_vars)} set: the MCP server refuses to start with the "
            "budget gate disabled or tools pre-approved. Unset them; approvals go "
            "through the human (see contrechamp_mcp/approvals.py)."
        )
    env_names.force("BUDGET_MODE", "cap")


def _fail(code: str, message: str, **extra: Any) -> ToolError:
    return ToolError(json.dumps({"status": "error", "code": code, "message": message, **extra}, ensure_ascii=False))


def _error_for(exc: BaseException) -> tuple[str, str]:
    """Map an exception to a stable error code the client can branch on."""
    from lib.budget import GovernanceUnavailableError

    if isinstance(exc, ConfinementError):
        return "E_PATH_REFUSED", str(exc)
    if isinstance(exc, jsonschema.ValidationError):
        return "E_INVALID_INPUT", exc.message
    if isinstance(exc, GovernanceUnavailableError):
        return "E_BUDGET_UNAVAILABLE", str(exc)
    if isinstance(exc, BudgetExceededError):
        return "E_BUDGET_EXCEEDED", str(exc)
    if isinstance(exc, ApprovalRequiredError):
        return "E_APPROVAL_REQUIRED", str(exc)
    if isinstance(exc, CheckpointValidationError):
        return "E_CHECKPOINT_REFUSED", str(exc)
    return "E_TOOL_FAILED", f"{type(exc).__name__}: {exc}"


def _raise_for(exc: BaseException) -> ToolError:
    code, message = _error_for(exc)
    extra: dict[str, Any] = {}
    match = re.search(r"First paid use of tool '([^']+)'", message)
    if code == "E_APPROVAL_REQUIRED" and match:
        extra["next_action"] = "request_paid_tool_approval"
        extra["tool_name"] = match.group(1)
    return _fail(code, message, **extra)


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _result_payload(result: Any) -> dict[str, Any]:
    return _jsonable({
        "success": bool(getattr(result, "success", False)),
        "data": getattr(result, "data", None),
        "artifacts": getattr(result, "artifacts", None),
        "error": getattr(result, "error", None),
        "cost_usd": getattr(result, "cost_usd", None),
        "duration_seconds": getattr(result, "duration_seconds", None),
    })


class _Approval(BaseModel):
    approve: bool = Field(description="true to approve, false to refuse")


class _Unreachable:
    """Resolver outcome when the client cannot relay a question to its user."""


def _client_can_ask(ctx: Context) -> bool:
    caps = ctx.client_capabilities
    return bool(caps is not None and caps.elicitation is not None)


def _approved(outcome: Any, fallback_command: str) -> bool:
    """Read a resolver outcome: True only for an explicit yes from the human."""
    if isinstance(outcome, AcceptedElicitation) and isinstance(outcome.data, _Unreachable):
        raise _fail(
            "E_HUMAN_CHANNEL_UNAVAILABLE",
            "this client cannot relay the question to its user; the human can "
            f"approve from a terminal: {fallback_command}",
        )
    return (
        isinstance(outcome, AcceptedElicitation)
        and isinstance(outcome.data, _Approval)
        and outcome.data.approve
    )


def _project_exists(project_id: str) -> bool:
    try:
        return project_dir(project_id).is_dir()
    except ConfinementError:
        return False


def artifacts_digest(artifacts: Any) -> str:
    raw = json.dumps(artifacts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _stored_awaiting(project_id: str, stage: str) -> dict[str, Any] | None:
    try:
        cp = read_checkpoint(projects_root(), project_id, stage)
    except Exception:
        return None
    return cp if cp and cp.get("status") == "awaiting_human" else None


def _stage_question(project_id: str, stage: str, status: str, artifacts: dict[str, Any], ctx: Context) -> Any:
    """Resolver: put a gated stage to the human, bound to what is on disk.

    The human is asked only about a checkpoint already stored as
    `awaiting_human` whose artifacts are exactly the ones in this call, so the
    answer approves what the human can open and read, not a payload the model
    could swap. A resolved parameter is filled by the framework, never from
    the tool's arguments, so the model cannot supply the answer.
    """
    if status != "completed" or not _project_exists(project_id):
        return _Approval(approve=False)
    pipeline = approvals.pipeline_type(project_id)
    if pipeline is None or not _stage_requires_approval(pipeline, stage):
        return _Approval(approve=False)
    stored = _stored_awaiting(project_id, stage)
    if stored is None or artifacts_digest(stored.get("artifacts")) != artifacts_digest(artifacts):
        return _Approval(approve=False)  # stored first as awaiting_human, asked next call
    if not _client_can_ask(ctx):
        return _Unreachable()
    return Elicit(
        f"Contrechamp — project {project_id!r}: approve stage {stage!r}? "
        f"Its content is in projects/{project_id}/checkpoint_{stage}.json "
        f"(fingerprint {artifacts_digest(artifacts)}). Read it before answering.",
        _Approval,
    )


def _tool_question(project_id: str, tool_name: str, ctx: Context) -> Any:
    """Resolver: ask the human whether a paid tool may spend on this project."""
    if not _project_exists(project_id):
        return _Approval(approve=False)
    if not _client_can_ask(ctx):
        return _Unreachable()
    return Elicit(
        f"Contrechamp — project {project_id!r}: allow the paid tool {tool_name!r} "
        "to spend from this project's budget?",
        _Approval,
    )


def _existing_project(project_id: str) -> Any:
    try:
        root = project_dir(project_id)
    except ConfinementError as exc:
        raise _raise_for(exc) from exc
    if not root.is_dir():
        raise _fail("E_PROJECT_NOT_FOUND", f"project {project_id!r} does not exist; call create_project first")
    return root


def build_server(jobs: JobTracker | None = None) -> MCPServer:
    from tools.tool_registry import registry

    registry.ensure_discovered()
    tracker = jobs if jobs is not None else JobTracker()
    inline_slots = anyio.Semaphore(INLINE_MAX_CONCURRENT)
    server = MCPServer(
        "contrechamp",
        instructions=(
            "Contrechamp video production tools. Work inside one project: call "
            "create_project, then pass its project_id everywhere; paths are relative "
            "to that project. Long calls return a job_id to poll. Approvals of gated "
            "stages and of paid tools go to the human, never through your arguments."
        ),
    )

    def _exposed(name: str):
        tool = registry.get(name)
        if tool is None or tool.capability in HIDDEN_CAPABILITIES:
            return None
        return tool

    @server.tool()
    def list_capabilities(category: str | None = None) -> dict[str, Any]:
        """Light index of the tools and pipelines. Call describe_tool for a tool's input schema."""
        tools = []
        for name in sorted(registry.list_all()):
            tool = _exposed(name)
            if tool is None or (category and tool.capability != category):
                continue
            try:
                status = tool.get_status().value
            except Exception:
                status = "unknown"
            tools.append({
                "name": name,
                "capability": tool.capability,
                "provider": tool.provider,
                "status": status,
            })
        return {"pipelines": list_pipelines(), "tools": tools}

    @server.tool()
    def describe_tool(name: str) -> dict[str, Any]:
        """Full description and input schema of one tool."""
        tool = _exposed(name)
        if tool is None:
            raise _fail("E_TOOL_NOT_FOUND", f"no exposed tool named {name!r}")
        info = tool.get_info()
        return _jsonable({
            "name": name,
            "capability": tool.capability,
            "provider": tool.provider,
            "best_for": tool.best_for,
            "input_schema": tool.input_schema,
            "install_instructions": info.get("install_instructions"),
            "note": "Paths in inputs are relative to projects/<project_id>/.",
        })

    @server.tool()
    def create_project(title: str, pipeline: str, brief: str | None = None) -> dict[str, Any]:
        """Create a project workspace; returns its project_id and stages."""
        if pipeline not in list_pipelines():
            raise _fail("E_INVALID_INPUT", f"unknown pipeline {pipeline!r}", pipelines=list_pipelines())
        base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:56] or "project"
        project_id, n = base, 1
        while (projects_root() / project_id).exists():
            n += 1
            project_id = f"{base}-{n}"
        root = init_project(project_id, title=title, pipeline_type=pipeline, pipeline_dir=projects_root())
        if brief:
            (root / "artifacts" / "brief.txt").write_text(brief, encoding="utf-8")
        stages = get_pipeline_stages(pipeline)
        return {"project_id": project_id, "pipeline": pipeline, "stages": stages,
                "next_stage": stages[0] if stages else None}

    @server.tool()
    def get_project_status(project_id: str) -> dict[str, Any]:
        """Completed stages, next stage, and stages waiting for the human."""
        _existing_project(project_id)
        pipeline = approvals.pipeline_type(project_id)
        completed = get_completed_stages(projects_root(), project_id, pipeline)
        awaiting = []
        for stage in get_pipeline_stages(pipeline):
            cp = read_checkpoint(projects_root(), project_id, stage)
            if cp and cp.get("status") == "awaiting_human":
                awaiting.append(stage)
        return {
            "project_id": project_id,
            "pipeline": pipeline,
            "completed_stages": completed,
            "next_stage": get_next_stage(projects_root(), project_id, pipeline),
            "awaiting_human": awaiting,
        }

    def _prepare(name: str, inputs: dict[str, Any], project_id: str):
        root = _existing_project(project_id)
        tool = _exposed(name)
        if tool is None:
            raise _fail("E_TOOL_NOT_FOUND", f"no exposed tool named {name!r}")
        try:
            confined = confine_inputs(project_id, inputs)
            jsonschema.validate(instance=confined, schema=tool.input_schema or {"type": "object"})
        except (ConfinementError, jsonschema.ValidationError) as exc:
            raise _raise_for(exc) from exc
        # Attribute the call to the project so the budget gate accounts for it.
        confined.setdefault("project_dir", str(root))
        # A tool that dispatches to another tool by name (narrate_script's
        # tts_tool) must not reach one hidden from MCP.
        for key, value in confined.items():
            if key.endswith("_tool") and isinstance(value, str) and registry.get(value) is not None:
                if _exposed(value) is None:
                    raise _fail("E_TOOL_NOT_FOUND", f"{key}={value!r} names a tool not exposed through MCP")
        # A tool left to its default output writes relative to the server's
        # working directory, the repository: the client must name the output.
        declared = set((tool.input_schema or {}).get("properties", {}))
        outputs = declared & _OUTPUT_KEYS
        if outputs and not outputs & set(confined):
            raise _fail("E_INVALID_INPUT",
                        f"name the output through MCP: pass one of {sorted(outputs)} inside the project")
        return tool, confined

    def _submit(tool: Any, inputs: dict[str, Any], project_id: str) -> dict[str, Any]:
        try:
            job = tracker.submit(
                lambda: _result_payload(tool.execute(inputs)),
                tool_name=tool.name, project_id=project_id, on_error=_error_for,
            )
        except JobLimitError as exc:
            raise _fail("E_QUEUE_FULL", str(exc)) from exc
        return {"status": "queued", **job.public()}

    @server.tool()
    async def run_tool(name: str, inputs: dict[str, Any], project_id: str) -> dict[str, Any]:
        """Run one tool inside a project. Long calls return a job_id to poll."""
        import asyncio

        tool, confined = _prepare(name, inputs, project_id)
        try:
            estimated = float(tool.estimate_runtime(confined) or 0.0)
        except Exception:
            estimated = 0.0
        if estimated > INLINE_MAX_SECONDS:
            return _submit(tool, confined, project_id)
        try:
            async with inline_slots:
                result = await asyncio.to_thread(tool.execute, confined)
        except Exception as exc:
            raise _raise_for(exc) from exc
        return {"status": "completed", "result": _result_payload(result)}

    @server.tool()
    def render_video(project_id: str, output_path: str = "renders/final.mp4") -> dict[str, Any]:
        """Render the project's final video from its edit_decisions and asset_manifest, in the background."""
        root = _existing_project(project_id)
        artifacts: dict[str, Any] = {}
        for name in ("edit_decisions", "asset_manifest"):
            path = root / "artifacts" / f"{name}.json"
            if not path.is_file():
                raise _fail("E_ARTIFACT_MISSING", f"{name}.json not found in projects/{project_id}/artifacts/")
            artifacts[name] = json.loads(path.read_text(encoding="utf-8"))
        tool, confined = _prepare(
            "video_compose",
            {"operation": "render", **artifacts, "output_path": output_path},
            project_id,
        )
        return _submit(tool, confined, project_id)

    @server.tool()
    def get_job_status(job_id: str) -> dict[str, Any]:
        """Status of a background job; poll until next_action is 'done'."""
        job = tracker.get(job_id)
        if job is None:
            raise _fail("E_JOB_NOT_FOUND", f"no job {job_id!r} in this server process")
        return job.public()

    @server.tool()
    def list_jobs(status: str | None = None) -> dict[str, Any]:
        """Background jobs of this server process, optionally filtered by status."""
        return {"jobs": tracker.list(status)}

    @server.tool()
    def cancel_job(job_id: str) -> dict[str, Any]:
        """Record a cancellation. The running tool is not interrupted (see the returned message)."""
        result = tracker.cancel(job_id)
        if result is None:
            raise _fail("E_JOB_NOT_FOUND", f"no job {job_id!r} in this server process")
        return result

    @server.tool()
    def write_checkpoint(
        project_id: str,
        stage: str,
        status: str,
        artifacts: dict[str, Any],
        approval: Annotated[ElicitationResult[_Approval], Resolve(_stage_question)],
    ) -> dict[str, Any]:
        """Record a stage. A gated stage asked 'completed' is first stored
        'awaiting_human'; call again with the same artifacts to put it to the human."""
        _existing_project(project_id)
        if status not in ("completed", "awaiting_human", "in_progress", "failed"):
            raise _fail("E_INVALID_INPUT", f"invalid status {status!r}")
        pipeline = approvals.pipeline_type(project_id)
        if pipeline is None:
            # Without the pipeline the gate cannot be read: refuse, do not guess.
            raise _fail("E_CHECKPOINT_REFUSED", f"project {project_id!r} has no readable pipeline_type")
        gated = bool(_stage_requires_approval(pipeline, stage))
        approved = gated and _approved(approval, f"python -m contrechamp_mcp approve-stage {project_id} {stage}")
        result: dict[str, Any] = {}
        if status == "completed" and gated and not approved:
            status = "awaiting_human"
            result["next_action"] = (
                f"the human reviews projects/{project_id}/checkpoint_{stage}.json; call "
                "write_checkpoint again with status 'completed' and the same artifacts to ask for approval"
            )
        try:
            _write_checkpoint(
                projects_root(), project_id, stage, status, artifacts,
                pipeline_type=pipeline, human_approved=approved,
            )
        except (CheckpointValidationError, ValueError) as exc:
            raise _fail("E_CHECKPOINT_REFUSED", str(exc)) from exc
        return {"project_id": project_id, "stage": stage, "status": status, "human_approved": approved,
                "fingerprint": artifacts_digest(artifacts),
                "next_stage": get_next_stage(projects_root(), project_id, pipeline), **result}

    @server.tool()
    def request_paid_tool_approval(
        project_id: str,
        tool_name: str,
        approval: Annotated[ElicitationResult[_Approval], Resolve(_tool_question)],
    ) -> dict[str, Any]:
        """Ask the human to approve the first paid use of a tool on this project."""
        _existing_project(project_id)
        if _exposed(tool_name) is None:
            raise _fail("E_TOOL_NOT_FOUND", f"no exposed tool named {tool_name!r}")
        if not _approved(approval, f"python -m contrechamp_mcp approve-tool {project_id} {tool_name}"):
            return {"project_id": project_id, "tool_name": tool_name, "approved": False}
        return approvals.approve_paid_tool(project_id, tool_name)

    return server


__all__ = ["build_server", "enforce_budget_env", "StartupRefused", "confine_path"]
