"""Narrate an approved script chunk by chunk with any TTS provider, and check it.

Wraps ``lib/narration.py``: the voice speaks only the text approved at the
script stage, in ~30 s chunks with stable ids (one chunk can be regenerated
alone), and each chunk is transcribed and compared with its text. See the
module docstring for the rules.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from lib import env_names, narration
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolStability,
    ToolStatus,
    ToolTier,
)

logger = logging.getLogger(__name__)


class NarrateScript(BaseTool):
    name = "narrate_script"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "narration"
    provider = "contrechamp"
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
            "tts_tool": {
                "type": "string",
                "default": "voxcpm2_tts",
                "description": "Left out: voxcpm2_tts, or piper_tts when VoxCPM2 is not installed "
                "(reported under data.fallback). Given: used as is, never swapped.",
            },
            "tts_inputs": {
                "type": "object",
                "description": "Extra inputs for the TTS tool (voice, seed...). With voxcpm2_tts and no "
                "voice, CONTRECHAMP_NARRATION_VOICE names the machine's default voice.",
            },
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
        fallback = None
        if inputs.get("tts_tool"):
            tts = registry.get(inputs["tts_tool"])
            if tts is None:
                return ToolResult(success=False, error=f"TTS tool {inputs.get('tts_tool')!r} not found")
        else:
            tts, fallback = _default_engine(registry)
            if tts is None:
                return ToolResult(success=False, error=fallback)
        tts_inputs = dict(inputs.get("tts_inputs") or {})
        voice = None
        if tts.name == "voxcpm2_tts" and not tts_inputs.get("voice"):
            default_voice = env_names.get("NARRATION_VOICE")
            if default_voice:
                tts_inputs["voice"] = default_voice.strip()
                voice = {"name": tts_inputs["voice"], "source": "CONTRECHAMP_NARRATION_VOICE"}

        def synthesize(text: str, path: Path, attempt: int = 0) -> None:
            call = {**tts_inputs, "text": text, "output_path": str(path)}
            if attempt:
                # a regeneration is a new take: the same seed gave the same file
                call["seed"] = int(tts_inputs.get("seed", 42)) + attempt
            r = tts.execute(call)
            if not r.success:
                raise RuntimeError(f"{tts.name} failed on a chunk: {r.error}")

        transcribe = None
        if inputs.get("verify", True):
            from tools.analysis.transcriber import Transcriber

            def transcribe(path: Path) -> list[dict]:
                try:
                    r = Transcriber().execute({
                        "input_path": str(path),
                        "language": inputs.get("language", "fr"),
                        "model_size": inputs.get("model_size", "medium"),
                    })
                except Exception as exc:  # e.g. model not in the offline cache
                    raise RuntimeError(
                        f"transcriber could not run on {path.name} "
                        f"(model {inputs.get('model_size', 'medium')!r}): {type(exc).__name__}: {exc}"
                    ) from exc
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
                voice_key=json.dumps({"tool": tts.name, **tts_inputs}, sort_keys=True, default=str),
                only=inputs.get("only"),
                replan=inputs.get("replan", False),
                min_fidelity=float(inputs.get("min_fidelity", 0.8)),
            )
        except (ValueError, RuntimeError) as exc:
            return ToolResult(success=False, error=str(exc))

        ok = report["status"] != "refused"
        result = ToolResult(
            success=ok,
            data={
                **report,
                "tts_tool": tts.name,
                **({"fallback": fallback} if fallback else {}),
                **({"voice": voice} if voice else {}),
            },
            error=None if ok else f"Narration refused: {report['approval'].get('reason')}",
            artifacts=[report["narration"]] if ok else [],
        )
        result.duration_seconds = round(time.time() - start, 2)
        return result


def _default_engine(registry) -> tuple[Any, Any]:
    """VoxCPM2 when installed, else Piper with the swap reported; (None, error) if neither."""
    wanted = registry.get("voxcpm2_tts")
    if wanted is not None and wanted.get_status() == ToolStatus.AVAILABLE:
        return wanted, None
    piper = registry.get("piper_tts")
    if piper is not None and piper.get_status() == ToolStatus.AVAILABLE:
        reason = "voxcpm2_tts is not installed (make setup-voxcpm2); narrated with Piper instead"
        logger.warning("narrate_script: %s", reason)
        return piper, {"wanted": "voxcpm2_tts", "used": "piper_tts", "reason": reason}
    return None, (
        "No narration engine available: voxcpm2_tts is not installed (make setup-voxcpm2) "
        "and piper_tts is not available either (see its install_instructions)."
    )

