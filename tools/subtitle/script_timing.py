"""Script timing tool: date the validated script on its narration.

The screen text comes from the script, the times from the transcriber's word
timestamps. See ``lib/script_timing.py`` for the alignment rules. The tool
writes one JSON file (words, sentences, sections, Remotion captions, report)
and never reports ``pass`` for a script passage the voice did not say.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from lib import script_timing
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolStability,
    ToolTier,
)


class ScriptTiming(BaseTool):
    name = "script_timing"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "subtitle"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = []  # pure Python
    install_instructions = "No external dependencies required."
    agent_skills = []

    capabilities = ["align_script_on_narration", "generate_caption_json", "date_sentences"]
    best_for = [
        "captions whose words come from the validated script, not from the transcription",
        "sentence and section times for cards, chapters and cuts",
    ]

    input_schema = {
        "type": "object",
        "properties": {
            "script": {"type": "object", "description": "Script artifact (sections[].text)."},
            "text": {"type": "string", "description": "Plain script text, if no artifact."},
            "word_timestamps": {
                "type": "array",
                "description": "Transcriber word_timestamps ([{word, start, end}]).",
            },
            "transcription_path": {
                "type": "string",
                "description": "JSON written by the transcriber (word_timestamps or segments[].words).",
            },
            "output_path": {"type": "string"},
            "min_timing_coverage": {"type": "number", "default": 0.9},
            "max_gap_words": {"type": "integer", "default": 4},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=10)
    idempotency_key_fields = ["script", "text", "word_timestamps", "transcription_path"]
    side_effects = ["writes the timing JSON to output_path"]
    user_visible_verification = [
        "Read report.gaps: each gap is script text the voice did not say",
    ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        script = inputs.get("script") or inputs.get("text")
        if not script:
            return ToolResult(success=False, error="Provide 'script' (artifact) or 'text'.")
        words = inputs.get("word_timestamps")
        if words is None and inputs.get("transcription_path"):
            data = json.loads(Path(inputs["transcription_path"]).read_text(encoding="utf-8"))
            words = data.get("word_timestamps") or [
                w for seg in data.get("segments", []) for w in seg.get("words", [])
            ]
        if words is None:
            return ToolResult(
                success=False, error="Provide 'word_timestamps' or 'transcription_path'.")

        timing = script_timing.time_script(
            script,
            words,
            min_timing_coverage=float(inputs.get("min_timing_coverage", 0.9)),
            max_gap_words=int(inputs.get("max_gap_words", 4)),
        )
        report = timing["report"]
        out = inputs.get("output_path")
        if out:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(json.dumps(timing, ensure_ascii=False, indent=2), encoding="utf-8")

        result = ToolResult(
            success=report["status"] != "fail",
            data={**timing, "output_path": out, "status": report["status"]},
            error=report.get("reason") if report["status"] == "fail" else None,
            artifacts=[out] if out else [],
        )
        result.duration_seconds = round(time.time() - start, 2)
        return result
