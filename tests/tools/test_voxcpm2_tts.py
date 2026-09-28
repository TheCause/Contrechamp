"""Focused tests for the VoxCPM2 MLX local TTS tool.

No live inference, no network: mlx_audio is only imported inside the
generation subprocess. Covers the tool contract, registry discovery,
model-dir resolution (including env override and incomplete downloads),
and execute() guardrails.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.base_tool import BaseTool, ToolRuntime, ToolStatus
from tools.tool_registry import ToolRegistry
from tools.audio import voxcpm2_tts as voxcpm2_module
from tools.audio.voxcpm2_tts import VoxCPM2TTS


def _make_model_dir(root: Path, complete: bool = True) -> Path:
    d = root / "VoxCPM2-4bit"
    d.mkdir(parents=True)
    (d / "config.json").write_text("{}")
    (d / "model.safetensors").write_bytes(b"\x00" * 16)
    if not complete:
        (d / "model.safetensors.aria2").write_bytes(b"")
    return d


@pytest.fixture(autouse=True)
def _no_env_model_dir(monkeypatch):
    monkeypatch.delenv("VOXCPM2_MODEL_DIR", raising=False)


@pytest.fixture
def fake_model(tmp_path, monkeypatch):
    model_dir = _make_model_dir(tmp_path)
    monkeypatch.setattr(voxcpm2_module, "_MODEL_DIRS", [model_dir])
    return model_dir


# ---- Contract ----

def test_contract_fields():
    tool = VoxCPM2TTS()
    assert isinstance(tool, BaseTool)
    assert tool.capability == "tts"
    assert tool.provider == "voxcpm"
    assert tool.runtime is ToolRuntime.LOCAL
    assert tool.input_schema["required"] == ["text"]
    assert "pip:mlx-audio" in tool.dependencies


def test_registry_discovery():
    registry = ToolRegistry()
    registry.discover()
    assert registry.get("voxcpm2_tts") is not None
    tts_tools = [t.name for t in registry.get_by_capability("tts")]
    assert "voxcpm2_tts" in tts_tools


# ---- Status / model resolution ----

def test_status_unavailable_without_model(tmp_path, monkeypatch):
    monkeypatch.setattr(voxcpm2_module, "_MODEL_DIRS", [tmp_path / "nowhere"])
    assert VoxCPM2TTS().get_status() is ToolStatus.UNAVAILABLE


def test_status_available_with_complete_model(fake_model):
    assert VoxCPM2TTS().get_status() is ToolStatus.AVAILABLE


def test_incomplete_download_is_unavailable(tmp_path, monkeypatch):
    model_dir = _make_model_dir(tmp_path, complete=False)
    monkeypatch.setattr(voxcpm2_module, "_MODEL_DIRS", [model_dir])
    assert VoxCPM2TTS().get_status() is ToolStatus.UNAVAILABLE


def test_env_override_resolves_model(fake_model, monkeypatch):
    monkeypatch.setattr(voxcpm2_module, "_MODEL_DIRS", [])
    monkeypatch.setenv("VOXCPM2_MODEL_DIR", str(fake_model))
    assert VoxCPM2TTS().get_status() is ToolStatus.AVAILABLE


def test_sharded_model_needs_index(tmp_path, monkeypatch):
    d = tmp_path / "VoxCPM2-bf16"
    d.mkdir()
    (d / "config.json").write_text("{}")
    (d / "model-00001-of-00002.safetensors").write_bytes(b"\x00" * 16)
    monkeypatch.setattr(voxcpm2_module, "_MODEL_DIRS", [d])
    # No model.safetensors.index.json -> treated as incomplete
    assert VoxCPM2TTS().get_status() is ToolStatus.UNAVAILABLE


# ---- Guardrails ----

def test_execute_refuses_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(voxcpm2_module, "_MODEL_DIRS", [tmp_path / "nowhere"])
    result = VoxCPM2TTS().execute({"text": "merhaba"})
    assert not result.success
    assert "not found locally" in result.error


def test_estimate_cost_is_zero():
    assert VoxCPM2TTS().estimate_cost({"text": "x"}) == 0.0


# ---- Official engine + named voices (fork) ----

import json as _json
import os as _os
import subprocess as _subprocess


@pytest.fixture
def official_python(tmp_path, monkeypatch):
    exe = tmp_path / "python"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(voxcpm2_module, "_MODEL_DIRS", [tmp_path / "no-mlx-model"])
    monkeypatch.setenv("VOXCPM2_PYTHON", str(exe))
    return exe


@pytest.fixture
def voices(tmp_path):
    d = tmp_path / "voices"
    d.mkdir()
    (d / "chaine.wav").write_bytes(b"RIFF")
    (d / "chaine.txt").write_text("Texte de la prise.\n", encoding="utf-8")
    (d / "chaine.json").write_text(_json.dumps({"cfg_value": 1.5, "mode": "ultimate", "seed": 7}))
    return d


def _capture_run(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["script"] = Path(cmd[1]).read_text()
        seen["payload"] = _json.loads(kwargs["env"]["VOXCPM_INPUTS"])
        out = Path(seen["payload"]["work_dir"]) / "voxcpm.wav"
        out.write_bytes(b"RIFF")
        return _subprocess.CompletedProcess(cmd, 0, stdout=f"VOXCPM_OUTPUT={out}\n", stderr="")

    monkeypatch.setattr(voxcpm2_module.subprocess, "run", fake_run)
    return seen


def test_official_engine_makes_the_tool_available_without_mlx_model(official_python):
    tool = VoxCPM2TTS()
    assert tool.get_status() is ToolStatus.AVAILABLE
    assert tool._backend() == "voxcpm"


def test_no_engine_at_all_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(voxcpm2_module, "_MODEL_DIRS", [tmp_path / "nowhere"])
    monkeypatch.delenv("VOXCPM2_PYTHON", raising=False)
    assert VoxCPM2TTS().get_status() is ToolStatus.UNAVAILABLE


def test_named_voice_runs_the_official_engine_with_its_reference(official_python, voices, tmp_path, monkeypatch):
    seen = _capture_run(monkeypatch)
    out = tmp_path / "out.wav"
    r = VoxCPM2TTS().execute({"text": "Bonjour.", "voice": "chaine",
                              "voices_dir": str(voices), "output_path": str(out)})
    assert r.success, r.error
    assert seen["cmd"][0] == str(official_python)
    assert "from voxcpm import VoxCPM" in seen["script"]
    p = seen["payload"]
    assert p["ref_audio"] == str((voices / "chaine.wav").resolve())
    assert p["ref_text"] == "Texte de la prise."
    assert (p["mode"], p["cfg_value"], p["seed"], p["inference_timesteps"]) == ("ultimate", 1.5, 7, 30)
    assert r.data["backend"] == "voxcpm" and r.data["voice"] == "chaine" and out.exists()


def test_explicit_inputs_win_over_the_voice_defaults(official_python, voices, tmp_path, monkeypatch):
    seen = _capture_run(monkeypatch)
    VoxCPM2TTS().execute({"text": "Bonjour.", "voice": "chaine", "voices_dir": str(voices),
                          "cfg_value": 2.0, "output_path": str(tmp_path / "o.wav")})
    assert seen["payload"]["cfg_value"] == 2.0


def test_unknown_voice_names_the_known_ones(official_python, voices):
    r = VoxCPM2TTS().execute({"text": "x", "voice": "absente", "voices_dir": str(voices)})
    assert not r.success and "chaine" in r.error


def test_requested_engine_that_is_missing_is_refused(official_python):
    r = VoxCPM2TTS().execute({"text": "x", "backend": "mlx"})
    assert not r.success and "'mlx'" in r.error


def test_ultimate_voice_never_runs_silently_on_mlx(fake_model, official_python, monkeypatch):
    # official_python emptied _MODEL_DIRS: put the MLX model back, both engines present
    monkeypatch.setattr(voxcpm2_module, "_MODEL_DIRS", [fake_model])
    tool = VoxCPM2TTS()
    assert tool._backend("auto", "reference") == "mlx"
    assert tool._backend("auto", "ultimate") == "voxcpm"
    assert tool._backend("mlx", "ultimate") is None


def test_invalid_voice_file_is_refused(official_python, voices):
    (voices / "chaine.json").write_text('{"mode": "ultimat", "cfg_value": "fort"}')
    r = VoxCPM2TTS().execute({"text": "x", "voice": "chaine", "voices_dir": str(voices)})
    assert not r.success and "invalid value" in r.error
    (voices / "chaine.json").write_text("null")
    r = VoxCPM2TTS().execute({"text": "x", "voice": "chaine", "voices_dir": str(voices)})
    assert not r.success and "JSON object" in r.error


def test_a_directory_is_not_an_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(voxcpm2_module, "_MODEL_DIRS", [tmp_path / "none"])
    monkeypatch.setenv("VOXCPM2_PYTHON", str(tmp_path))
    assert VoxCPM2TTS().get_status() is ToolStatus.UNAVAILABLE
