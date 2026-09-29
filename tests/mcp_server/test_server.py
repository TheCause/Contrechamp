"""The MCP server's guarantees, driven through a real MCP client (in process).

Each guarantee closes a gap of the openmontage-zh-mcp server this one is
modelled on: `human_approved` accepted from the client, `project_id` and
paths taken as given, paid calls outside any project.
"""

from __future__ import annotations

import json
import threading

import anyio
import pytest

from tests.contracts.test_phase0_contracts import sample_artifact
from tests.mcp_server.conftest import FakePaidTool, FakeReaderTool, FakeSlowTool, call, connect, human, new_project

pytestmark = pytest.mark.anyio


# --- approvals belong to the human -----------------------------------------

RESEARCH = {"research_brief": sample_artifact("research_brief")}


async def _complete(client, pid, artifacts=RESEARCH, **extra):
    return await call(client, "write_checkpoint", project_id=pid, stage="research",
                      status="completed", artifacts=artifacts, **extra)


async def test_a_gated_stage_is_first_stored_for_review_without_asking(projects):
    callback = human(True)
    async with connect(callback) as client:
        pid = await new_project(client)
        error, out = await _complete(client, pid)
    assert not error, out
    assert out["status"] == "awaiting_human" and out["human_approved"] is False
    assert "checkpoint_research.json" in out["next_action"]
    assert callback.asked == []


async def test_gated_stage_is_not_completed_when_the_human_refuses(projects):
    async with connect(human(False)) as client:
        pid = await new_project(client)
        await _complete(client, pid)
        error, out = await _complete(client, pid, human_approved=True)  # must not be honoured
    assert not error, out
    assert out["status"] == "awaiting_human" and out["human_approved"] is False
    cp = json.loads((projects / pid / "checkpoint_research.json").read_text())
    assert cp["status"] == "awaiting_human" and cp["human_approved"] is False


