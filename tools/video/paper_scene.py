"""Paper scene: validate, compile and render a declarative paper-cut scene.

``validate``: run every pre-render check of the scene (lib/paper_scene) and
return the report — vocabulary, references, timing, limb overlap, jumps,
off-frame actions, contacts, zoom speed, the bottom band of 9:16 layouts,
beats inside their phrase, on-screen text.

``compile``: the same checks, then (only when they pass) write the three
outputs: one HyperFrames project per layout (rendered with paper-cut.js),
``sfx_events.json`` for ``sfx_synth`` and ``story_beats.json`` for
``mute_review`` — plus ``report.json`` and ``scene.resolved.json``.

``render`` (render machine: Chrome + HyperFrames): compile, render each layout
with ``npx hyperframes render``, synthesize the sound with ``sfx_synth`` and mux
it under each video. The mute review stays a separate, required step
(skills/creative/paper-scene.md).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

HF_SPEC_ENV = "CONTRECHAMP_HYPERFRAMES_SPEC"


class PaperScene(BaseTool):
    name = "paper_scene"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "animation"
    provider = "contrechamp"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["python:jsonschema", "python:numpy"]
    install_instructions = (
        "validate and compile need only Python. render needs Node (npx) and Chrome for "
        "HyperFrames, and ffmpeg to mux the sound; pin the CLI with CONTRECHAMP_HYPERFRAMES_SPEC "
        "(e.g. hyperframes@0.8.105)."
    )
    agent_skills = ["hyperframes"]

    capabilities = ["validate_paper_scene", "compile_paper_scene", "render_paper_scene"]
    supports = {
        "offline": True,
        "layouts": ["9:16", "16:9"],
        "outputs": ["hyperframes_project", "sfx_events", "story_beats"],
    }
    best_for = [
        "wordless paper-cut B-roll and explainer inserts written as data from a text brief",
        "one timeline in 16:9 and 9:16, cut to narration marks",
        "scenes whose sound events and story beats must agree with the picture",
    ]
    not_good_for = ["realistic footage", "lip-sync or dialogue", "free-form motion outside the library"]

    input_schema = {
        "type": "object",
        "required": ["operation", "scene"],
        "properties": {
            "operation": {"type": "string", "enum": ["validate", "compile", "render"]},
            "scene": {"description": "The scene (schemas/artifacts/paper_scene.schema.json): an object or a JSON file path"},
            "output_dir": {"type": "string", "description": "compile / render: where the outputs go"},
            "fps": {"type": "integer", "default": 30, "description": "render"},
            "with_sound": {"type": "boolean", "default": True, "description": "render: synthesize and mux the sound"},
            "layouts": {"type": "array", "items": {"type": "string"},
                        "description": "render: only these layouts (default: all)"},
        },
    }
    resource_profile = ResourceProfile(cpu_cores=4, ram_mb=3000, vram_mb=0, disk_mb=500, network_required=False)
    idempotency_key_fields = ["operation", "scene", "fps", "with_sound"]
    side_effects = ["compile / render: write files under output_dir"]
    user_visible_verification = [
        "Read report.json: every check pass, or the issue to fix",
        "Watch each layout, then run the mute review with story_beats.json",
    ]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        from lib.paper_scene import compile_scene, scene_from, validate

        start = time.time()
        op = inputs.get("operation")
        try:
            scene = scene_from(inputs.get("scene"))
        except (OSError, ValueError, TypeError) as e:
            return ToolResult(success=False, error=f"cannot read the scene: {e}")
        if op == "validate":
            rep = validate(scene)
            return ToolResult(success=rep["status"] == "pass", data={"report": rep},
                              error=None if rep["status"] == "pass" else "; ".join(rep["errors"][:20]),
                              duration_seconds=round(time.time() - start, 2))
        if op not in ("compile", "render"):
            return ToolResult(success=False, error=f"Unknown operation: {op!r}")
        out = Path(inputs.get("output_dir") or f"paper_scene_{scene.get('id', 'scene')}")
        res = compile_scene(scene, out)
        rep = res["report"]
        if rep["status"] != "pass":
            return ToolResult(success=False, data={"report": rep}, artifacts=res["written"],
                              error="; ".join(rep["errors"][:20]))
        data: dict[str, Any] = {"report": rep, "output_dir": str(out), "layouts": res["layouts"],
                                "sfx_events": str(out / "sfx_events.json"), "story_beats": str(out / "story_beats.json")}
        artifacts = list(res["written"])
        if op == "render":
            r = self._render(out, res["layouts"], inputs, data, artifacts)
            if r is not None:
                return r
        return ToolResult(success=True, data=data, artifacts=artifacts, duration_seconds=round(time.time() - start, 2))

    def _render(self, out: Path, layouts: list[str], inputs: dict[str, Any], data: dict[str, Any],
                artifacts: list[str]) -> ToolResult | None:
        npx = shutil.which("npx")
        if not npx:
            return ToolResult(success=False, data=data, error="render needs npx (Node) for HyperFrames")
        spec = os.environ.get(HF_SPEC_ENV, "hyperframes")
        fps = int(inputs.get("fps", 30))
        wanted = inputs.get("layouts") or layouts
        env = dict(os.environ, HYPERFRAMES_NO_TELEMETRY="1")
        videos = {}
        for name in wanted:
            if name not in layouts:
                return ToolResult(success=False, data=data, error=f"unknown layout {name!r} (known: {layouts})")
            mp4 = out / f"{name}.mp4"
            proc = subprocess.run([npx, "--yes", spec, "render", str(out / name), "--output", str(mp4), "--fps",
                                   str(fps), "--quiet"], env=env, capture_output=True, text=True, timeout=3600)
            if proc.returncode != 0 or not mp4.is_file():
                return ToolResult(success=False, data=data, error=f"hyperframes render of {name} failed: "
                                  f"{(proc.stderr or proc.stdout or '')[-1500:]}")
            videos[name] = str(mp4)
            artifacts.append(str(mp4))
        data["videos"] = videos
        data["hyperframes"] = spec
        if not inputs.get("with_sound", True):
            return None
        from tools.audio.sfx_synth import SfxSynth

        sfx = json.loads((out / "sfx_events.json").read_text(encoding="utf-8"))
        wav = out / "sound.wav"
        snd = SfxSynth().execute({**sfx, "output_path": str(wav)})
        if not snd.success:
            return ToolResult(success=False, data=data, error=f"sfx_synth failed: {snd.error}")
        data["sound"] = {k: snd.data[k] for k in ("status", "issues", "measurements", "checks")}
        artifacts.append(str(wav))
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            data["sound"]["mux"] = "not_checked: ffmpeg not found"
            return None
        muxed = {}
        for name, mp4 in videos.items():
            dest = out / f"{name}_sound.mp4"
            proc = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", mp4, "-i", str(wav), "-map", "0:v:0", "-map",
                                   "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(dest)],
                                  capture_output=True, text=True, timeout=600)
            if proc.returncode != 0:
                return ToolResult(success=False, data=data, error=f"mux of {name} failed: {proc.stderr[-800:]}")
            muxed[name] = str(dest)
            artifacts.append(str(dest))
        data["videos_with_sound"] = muxed
        return None
