"""Shared fixtures: an isolated projects root and an in-process MCP client."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import mcp_types as types
import pytest
from mcp import Client

from lib import budget
from tools.base_tool import BaseTool, ToolResult, ToolRuntime, ToolTier
from tools.tool_registry import registry


class FakePaidTool(BaseTool):
    """A paid provider with no path input: attribution must come from the server."""

    name = "mcp_fake_paid"
    capability = "image_generation"
    tier = ToolTier.GENERATE
    runtime = ToolRuntime.API
    input_schema = {"type": "object", "properties": {"prompt": {"type": "string"}}}
    calls: list[dict] = []

    def estimate_cost(self, inputs: dict) -> float:
        return 0.10

    def execute(self, inputs: dict) -> ToolResult:
        FakePaidTool.calls.append(inputs)
        return ToolResult(success=True, data={"ok": True}, cost_usd=0.10)


class FakeReaderTool(BaseTool):
    """Reads a file it is given, as an image-to-video provider would before uploading it."""

    name = "mcp_fake_reader"
    capability = "video_generation"
    input_schema = {"type": "object", "properties": {"image_path": {"type": "string"},
                                                     "output_path": {"type": "string"}}}
    seen: list[dict] = []

    def execute(self, inputs: dict) -> ToolResult:
        FakeReaderTool.seen.append(inputs)
        return ToolResult(success=True, data={"image_path": inputs.get("image_path")})


class FakeSlowTool(BaseTool):
    name = "mcp_fake_slow"
    capability = "video_post"
    input_schema = {"type": "object", "properties": {"output_path": {"type": "string"}}}
    gate = None  # a threading.Event the test releases

    def estimate_runtime(self, inputs: dict) -> float:
        return 60.0

    def execute(self, inputs: dict) -> ToolResult:
        if FakeSlowTool.gate is not None:
            FakeSlowTool.gate.wait(10)
        return ToolResult(success=True, data={"rendered": inputs.get("output_path")})


class FakePublishTool(BaseTool):
    name = "mcp_fake_publish"
    capability = "publish"

    def execute(self, inputs: dict) -> ToolResult:
        return ToolResult(success=True)


FAKES = (FakePaidTool, FakeReaderTool, FakeSlowTool, FakePublishTool)


@pytest.fixture
def projects(monkeypatch, tmp_path):
    """Re-root projects/ in tmp_path everywhere it is read, gate in cap mode."""
    import lib.checkpoint
    import lib.events
    import lib.paths

    root = tmp_path / "projects"
    root.mkdir()
    for module in (lib.paths, lib.events, lib.checkpoint):
        monkeypatch.setattr(module, "PROJECTS_DIR", root)
    for var in ("OPENMONTAGE_BUDGET_DISABLED", "OPENMONTAGE_APPROVE_TOOLS",
                "OPENMONTAGE_BUDGET_TOTAL_USD", "OPENMONTAGE_SINGLE_ACTION_USD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENMONTAGE_BUDGET_MODE", "warn")  # enforce_budget_env must override it
    budget.reset_trackers()
    registry.ensure_discovered()
    for cls in FAKES:
        registry.register(cls())
    FakePaidTool.calls.clear()
    FakeReaderTool.seen.clear()
    from contrechamp_mcp.server import enforce_budget_env

    enforce_budget_env()
    yield root
    for cls in FAKES:
        registry._tools.pop(cls.name, None)
    budget.reset_trackers()


def human(answer: bool | None):
    """An elicitation callback standing in for the human; None = the client has none."""
    if answer is None:
        return None
    asked: list[str] = []

    async def callback(context, params):
        asked.append(params.message)
        return types.ElicitResult(action="accept", content={"approve": answer})

    callback.asked = asked
    return callback


@asynccontextmanager
async def connect(elicitation=None, jobs=None):
    from contrechamp_mcp.server import build_server

    async with Client(build_server(jobs), elicitation_callback=elicitation) as client:
        yield client


async def call(client, tool: str, /, **arguments):
    """Call a tool; return (is_error, payload)."""
    result = await client.call_tool(tool, arguments)
    text = result.content[0].text if result.content else "{}"
    try:
        # An error reads "Error executing tool <name>: {json}".
        payload = json.loads(text[text.find("{"):])
    except ValueError:
        payload = {"raw": text}
    return bool(result.is_error), payload


async def new_project(client, pipeline: str = "framework-smoke") -> str:
    error, payload = await call(client, "create_project", title="Essai MCP", pipeline=pipeline)
    assert not error, payload
    return payload["project_id"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
