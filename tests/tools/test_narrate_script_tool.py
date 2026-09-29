from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.audio.narrate_script import NarrateScript  # noqa: E402

SCRIPT = {"sections": [{"id": "s0", "text": "Bonjour à tous. Voici la suite."}]}


def test_refused_when_the_text_changed_since_approval(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    approved = {"sections": [{"id": "s0", "text": "Bonjour à tous."}]}
    (proj / "checkpoint_script.json").write_text(json.dumps({
        "status": "completed", "human_approved": True, "artifacts": {"script": approved}}))
    r = NarrateScript().execute({"script": SCRIPT, "output_dir": str(tmp_path / "o"),
                                 "project_dir": str(proj), "tts_tool": "piper_tts", "verify": False})
    assert not r.success and "differs from the approved text" in r.error


def test_unknown_tts_tool_is_an_error(tmp_path):
    r = NarrateScript().execute({"script": SCRIPT, "output_dir": str(tmp_path), "tts_tool": "nope"})
    assert not r.success and "not found" in r.error


def test_registry_lists_the_tool():
    from tools.tool_registry import ToolRegistry
    reg = ToolRegistry()
    reg.discover()
    assert reg.get("narrate_script") is not None


def test_a_transcriber_that_cannot_run_is_a_clear_error(tmp_path, monkeypatch):
    import tools.analysis.transcriber as tr
    from tools.base_tool import ToolResult

    class Boom:
        def execute(self, inputs):
            raise OSError("model not in the offline cache")

    class Voice:
        name = "fake_tts"

        def execute(self, inputs):
            import math, struct, wave
            with wave.open(inputs["output_path"], "w") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(b"".join(struct.pack("<h", int(2000 * math.sin(i / 7))) for i in range(3200)))
            return ToolResult(success=True)

    from tools.tool_registry import registry
    monkeypatch.setattr(tr, "Transcriber", Boom)
    monkeypatch.setattr(registry, "get", lambda name: Voice())
    r = NarrateScript().execute({"script": SCRIPT, "output_dir": str(tmp_path), "tts_tool": "fake_tts",
                                 "require_approval": False})
    assert not r.success and "offline cache" in r.error
