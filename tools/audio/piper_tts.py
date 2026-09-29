"""Piper local text-to-speech provider tool."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

# Where voice models (<name>.onnx + <name>.onnx.json) are looked for, after
# $PIPER_VOICES_DIR. The first directory holding at least one voice wins.
DEFAULT_VOICE_DIRS = [
    Path(__file__).resolve().parents[2] / "models" / "piper",
    Path.home() / ".piper" / "models",
]
# Voice used when none is given, if installed (see PiperTTS._resolve_model).
DEFAULT_MODEL = "en_US-lessac-medium"


class PiperTTS(BaseTool):
    name = "piper_tts"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "piper"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:piper"]
    install_instructions = (
        "Install Piper TTS:\n"
        "  pip install piper-tts\n"
        "Or download from https://github.com/rhasspy/piper/releases\n"
        "Then download at least one voice (piper is unusable without one):\n"
        "  python -m piper.download_voices fr_FR-siwis-medium --data-dir models/piper\n"
        "Voices are looked up in $PIPER_VOICES_DIR, models/piper/, then ~/.piper/models."
    )
    agent_skills = ["text-to-speech"]

    capabilities = [
        "text_to_speech",
        "offline_generation",
    ]
    supports = {
        "voice_cloning": False,
        "multilingual": False,
        "offline": True,
        "native_audio": True,
    }
    best_for = [
        "offline narration fallback",
        "privacy-sensitive local-only workflows",
    ]
    not_good_for = [
        "best-in-class expressive voice quality",
        "voice clone matching",
    ]

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "model": {
                "type": "string",
                "default": "en_US-lessac-medium",
                "description": "An installed voice. Left out: en_US-lessac-medium if installed, else an "
                "installed French voice, else the first installed one (reported as model_substituted).",
            },
            "speaker_id": {
                "type": "integer",
                "default": 0,
            },
            "length_scale": {
                "type": "number",
                "default": 1.0,
            },
            "sentence_silence": {
                "type": "number",
                "default": 0.3,
            },
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=512, vram_mb=0, disk_mb=200, network_required=False
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=[])
    idempotency_key_fields = ["text", "model", "speaker_id", "length_scale"]
    side_effects = ["writes audio file to output_path"]
    user_visible_verification = ["Listen to generated audio for intelligibility"]

    def piper_binary(self) -> str | None:
        """piper on PATH, else next to the running interpreter (active venv)."""
        found = shutil.which("piper")
        if found:
            return found
        candidate = Path(sys.executable).parent / "piper"
        return str(candidate) if candidate.exists() else None

    def voices_dir(self) -> Path | None:
        dirs = []
        if os.environ.get("PIPER_VOICES_DIR"):
            dirs.append(Path(os.environ["PIPER_VOICES_DIR"]))
        dirs += DEFAULT_VOICE_DIRS
        for d in dirs:
            if d.is_dir() and any(d.glob("*.onnx")):
                return d
        return None

    def installed_voices(self) -> list[str]:
        d = self.voices_dir()
        return sorted(p.stem for p in d.glob("*.onnx")) if d else []

    def get_status(self) -> ToolStatus:
        if self.piper_binary() and self.installed_voices():
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def status_reason(self) -> str:
        if not self.piper_binary():
            return "piper binary not found (PATH or active venv)"
        if not self.installed_voices():
            return "no piper voice model installed (see install_instructions)"
        return ""

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        if self.get_status() != ToolStatus.AVAILABLE:
            return ToolResult(
                success=False,
                error=f"Piper TTS not available: {self.status_reason()}. " + self.install_instructions,
            )

        start = time.time()
        try:
            result = self._generate(inputs)
        except Exception as exc:
            return ToolResult(success=False, error=f"Local TTS generation failed: {exc}")

        result.duration_seconds = round(time.time() - start, 2)
        return result

    def _resolve_model(self, inputs: dict[str, Any]) -> tuple[str | None, dict[str, str] | None, str | None]:
        """(voice to run, substitution to report, error). The voice is always an installed one.

        Given explicitly: used if installed, refused by name otherwise. Left out:
        the default if installed, else an installed French voice, else the first one.
        """
        installed = self.installed_voices()
        wanted = inputs.get("model")
        if wanted:
            if wanted in installed:
                return wanted, None, None
            return None, None, f"Piper voice {wanted!r} is not installed (installed: {installed})"
        if DEFAULT_MODEL in installed:
            return DEFAULT_MODEL, None, None
        french = [v for v in installed if v.startswith("fr_")]
        used = (french or installed)[0]
        return used, {"wanted": DEFAULT_MODEL, "used": used}, None

    def _generate(self, inputs: dict[str, Any]) -> ToolResult:
        model, substituted, error = self._resolve_model(inputs)
        if error:
            return ToolResult(success=False, error=error)
        output_path = Path(inputs.get("output_path", "tts_output.wav"))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        proc = subprocess.run(
            [
                self.piper_binary() or "piper",
                "--data-dir", str(self.voices_dir()),
                "--model", model,
                "--speaker", str(inputs.get("speaker_id", 0)),
                "--length-scale", str(inputs.get("length_scale", 1.0)),
                "--sentence-silence", str(inputs.get("sentence_silence", 0.3)),
                "--output_file", str(output_path),
            ],
            input=inputs["text"],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if proc.returncode != 0:
            return ToolResult(success=False, error=f"Piper failed (exit {proc.returncode}): {proc.stderr}")
        if not output_path.exists():
            return ToolResult(success=False, error=f"Piper output file missing: {output_path}")

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": model,
                **({"model_substituted": substituted} if substituted else {}),
                "speaker_id": inputs.get("speaker_id", 0),
                "text_length": len(inputs["text"]),
                "output": str(output_path),
                "format": "wav",
            },
            artifacts=[str(output_path)],
            model=model,
        )
