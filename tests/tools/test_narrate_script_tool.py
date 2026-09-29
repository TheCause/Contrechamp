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


# ---- Default engine, announced fallback, default voice ----

import pytest  # noqa: E402

from tools.base_tool import ToolResult, ToolStatus  # noqa: E402


class _FakeTTS:
    def __init__(self, name, available=True):
        self.name = name
        self.available = available
        self.calls: list[dict] = []

    def get_status(self):
        return ToolStatus.AVAILABLE if self.available else ToolStatus.UNAVAILABLE

    def execute(self, inputs):
        import math, struct, wave
        self.calls.append(inputs)
        with wave.open(inputs["output_path"], "w") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"".join(struct.pack("<h", int(2000 * math.sin(i / 7))) for i in range(3200)))
        return ToolResult(success=True)


@pytest.fixture
def engines(monkeypatch):
    from tools.tool_registry import registry
    fakes = {"voxcpm2_tts": _FakeTTS("voxcpm2_tts"), "piper_tts": _FakeTTS("piper_tts")}
    monkeypatch.setattr(registry, "get", lambda name: fakes.get(name))
    for var in ("CONTRECHAMP_NARRATION_VOICE", "OPENMONTAGE_NARRATION_VOICE"):
        monkeypatch.delenv(var, raising=False)
    return fakes


def _narrate(tmp_path, **extra):
    return NarrateScript().execute({"script": SCRIPT, "output_dir": str(tmp_path), "verify": False,
                                    "require_approval": False, **extra})


def test_voxcpm2_is_the_default_engine(tmp_path, engines):
    r = _narrate(tmp_path)
    assert r.success and r.data["tts_tool"] == "voxcpm2_tts" and "fallback" not in r.data


def test_missing_voxcpm2_falls_back_to_piper_and_says_so(tmp_path, engines):
    engines["voxcpm2_tts"].available = False
    r = _narrate(tmp_path)
    assert r.success and r.data["tts_tool"] == "piper_tts"
    assert r.data["fallback"]["wanted"] == "voxcpm2_tts" and r.data["fallback"]["used"] == "piper_tts"
    assert "setup-voxcpm2" in r.data["fallback"]["reason"]


def test_no_engine_at_all_is_a_clear_error(tmp_path, engines):
    engines["voxcpm2_tts"].available = False
    engines["piper_tts"].available = False
    r = _narrate(tmp_path)
    assert not r.success and "voxcpm2_tts" in r.error and "piper_tts" in r.error and "setup-voxcpm2" in r.error


def test_an_explicit_engine_is_never_swapped(tmp_path, engines):
    engines["voxcpm2_tts"].available = False
    r = _narrate(tmp_path, tts_tool="voxcpm2_tts")
    assert r.data.get("tts_tool") != "piper_tts" and not engines["piper_tts"].calls


def test_the_machine_default_voice_is_used_when_none_is_given(tmp_path, engines, monkeypatch):
    monkeypatch.setenv("CONTRECHAMP_NARRATION_VOICE", "chaine")
    r = _narrate(tmp_path)
    assert r.success and all(c["voice"] == "chaine" for c in engines["voxcpm2_tts"].calls)
    assert r.data["voice"] == {"name": "chaine", "source": "CONTRECHAMP_NARRATION_VOICE"}


def test_an_explicit_voice_wins_over_the_machine_default(tmp_path, engines, monkeypatch):
    monkeypatch.setenv("CONTRECHAMP_NARRATION_VOICE", "chaine")
    _narrate(tmp_path, tts_inputs={"voice": "autre"})
    assert all(c["voice"] == "autre" for c in engines["voxcpm2_tts"].calls)


def test_the_default_voice_is_not_sent_to_piper(tmp_path, engines, monkeypatch):
    monkeypatch.setenv("CONTRECHAMP_NARRATION_VOICE", "chaine")
    engines["voxcpm2_tts"].available = False
    _narrate(tmp_path)
    assert all("voice" not in c for c in engines["piper_tts"].calls)
