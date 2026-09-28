"""Loudness and last-sentence checks, measured on real files made by ffmpeg.

Lessons: a mixer flattened a finished narration to about -30 LUFS; a fade-out
swallowed the last 4 seconds of voice while the duration looked right.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib import render_checks as rc  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


def _video(path: Path, *, volume_db: float = 0.0, fade_last_s: float = 0.0, seconds: float = 6.0) -> Path:
    # speech-like signal: a tone gated on/off every 0.25 s
    af = f"volume={volume_db}dB"
    if fade_last_s:
        af += f",afade=t=out:st={seconds - fade_last_s}:d={fade_last_s}"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=160x90:d={seconds}",
        "-f", "lavfi", "-i", f"sine=f=220:d={seconds},volume='if(lt(mod(t,0.5),0.25),1,0.2)':eval=frame",
        "-af", af, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], check=True)
    return path


def test_quiet_mix_is_measured_below_the_floor(tmp_path):
    loud = rc.loudness(_video(tmp_path / "loud.mp4", volume_db=12))
    quiet = rc.loudness(_video(tmp_path / "quiet.mp4", volume_db=-13))
    assert loud["integrated_lufs"] > rc.settings().loudness_min_lufs
    assert quiet["integrated_lufs"] < rc.settings().loudness_min_lufs
    assert quiet["integrated_lufs"] < loud["integrated_lufs"] - 20


def test_faded_last_sentence_is_flagged(tmp_path):
    v = _video(tmp_path / "fade.mp4", volume_db=12, fade_last_s=4.0)
    r = rc.check_last_sentence(v, 4.0, 5.8, 6.0)
    assert any("faded out" in i for i in r["issues"]), r


def test_last_sentence_past_the_end_is_flagged_as_cut(tmp_path):
    v = _video(tmp_path / "cut.mp4")
    r = rc.check_last_sentence(v, 5.0, 6.5, 6.0)
    assert any("it is cut" in i for i in r["issues"]), r


def test_a_short_outro_fade_after_the_voice_is_tolerated(tmp_path):
    v = _video(tmp_path / "outro.mp4", volume_db=12, fade_last_s=1.0)
    assert rc.check_last_sentence(v, 4.0, 5.8, 6.0)["issues"] == []


def test_healthy_ending_is_silent(tmp_path):
    v = _video(tmp_path / "ok.mp4", volume_db=12)
    r = rc.check_last_sentence(v, 4.0, 5.8, 6.0)
    assert r["issues"] == [] and "not_checked" not in r
