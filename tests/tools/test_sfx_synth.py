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


def _dense_scene() -> list[dict]:
    """Many quiet sparkles overlapping each other under a pad and two swishes."""
    rng = np.random.default_rng(3)
    evs = [{"t": 0.1, "kind": "pad", "gain": 0.03, "dur": 5.5},
           {"t": 1.0, "kind": "swish", "gain": 0.1, "dur": 1.0, "lo": 900.0, "hi": 6000.0},
           {"t": 2.6, "kind": "swish", "gain": 0.08, "dur": 1.0, "lo": 900.0, "hi": 6000.0}]
    for _ in range(40):
        evs.append({"t": round(float(rng.uniform(0.8, 4.5)), 4), "kind": "sparkle",
                    "gain": round(float(rng.uniform(0.012, 0.04)), 4), "pan": round(float(rng.uniform(0.1, 0.9)), 3),
                    "freq": float(rng.choice([1046.5, 1318.5, 1568.0, 2093.0, 2637.0, 3136.0])),
                    "dur": round(float(rng.uniform(0.4, 1.1)), 3)})
    evs.append({"t": 3.0, "kind": "sparkle", "gain": 0.012, "pan": 0.5, "freq": 2093.0, "dur": 0.5})
    return evs


DENSE = _dense_scene()


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
    assert d["checks"] == {"peak_under_limit": True, "true_peak_under_limit": True,
                           "all_events_present": True, "loudness_measured": True}
    assert all(e["present"] and 0.99 <= e["alpha"] <= 1.01 for e in d["events"]), d["events"]
    assert d["masked"] == []
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
    assert missing[0]["alpha"] < 0.1  # almost none of the expected sound is in the file
    assert any("marimba" in i and "absent" in i for i in d["issues"])


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
    assert d["measurements"]["peak_dbfs"] <= -1.0  # normalization aims at the true peak


def test_peak_above_limit_is_reported(tmp_path, monkeypatch):
    """Sabotaged normalization (+2 dB too hot): the file peaks above -0.5 dBFS."""
    real_gain = sfx_synth.normalization_gain
    monkeypatch.setattr(sfx_synth, "normalization_gain", lambda st, p: real_gain(st, p) * 10 ** (2.0 / 20))
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


# ---------------------------------------------------------------- review B corrections

def _run_events(tmp_path, events, duration=DURATION, name="d.wav", **extra):
    res = SfxSynth().execute({"events": events, "duration_seconds": duration,
                              "output_path": str(tmp_path / name), **extra})
    assert res.success, res.error
    return res.data


def test_dense_healthy_scene_raises_no_alarm(tmp_path):
    """Overlapping quiet sparkles under a pad are all in the file: none is absent.
    (Masked ones are reported as information, never as issues.)"""
    d = _run_events(tmp_path, DENSE, duration=6.0)
    assert d["checks"]["all_events_present"] is True, d["issues"]
    assert not any("absent" in i for i in d["issues"])
    assert all(0.9 <= e["alpha"] <= 1.1 for e in d["events"])
    assert d["measurements"]["events_masked"] >= 10  # the scene really is dense
    assert d["status"] == "pass" or not HAS_FFMPEG


def test_one_quiet_sparkle_dropped_from_dense_scene_is_caught(tmp_path, monkeypatch):
    """The last sparkle (gain 0.012, under everything else) never reaches the bus."""
    real_place = sfx_synth._place
    target = DENSE[-1]

    def drop_it(dry, ev, sig):
        if not (ev["t"] == target["t"] and ev["gain"] == target["gain"] and ev["kind"] == "sparkle"):
            real_place(dry, ev, sig)

    monkeypatch.setattr(sfx_synth, "_place", drop_it)
    d = _run_events(tmp_path, DENSE, duration=6.0)
    absent = [e["index"] for e in d["events"] if not e["present"]]
    assert absent == [len(DENSE) - 1]
    assert any("absent" in i for i in d["issues"])


def test_event_silenced_by_the_fade_out_is_absent(tmp_path):
    """A click at the very end lands in the 0.3 s fade-out: it is not in the file."""
    d = _run_events(tmp_path, [{"t": 0.5, "kind": "thud"}, {"t": 1.99999, "kind": "click"}], duration=2.0)
    assert [e["index"] for e in d["events"] if not e["present"]] == [1]
    assert d["status"] == "revise"


