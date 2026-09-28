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
    # the 4 s fade-out that swallowed a channel's last sentence: the last word
    # ends with the fade (measured 25 dB under the mix here)
    v = _video(tmp_path / "fade.mp4", volume_db=12, fade_last_s=4.0)
    r = rc.check_last_sentence(v, 4.0, 6.0, 6.2, last_word=(5.7, 6.0))
    assert any("faded out" in i for i in r["issues"]), r


def test_last_sentence_past_the_end_is_flagged_as_cut(tmp_path):
    v = _video(tmp_path / "cut.mp4")
    r = rc.check_last_sentence(v, 5.0, 6.5, 6.0)
    assert any("it is cut" in i for i in r["issues"]), r


def test_a_short_outro_fade_after_the_voice_is_tolerated(tmp_path):
    v = _video(tmp_path / "outro.mp4", volume_db=12, fade_last_s=1.0)
    assert rc.check_last_sentence(v, 4.0, 5.8, 6.0)["issues"] == []


def test_a_naturally_falling_last_word_is_not_a_fade(tmp_path):
    # healthy French last words measured 12.5 dB under the mix at worst
    v = _video(tmp_path / "fall.mp4", volume_db=12)
    whole = rc.window_volume(v, 0, 6.0)
    assert rc.settings().last_sentence_max_drop_db > 12.5
    r = rc.check_last_sentence(v, 4.0, 5.8, 6.0, last_word=(5.55, 5.8))
    assert not any("Last word" in i for i in r["issues"]), (whole, r)


def test_healthy_ending_is_silent(tmp_path):
    v = _video(tmp_path / "ok.mp4", volume_db=12)
    r = rc.check_last_sentence(v, 4.0, 5.8, 6.0)
    assert r["issues"] == [] and "level" not in r["not_checked"]



def _video_with_bed(path: Path, voice_until: float, seconds: float = 6.0) -> Path:
    """Gated tone ("voice") until voice_until, then a steady noise bed to the end."""
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=160x90:d={seconds}",
        "-f", "lavfi", "-i",
        f"sine=f=220:d={seconds},volume='if(lt(t,{voice_until}),if(lt(mod(t,0.5),0.25),4,0.8),0)':eval=frame[v];"
        f"anoisesrc=d={seconds}:a=0.25[n];[v][n]amix=inputs=2:normalize=0",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], check=True)
    return path


def test_voice_cut_under_a_music_bed_is_caught_by_listening(tmp_path):
    # level stays normal (the bed fills the gap): only transcription can tell
    v = _video_with_bed(tmp_path / "cut.mp4", voice_until=4.2)
    heard_nothing = lambda wav: [{"word": " musique", "start": 0.1, "end": 0.4}]
    r = rc.check_last_sentence(v, 4.0, 5.8, 6.0, expected_text="et voilà la fin de la phrase.",
                               transcribe=heard_nothing)
    assert any("not heard" in i for i in r["issues"]), r


def test_last_sentence_heard_passes(tmp_path):
    v = _video_with_bed(tmp_path / "ok.mp4", voice_until=6.0)
    said = "et voilà la fin de la phrase".split()
    heard = lambda wav: [{"word": f" {w}", "start": i * 0.2, "end": i * 0.2 + 0.15} for i, w in enumerate(said)]
    r = rc.check_last_sentence(v, 4.0, 5.8, 6.0, expected_text="Et voilà la fin de la phrase.",
                               transcribe=heard)
    assert r["issues"] == [] and r["heard_fidelity"] == 1.0


def test_a_short_fade_swallowing_the_last_word_is_caught_on_that_word(tmp_path):
    # the fade ends before the last word does: the word is swallowed (real case: 35.7 dB)
    v = _video(tmp_path / "fade13.mp4", volume_db=12, fade_last_s=1.3, seconds=6.0)
    r = rc.check_last_sentence(v, 4.0, 6.0, 6.2, last_word=(5.75, 6.0))
    assert any("Last word" in i for i in r["issues"]), r


def test_a_transcriber_that_fails_is_not_checked_not_passed(tmp_path):
    v = _video(tmp_path / "ok2.mp4", volume_db=12)
    def boom(wav):
        raise OSError("no model")
    r = rc.check_last_sentence(v, 4.0, 5.8, 6.0, expected_text="la fin.", transcribe=boom)
    assert "no model" in r["not_checked"]["heard"]


def test_silence_under_the_gate_is_not_a_loudness_measure(tmp_path):
    v = _video(tmp_path / "silent.mp4", volume_db=-200)
    assert rc.loudness(v)["integrated_lufs"] is None
