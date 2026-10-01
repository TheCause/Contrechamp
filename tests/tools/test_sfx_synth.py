"""sfx_synth: checks that bite (lot 1 rule) on synthesized sound effects.

Each check has a test where it must fire on a real defect (a WAV missing an
event, a WAV peaking above -0.5 dBFS, a loudness meter that cannot run) and a
healthy case where nothing comes out.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.audio import sfx_synth  # noqa: E402
from tools.audio.sfx_synth import KIND_PARAMS, SfxSynth  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None

# One event of every kind, spaced so none masks another.
HEALTHY = [
    {"t": 0.20, "kind": "click", "gain": 0.6, "pan": 0.2},
    {"t": 0.70, "kind": "thud", "gain": 0.8, "pan": 0.5},
    {"t": 1.30, "kind": "metal", "gain": 0.3, "pan": 0.7, "dur": 0.5},
    {"t": 2.00, "kind": "bell", "gain": 0.3, "pan": 0.5, "freq": 659.25, "dur": 0.8},
    {"t": 3.00, "kind": "marimba", "gain": 0.5, "pan": 0.4, "dur": 0.5},
    {"t": 3.70, "kind": "motor", "gain": 0.2, "pan": 0.6, "dur": 0.8, "freq": 95.0, "freq_end": 130.0},
    {"t": 4.70, "kind": "swish", "gain": 0.4, "pan": 0.3, "dur": 0.5},
    {"t": 5.50, "kind": "sparkle", "gain": 0.3, "pan": 0.8, "dur": 0.5},
    {"t": 6.30, "kind": "pad", "gain": 0.05, "pan": 0.5, "dur": 1.2},
]
DURATION = 8.0


def _run(tmp_path: Path, name: str = "out.wav", **extra) -> dict:
    res = SfxSynth().execute({"events": HEALTHY, "duration_seconds": DURATION,
                              "output_path": str(tmp_path / name), **extra})
    assert res.success, res.error
    return res.data


def _peak_dbfs(path: Path) -> float:
    """Read the file independently of the tool."""
    with wave.open(str(path), "rb") as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (2, 48000, 2)
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return 20 * np.log10(np.max(np.abs(pcm.astype(float))) / 32767)


def test_healthy_list_covers_every_kind():
    assert {e["kind"] for e in HEALTHY} == set(KIND_PARAMS)


# ---------------------------------------------------------------- healthy case

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg missing: pass needs the loudness meter")
def test_healthy_render_reports_nothing(tmp_path):
    d = _run(tmp_path)
    assert d["status"] == "pass", d["issues"]
    assert d["issues"] == [] and d["not_checked"] == {}
    assert d["checks"] == {"peak_under_limit": True, "all_events_present": True, "loudness_measured": True}
    assert all(e["present"] and e["margin_db"] > 6 for e in d["events"]), d["events"]
    m = d["measurements"]
    assert isinstance(m["integrated_lufs"], float) and -60 < m["integrated_lufs"] < 0
    assert (d["sample_rate"], d["channels"], d["duration_seconds"]) == (48000, 2, DURATION)


# ---------------------------------------------------------------- missing event

def test_event_missing_from_the_wav_is_reported(tmp_path, monkeypatch):
    """Sabotaged mixer: the marimba never reaches the bus. The WAV is written
    without it, and the check must name that event and only that one."""
    real_place = sfx_synth._place

    def drop_marimba(dry, ev, sig):
        if ev["kind"] != "marimba":
            real_place(dry, ev, sig)

    monkeypatch.setattr(sfx_synth, "_place", drop_marimba)
    d = _run(tmp_path)
    assert d["checks"]["all_events_present"] is False
    assert d["status"] == "revise"
    missing = [e for e in d["events"] if not e["present"]]
    assert [e["kind"] for e in missing] == ["marimba"]
    assert missing[0]["margin_db"] < 0  # the file holds less than the background-plus-event
    assert any("marimba" in i for i in d["issues"])


def test_event_moved_away_from_its_time_is_reported(tmp_path, monkeypatch):
    """A mixer that places the bell 0.3 s late: the bell is in the file, but not
    in its window (±1 frame at 30 fps)."""
    real_place = sfx_synth._place

    def late_bell(dry, ev, sig):
        real_place(dry, {**ev, "t": ev["t"] + 0.3} if ev["kind"] == "bell" else ev, sig)

    monkeypatch.setattr(sfx_synth, "_place", late_bell)
    d = _run(tmp_path)
    assert [e["kind"] for e in d["events"] if not e["present"]] == ["bell"]


# ---------------------------------------------------------------- peak

def test_peak_stays_under_minus_half_dbfs(tmp_path):
    d = _run(tmp_path)
    assert _peak_dbfs(Path(d["output"])) <= -0.5
    assert d["checks"]["peak_under_limit"] is True
    assert d["measurements"]["peak_dbfs"] == pytest.approx(-1.0, abs=0.01)


def test_peak_above_limit_is_reported(tmp_path, monkeypatch):
    """Sabotaged normalization (+1 dB too hot): the file peaks above -0.5 dBFS."""
    real_gain = sfx_synth.normalization_gain
    monkeypatch.setattr(sfx_synth, "normalization_gain", lambda st, p: real_gain(st, p) * 10 ** (1.0 / 20))
    d = _run(tmp_path)
    assert _peak_dbfs(Path(d["output"])) > -0.5
    assert d["checks"]["peak_under_limit"] is False
    assert d["status"] == "revise"
    assert any("peak" in i for i in d["issues"])


def test_peak_target_above_limit_is_refused(tmp_path):
    res = SfxSynth().execute({"events": HEALTHY, "duration_seconds": DURATION,
                              "output_path": str(tmp_path / "x.wav"), "peak_dbfs": -0.1})
    assert not res.success and "peak_dbfs" in res.error


# ---------------------------------------------------------------- loudness

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg missing")
def test_loudness_is_measured_by_ebur128(tmp_path):
    """The reported value is ffmpeg's reading of the file, not a constant: a
    render 6 dB quieter must read about 6 LU lower."""
    loud = _run(tmp_path, "a.wav")["measurements"]["integrated_lufs"]
    quiet = _run(tmp_path, "b.wav", peak_dbfs=-7.0)["measurements"]["integrated_lufs"]
    assert loud - quiet == pytest.approx(6.0, abs=0.3)


def test_loudness_without_meter_is_null_and_not_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(sfx_synth.shutil, "which", lambda name: None)
    d = _run(tmp_path)
    assert d["measurements"]["integrated_lufs"] is None
    assert "ffmpeg" in d["not_checked"]["integrated_lufs"]
    assert d["checks"]["loudness_measured"] is False
    assert d["status"] == "revise"  # never pass by default


# ---------------------------------------------------------------- determinism

def test_two_renders_are_byte_identical(tmp_path):
    a = _run(tmp_path, "a.wav")
    b = _run(tmp_path, "b.wav")
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()  # noqa: E731
    assert digest(a["output"]) == digest(b["output"]) == a["sha256"] == b["sha256"]


def test_seed_changes_the_noise(tmp_path):
    """Guards the identity above against a constant output."""
    assert _run(tmp_path, "a.wav")["sha256"] != _run(tmp_path, "b.wav", seed=12)["sha256"]


# ---------------------------------------------------------------- explicit errors

@pytest.mark.parametrize(("event", "needle"), [
    ({"t": 0.5, "kind": "kazoo"}, "unknown kind 'kazoo'"),
    ({"t": 0.5, "kind": "click", "lo": 200}, "unknown parameter"),
    ({"t": 9.0, "kind": "click"}, "t=9.0"),
    ({"t": 0.5, "kind": "click", "gain": 0}, "gain"),
    ({"t": 0.5, "kind": "click", "pan": 1.5}, "pan"),
    ({"t": 0.5, "kind": "thud", "dur": 0}, "dur"),
])
def test_bad_event_is_an_explicit_error(tmp_path, event, needle):
    out = tmp_path / "x.wav"
    res = SfxSynth().execute({"events": [event], "duration_seconds": DURATION, "output_path": str(out)})
    assert not res.success and needle in res.error
    assert not out.exists()


def test_empty_event_list_is_an_error(tmp_path):
    res = SfxSynth().execute({"events": [], "duration_seconds": 1.0, "output_path": str(tmp_path / "x.wav")})
    assert not res.success and "non-empty" in res.error
