"""Narrate an approved script chunk by chunk with any TTS provider, and check it.

Wraps ``lib/narration.py``: the voice speaks only the text approved at the
script stage, in ~30 s chunks with stable ids (one chunk can be regenerated
alone), and each chunk is transcribed and compared with its text. See the
module docstring for the rules.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from lib import narration
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolStability,
    ToolTier,
)


class NarrateScript(BaseTool):
    name = "narrate_script"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "narration"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED

    dependencies = ["cmd:ffmpeg"]
    install_instructions = "Needs ffmpeg and one TTS provider (e.g. piper_tts, voxcpm2_tts)."
    agent_skills = ["text-to-speech"]
    capabilities = ["narrate_approved_script", "regenerate_one_chunk", "check_what_was_said"]
    best_for = [
        "long narration that must match the approved script word for word",
        "regenerating one chunk without renumbering the others",
    ]

    input_schema = {
        "type": "object",
        "required": ["script", "output_dir"],
        "properties": {
            "script": {"type": "object", "description": "Script artifact (sections[].text)."},
            "output_dir": {"type": "string"},
            "project_dir": {
                "type": "string",
                "description": "Project folder holding checkpoint_script.json; the text must match the approved one.",
            },
            "require_approval": {"type": "boolean", "default": True},
            "tts_tool": {"type": "string", "default": "voxcpm2_tts"},
            "tts_inputs": {"type": "object", "description": "Extra inputs for the TTS tool (voice, seed...)."},
            "only": {"type": "array", "items": {"type": "string"}, "description": "Chunk ids to regenerate."},
            "replan": {"type": "boolean", "default": False},
            "verify": {"type": "boolean", "default": True},
            "language": {"type": "string", "default": "fr"},
            "model_size": {"type": "string", "default": "medium"},
            "min_fidelity": {"type": "number", "default": 0.8},
        },
    }
    resource_profile = ResourceProfile(cpu_cores=4, ram_mb=4096, vram_mb=0, disk_mb=500)
    idempotency_key_fields = ["script", "tts_tool", "tts_inputs", "only"]
    side_effects = ["writes chunk audio, narration_plan.json and narration.wav to output_dir"]
    user_visible_verification = [
        "Listen to chunks flagged drift_risk or suspect, alone",
        "Listen to narration.wav in full before the cut",
    ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        from tools.tool_registry import registry

        start = time.time()
        registry.discover()
        tts = registry.get(inputs.get("tts_tool", "voxcpm2_tts"))
        if tts is None:
            return ToolResult(success=False, error=f"TTS tool {inputs.get('tts_tool')!r} not found")
        tts_inputs = dict(inputs.get("tts_inputs") or {})

        def synthesize(text: str, path: Path) -> None:
            r = tts.execute({**tts_inputs, "text": text, "output_path": str(path)})
            if not r.success:
                raise RuntimeError(f"{tts.name} failed on a chunk: {r.error}")

        transcribe = None
        if inputs.get("verify", True):
            from tools.analysis.transcriber import Transcriber

            def transcribe(path: Path) -> list[dict]:
                r = Transcriber().execute({
                    "input_path": str(path),
                    "language": inputs.get("language", "fr"),
                    "model_size": inputs.get("model_size", "medium"),
                })
                if not r.success:
                    raise RuntimeError(f"transcriber failed on {path.name}: {r.error}")
                return r.data.get("word_timestamps", [])

        try:
            report = narration.narrate(
                inputs["script"],
                Path(inputs["output_dir"]),
                synthesize,
                transcribe,
                project_dir=Path(inputs["project_dir"]) if inputs.get("project_dir") else None,
                require_approval=inputs.get("require_approval", True),
                only=inputs.get("only"),
                replan=inputs.get("replan", False),
                min_fidelity=float(inputs.get("min_fidelity", 0.8)),
            )
        except (ValueError, RuntimeError) as exc:
            return ToolResult(success=False, error=str(exc))

        ok = report["status"] != "refused"
        result = ToolResult(
            success=ok,
            data={**report, "tts_tool": tts.name},
            error=None if ok else f"Narration refused: {report['approval'].get('reason')}",
            artifacts=[report["narration"]] if ok else [],
        )
        result.duration_seconds = round(time.time() - start, 2)
        return result