def test_presence_margin_knob_is_gone():
    """presence_margin_db=0 turned the check into a false green; it no longer exists."""
    assert "presence_margin_db" not in SfxSynth().input_schema["properties"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg missing")
def test_true_peak_of_a_single_click_stays_under_limit(tmp_path):
    """One default click: high-frequency content overshoots between samples.
    Normalization must aim at the true peak, and the check must read it."""
    d = _run_events(tmp_path, [{"t": 0.5, "kind": "click"}], duration=2.0)
    tp = d["measurements"]["true_peak_dbtp"]
    assert tp is not None and tp <= -0.5, tp
    assert d["checks"]["true_peak_under_limit"] is True
    assert d["status"] == "pass", d["issues"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg missing")
def test_true_peak_above_limit_is_reported(tmp_path, monkeypatch):
    """Sabotage: normalize on the sample peak to -0.6 dBFS (under the sample limit);
    the click's inter-sample peak then exceeds -0.5 dBTP and must be reported."""
    monkeypatch.setattr(sfx_synth, "normalization_gain",
                        lambda st, p: 10 ** (-0.6 / 20) / float(np.max(np.abs(st))))
    d = _run_events(tmp_path, [{"t": 0.5, "kind": "click"}], duration=2.0)
    assert d["measurements"]["true_peak_dbtp"] > -0.5
    assert d["checks"]["peak_under_limit"] is True
    assert d["checks"]["true_peak_under_limit"] is False
    assert any("true peak" in i for i in d["issues"])


def test_true_peak_without_meter_is_null_and_not_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(sfx_synth.shutil, "which", lambda name: None)
    d = _run(tmp_path)
    assert d["checks"]["true_peak_under_limit"] is None
    assert "true_peak_dbtp" in d["not_checked"]
    assert d["status"] == "revise"


@pytest.mark.parametrize(("bad", "field"), [
    ({"t": True, "kind": "click"}, "t="),
    ({"t": 0.5, "kind": "click", "gain": True}, "gain"),
    ({"t": 0.5, "kind": "click", "pan": False}, "pan"),
    ({"t": 0.5, "kind": "click", "gain": float("nan")}, "gain"),
    ({"t": 0.5, "kind": "click", "gain": float("inf")}, "gain"),
    ({"t": float("nan"), "kind": "click"}, "t="),
    ({"t": 0.5, "kind": "thud", "dur": "0.5"}, "dur"),
    ({"t": 0.5, "kind": "pad", "freqs": "abc"}, "freqs"),
    ({"t": 0.5, "kind": "pad", "freqs": []}, "freqs"),
    ({"t": 0.5, "kind": "pad", "freqs": [220.0, float("nan")]}, "freqs"),
    ({"t": 0.5, "kind": "bell", "freq": 0}, "freq"),
    ({"t": 0.5, "kind": "bell", "freq": -440.0}, "freq"),
    ({"t": 0.5, "kind": "motor", "freq_end": float("inf")}, "freq_end"),
    ({"t": 0.5, "kind": "click", "noise": float("nan")}, "noise"),
    ({"t": 0.5, "kind": "swish", "lo": 3000.0, "hi": 500.0}, "lo="),
    ({"t": 0.5, "kind": ["click"]}, "unknown kind"),
])
def test_bad_values_return_an_error_result(tmp_path, bad, field):
    """Never an exception out of execute, never a WAV of NaN with success=True,
    and the error names the faulty field."""
    out = tmp_path / "x.wav"
    res = SfxSynth().execute({"events": [bad], "duration_seconds": DURATION, "output_path": str(out)})
    assert res.success is False and field in res.error, res.error
    assert not out.exists()


def test_event_below_16_bit_resolution_is_absent(tmp_path):
    """Rounded away by the 16-bit file: not 'present' on rounding noise."""
    d = _run_events(tmp_path, [{"t": 0.5, "kind": "thud"}, {"t": 1.0, "kind": "click", "gain": 1e-7}], duration=2.0)
    absent = [e for e in d["events"] if not e["present"]]
    assert [e["index"] for e in absent] == [1] and "16-bit" in absent[0]["reason"]


def test_unexpected_failure_is_an_error_result(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("synth exploded")

    monkeypatch.setattr(sfx_synth, "synthesize", boom)
    res = SfxSynth().execute({"events": HEALTHY, "duration_seconds": DURATION, "output_path": str(tmp_path / "x.wav")})
    assert res.success is False and "synth exploded" in res.error


@pytest.mark.parametrize("dur", [True, float("nan"), float("inf"), "8", 3600])
def test_bad_duration_returns_an_error_result(tmp_path, dur):
    res = SfxSynth().execute({"events": [{"t": 0.1, "kind": "click"}], "duration_seconds": dur,
                              "output_path": str(tmp_path / "x.wav")})
    assert res.success is False and res.error


def test_duration_bound_matches_declared_memory():
    tool = SfxSynth()
    max_s = tool.input_schema["properties"]["duration_seconds"]["maximum"]
    assert max_s <= 600
    assert tool.resource_profile.ram_mb >= sfx_synth.estimated_ram_mb(max_s)
