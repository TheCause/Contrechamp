"""The server as a client really starts it: a subprocess speaking stdio."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items()
           if k.removeprefix("CONTRECHAMP_").removeprefix("OPENMONTAGE_")
           not in ("BUDGET_DISABLED", "APPROVE_TOOLS", "PROJECTS_DIR")}
    env["CONTRECHAMP_PROJECTS_DIR"] = str(tmp_path / "projects")
    env.update(extra)
    return env


@pytest.mark.anyio
async def test_stdio_server_creates_a_project(tmp_path):
    (tmp_path / "projects").mkdir()
    params = StdioServerParameters(command=sys.executable, args=[str(REPO_ROOT / "contrechamp_mcp" / "__main__.py")],
                                   cwd=str(tmp_path), env=_env(tmp_path))
    async with Client(params, read_timeout_seconds=60) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        result = await client.call_tool("create_project", {"title": "Par stdio", "pipeline": "framework-smoke"})
    assert {"run_tool", "render_video", "write_checkpoint", "request_paid_tool_approval"} <= names
    assert not result.is_error, result.content
    payload = json.loads(result.content[0].text)
    assert (tmp_path / "projects" / payload["project_id"] / "project.json").is_file()


def test_stdio_server_refuses_to_start_with_the_gate_disabled(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "contrechamp_mcp"], cwd=REPO_ROOT,
                          env=_env(tmp_path, CONTRECHAMP_BUDGET_DISABLED="1"),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2
    assert "CONTRECHAMP_BUDGET_DISABLED" in proc.stderr