async def test_the_approval_is_not_an_argument_the_model_can_fill(projects):
    async with connect(human(None)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        pid = await new_project(client)
        await _complete(client, pid)
        error, out = await _complete(client, pid, approval={"approve": True})
    for name in ("write_checkpoint", "request_paid_tool_approval"):
        props = tools[name].input_schema.get("properties", {})
        assert "approval" not in props and "human_approved" not in props
    assert error and out["code"] == "E_HUMAN_CHANNEL_UNAVAILABLE"
    cp = json.loads((projects / pid / "checkpoint_research.json").read_text())
    assert cp["status"] == "awaiting_human"


async def test_gated_stage_completes_when_the_human_approves_what_is_stored(projects):
    callback = human(True)
    async with connect(callback) as client:
        pid = await new_project(client)
        await _complete(client, pid)
        error, out = await _complete(client, pid)
    assert not error, out
    assert out["status"] == "completed" and out["human_approved"] is True
    assert len(callback.asked) == 1
    assert f"checkpoint_research.json" in callback.asked[0] and out["fingerprint"] in callback.asked[0]


async def test_an_approval_cannot_be_obtained_for_a_swapped_payload(projects):
    """The human reviews what is on disk; other artifacts in the approving call
    are stored for review again, never approved blind."""
    callback = human(True)
    swapped = {"research_brief": {**sample_artifact("research_brief"), "topic": "Something else"}}
    async with connect(callback) as client:
        pid = await new_project(client)
        await _complete(client, pid)
        error, out = await _complete(client, pid, artifacts=swapped)
    assert not error, out
    assert out["status"] == "awaiting_human" and callback.asked == []
    cp = json.loads((projects / pid / "checkpoint_research.json").read_text())
    assert cp["artifacts"]["research_brief"]["topic"] == "Something else"


async def test_client_without_a_human_channel_cannot_complete_a_gate(projects):
    async with connect(human(None)) as client:
        pid = await new_project(client)
        await _complete(client, pid)
        error, out = await _complete(client, pid)
    assert error and out["code"] == "E_HUMAN_CHANNEL_UNAVAILABLE"
    assert "approve-stage" in out["message"]
    cp = json.loads((projects / pid / "checkpoint_research.json").read_text())
    assert cp["status"] == "awaiting_human"


async def test_a_project_without_its_pipeline_refuses_checkpoints(projects):
    async with connect(human(True)) as client:
        pid = await new_project(client)
        marker = projects / pid / "project.json"
        data = json.loads(marker.read_text())
        del data["pipeline_type"]
        marker.write_text(json.dumps(data))
        error, out = await _complete(client, pid)
    assert error and out["code"] == "E_CHECKPOINT_REFUSED"
    assert not (projects / pid / "checkpoint_research.json").exists()


def test_the_terminal_command_approves_an_awaiting_stage(projects):
    from lib.checkpoint import init_project, write_checkpoint
    from openmontage_mcp.__main__ import main

    init_project("p", title="P", pipeline_type="framework-smoke", pipeline_dir=projects)
    write_checkpoint(projects, "p", "research", "awaiting_human",
                     {"research_brief": sample_artifact("research_brief")},
                     pipeline_type="framework-smoke")
    assert main(["approve-stage", "p", "research"]) == 0
    cp = json.loads((projects / "p" / "checkpoint_research.json").read_text())
    assert cp["status"] == "completed" and cp["human_approved"] is True


# --- paths ------------------------------------------------------------------

async def test_project_id_cannot_climb_out_of_projects(projects):
    async with connect() as client:
        error, out = await call(client, "run_tool", name="mcp_fake_reader",
                                inputs={}, project_id="../tools")
    assert error and out["code"] == "E_PATH_REFUSED"
    assert FakeReaderTool.seen == []


async def test_a_tool_cannot_be_pointed_at_a_file_outside_the_project(projects):
    async with connect() as client:
        pid = await new_project(client)
        error, out = await call(client, "run_tool", name="mcp_fake_reader",
                                inputs={"image_path": "/etc/passwd"}, project_id=pid)
    assert error and out["code"] == "E_PATH_REFUSED"
    assert FakeReaderTool.seen == []


async def test_a_web_url_cannot_smuggle_a_path(projects):
    """Regression: "https://../../x" under output_path escaped the project."""
    async with connect() as client:
        pid = await new_project(client)
        for bad in ("https://../../../../x.srt", "HTTP://../x"):
            error, out = await call(client, "run_tool", name="mcp_fake_reader",
                                    inputs={"output_path": bad}, project_id=pid)
            assert error and out["code"] == "E_PATH_REFUSED", bad
    assert FakeReaderTool.seen == []


@pytest.mark.parametrize("target", ["project.json", "cost_log.json", "checkpoint_research.json",
                                    "events.jsonl", "decision_log.json", "history/x.json", "."])
async def test_governance_files_are_out_of_reach(projects, target):
    """Regression: overwriting project.json erased the gate, cost_log.json the spend."""
    async with connect() as client:
        pid = await new_project(client)
        error, out = await call(client, "run_tool", name="mcp_fake_reader",
                                inputs={"output_path": target}, project_id=pid)
    assert error and out["code"] == "E_PATH_REFUSED", target
    assert FakeReaderTool.seen == []


@pytest.mark.parametrize("inputs", [
    {"custom_vf": "movie=/etc/x.mp4[m];[in][m]overlay[out]"},
    {"custom_af": "amovie=/etc/x.wav"},
    {"extra_args": ["--flag"]},
    {"workflow_json": {"1": {}}},
    {"extra_params": {"x": 1}},
    {"allow_unsafe_code": True},
    {"require_approval": False},
    {"tts_tool": "mcp_fake_publish"},
    {"images": ["config.yaml"]},
])
async def test_inputs_that_carry_code_filters_or_waivers_are_refused(projects, inputs):
    async with connect() as client:
        pid = await new_project(client)
        error, out = await call(client, "run_tool", name="mcp_fake_reader",
                                inputs={"output_path": "renders/x.mp4", **inputs}, project_id=pid)
    assert error and out["code"] in ("E_PATH_REFUSED", "E_TOOL_NOT_FOUND"), (inputs, out)
    assert FakeReaderTool.seen == []


async def test_harmless_values_of_those_keys_pass(projects):
    """Silent side."""
    async with connect() as client:
        pid = await new_project(client)
        (projects / pid / "assets" / "images" / "a.png").write_bytes(b"x")
        error, out = await call(client, "run_tool", name="mcp_fake_reader", project_id=pid,
                                inputs={"allow_unsafe_code": False, "require_approval": True,
                                        "custom_vf": "", "tts_tool": "mcp_fake_paid",
                                        "images": ["assets/images/a.png"], "style": "docs",
                                        "extra_images": ["Makefile"],
                                        "output_path": "renders"})
    assert not error, out
    seen = FakeReaderTool.seen[0]
    assert seen["images"] == [str((projects / pid / "assets/images/a.png").resolve())]
    assert seen["style"] == "docs"
    # a repository file named without a dot is re-anchored in the project, not read
    assert seen["extra_images"] == [str((projects / pid / "Makefile").resolve())]


async def test_a_tool_must_be_given_its_output_inside_the_project(projects):
    """Regression: an omitted output_path let subtitle_gen write subtitles.srt
    into the server's working directory, the repository."""
    async with connect() as client:
        pid = await new_project(client)
        error, out = await call(client, "run_tool", name="mcp_fake_slow", inputs={}, project_id=pid)
    assert error and out["code"] == "E_INVALID_INPUT" and "output_path" in out["message"]


async def test_a_tool_reads_inside_its_project(projects):
    """Silent side."""
    async with connect() as client:
        pid = await new_project(client)
        error, out = await call(client, "run_tool", name="mcp_fake_reader",
                                inputs={"image_path": "assets/images/a.png",
                                        "output_path": "renders/out.mp4"}, project_id=pid)
    assert not error, out
    assert FakeReaderTool.seen[0]["image_path"] == str((projects / pid / "assets/images/a.png").resolve())


# --- spend ------------------------------------------------------------------

async def test_first_paid_use_waits_for_the_human_then_is_charged_to_the_project(projects):
    async with connect(human(True)) as client:
        pid = await new_project(client)
        error, out = await call(client, "run_tool", name="mcp_fake_paid",
                                inputs={"prompt": "x"}, project_id=pid)
        assert error and out["code"] == "E_APPROVAL_REQUIRED"
        assert out["next_action"] == "request_paid_tool_approval"
        assert FakePaidTool.calls == []

        error, out = await call(client, "request_paid_tool_approval", project_id=pid,
                                tool_name="mcp_fake_paid")
        assert not error and out["approved"] is True
        error, out = await call(client, "run_tool", name="mcp_fake_paid",
                                inputs={"prompt": "x"}, project_id=pid)
    assert not error, out
    log = json.loads((projects / pid / "cost_log.json").read_text())
    assert [e["tool"] for e in log["entries"] if e["status"] == "completed"] == ["mcp_fake_paid"]


async def test_a_refused_paid_tool_stays_refused(projects):
    async with connect(human(False)) as client:
        pid = await new_project(client)
        error, out = await call(client, "request_paid_tool_approval", project_id=pid,
                                tool_name="mcp_fake_paid")
        assert not error and out["approved"] is False
        error, out = await call(client, "run_tool", name="mcp_fake_paid",
                                inputs={"prompt": "x"}, project_id=pid)
    assert error and out["code"] == "E_APPROVAL_REQUIRED"
    assert FakePaidTool.calls == []


async def test_the_ceiling_refuses_even_an_approved_tool(projects, monkeypatch):
    monkeypatch.setenv("OPENMONTAGE_BUDGET_TOTAL_USD", "0.05")
    async with connect(human(True)) as client:
        pid = await new_project(client)
        await call(client, "request_paid_tool_approval", project_id=pid, tool_name="mcp_fake_paid")
        error, out = await call(client, "run_tool", name="mcp_fake_paid",
                                inputs={"prompt": "x"}, project_id=pid)
    assert error and out["code"] == "E_BUDGET_EXCEEDED"
    assert FakePaidTool.calls == []


@pytest.mark.parametrize("var", ["OPENMONTAGE_BUDGET_DISABLED", "OPENMONTAGE_APPROVE_TOOLS"])
def test_server_refuses_to_start_with_the_gate_bypassed(projects, monkeypatch, var):
    from openmontage_mcp.server import StartupRefused, enforce_budget_env

    monkeypatch.setenv(var, "1" if var.endswith("DISABLED") else "*")
    with pytest.raises(StartupRefused, match=var):
        enforce_budget_env()


def test_server_forces_cap_mode(projects):
    import os

    assert os.environ["OPENMONTAGE_BUDGET_MODE"] == "cap"


# --- surface ----------------------------------------------------------------

async def test_publishing_tools_are_not_exposed(projects):
    async with connect() as client:
        error, out = await call(client, "list_capabilities")
        names = {t["name"] for t in out["tools"]}
        assert "mcp_fake_paid" in names and "mcp_fake_publish" not in names
        assert not names & {"screen_recorder", "cap_recorder", "screen_capture_selector"}
        pid = await new_project(client)
        error, out = await call(client, "run_tool", name="mcp_fake_publish", inputs={}, project_id=pid)
    assert error and out["code"] == "E_TOOL_NOT_FOUND"


async def test_long_call_runs_in_the_background_and_cancel_is_honest(projects):
    FakeSlowTool.gate = threading.Event()
    try:
        async with connect() as client:
            pid = await new_project(client)
            error, out = await call(client, "run_tool", name="mcp_fake_slow",
                                    inputs={"output_path": "renders/x.mp4"}, project_id=pid)
            assert not error and out["status"] == "running"
            job_id = out["job_id"]
            error, out = await call(client, "cancel_job", job_id=job_id)
            assert out["cancel_requested"] is True and "cannot be interrupted" in out["message"]
            FakeSlowTool.gate.set()
            for _ in range(100):
                error, out = await call(client, "get_job_status", job_id=job_id)
                if out["next_action"] == "done":
                    break
                await anyio.sleep(0.05)
    finally:
        FakeSlowTool.gate = None
    assert out["status"] == "completed" and out["cancel_requested"] is True
