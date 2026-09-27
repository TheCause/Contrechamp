"""A tool is AVAILABLE only when it can actually do its job.

Seen on a real install: piper_tts reported AVAILABLE with no voice model
("Unable to find voice" at the first call), was reported UNAVAILABLE when the
binary sat in the active venv but not on PATH, and pixabay_music reported
AVAILABLE "no setup required" while pixabay.com answered HTTP 403."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.audio import piper_tts as piper_mod
from tools.audio.piper_tts import PiperTTS
from tools.audio.pixabay_music import PixabayMusic
from tools.base_tool import ToolStatus


def _fake_binary(dir_: Path) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    binary = dir_ / "piper"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return binary


def test_piper_without_any_voice_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(piper_mod.shutil, "which", lambda n: str(_fake_binary(tmp_path / "bin")))
    monkeypatch.setenv("PIPER_VOICES_DIR", str(tmp_path / "voices"))
    monkeypatch.setattr(piper_mod, "DEFAULT_VOICE_DIRS", [])
    tool = PiperTTS()
    assert tool.get_status() == ToolStatus.UNAVAILABLE
    assert "voice" in tool.status_reason().lower()


def test_piper_with_binary_and_a_voice_is_available(tmp_path, monkeypatch):
    monkeypatch.setattr(piper_mod.shutil, "which", lambda n: str(_fake_binary(tmp_path / "bin")))
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "fr_FR-siwis-medium.onnx").write_bytes(b"x")
    monkeypatch.setenv("PIPER_VOICES_DIR", str(voices))
    assert PiperTTS().get_status() == ToolStatus.AVAILABLE
    assert PiperTTS().installed_voices() == ["fr_FR-siwis-medium"]


def test_piper_found_in_active_venv_when_not_on_path(tmp_path, monkeypatch):
    venv_bin = tmp_path / "venv" / "bin"
    _fake_binary(venv_bin)
    monkeypatch.setattr(piper_mod.shutil, "which", lambda n: None)
    monkeypatch.setattr(piper_mod.sys, "executable", str(venv_bin / "python"))
    assert PiperTTS().piper_binary() == str(venv_bin / "piper")


def test_piper_call_points_at_the_voice_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(piper_mod.shutil, "which", lambda n: str(_fake_binary(tmp_path / "bin")))
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "fr_FR-siwis-medium.onnx").write_bytes(b"x")
    monkeypatch.setenv("PIPER_VOICES_DIR", str(voices))
    seen = {}

    def fake_run(cmd, *a, **k):
        seen["cmd"] = cmd
        Path(cmd[cmd.index("--output_file") + 1]).write_bytes(b"RIFF")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(piper_mod.subprocess, "run", fake_run)
    out = tmp_path / "o.wav"
    result = PiperTTS().execute({"text": "Bonjour", "model": "fr_FR-siwis-medium",
                                 "output_path": str(out)})
    assert result.success, result.error
    assert seen["cmd"][seen["cmd"].index("--data-dir") + 1] == str(voices)


def test_pixabay_music_is_not_claimed_available_without_proof():
    tool = PixabayMusic()
    assert tool.get_status() != ToolStatus.AVAILABLE
    assert "403" in tool.status_reason() or "unverified" in tool.status_reason().lower()
