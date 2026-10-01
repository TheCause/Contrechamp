"""Synthesized sound effects from an event list.

Every sound is computed from oscillators and seeded noise: no sound file, no
licence, no network. The same events and seed give the same WAV bytes in a
given environment (numpy version and platform): FFT rounding may differ elsewhere.

The report carries MEASUREMENTS read back from the written file, never
assumptions: sample peak (dBFS), true peak (dBTP) and integrated loudness
(LUFS) from ffmpeg ebur128 — ``null`` + ``not_checked`` when the meter cannot
run — and for each event the share of its expected sound found in its window
(absent = issue; masked under louder sounds = information only).
"""

from __future__ import annotations

import hashlib
import math
import shutil
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

SR = 48000
DEFAULT_SEED = 11
PEAK_LIMIT_DBFS = -0.5  # a render louder than this is reported as an issue
FULL_SCALE = 32767.0

# Kind -> its optional parameters and their defaults. Anything else is refused.
KIND_PARAMS: dict[str, dict[str, Any]] = {
    "click": {"freq": 2600.0, "dur": 0.03, "noise": 0.6},
    "thud": {"freq": 95.0, "dur": 0.35},
    "metal": {"freq": 1900.0, "dur": 0.8},
    "bell": {"freq": 523.25, "dur": 1.6},
    "marimba": {"freq": 392.0, "dur": 0.6},
    "motor": {"freq": 90.0, "freq_end": 90.0, "dur": 1.0, "wobble": 0.0},
    "swish": {"dur": 0.6, "lo": 500.0, "hi": 3000.0},
    "sparkle": {"freq": 2093.0, "dur": 0.7},
    "pad": {"dur": 3.0, "freqs": [220.0, 261.63, 329.63, 392.0, 493.88]},
}
EVENT_KEYS = {"t", "kind", "gain", "pan"}


# ---------------------------------------------------------------- DSP helpers

def _times(dur: float) -> np.ndarray:
    return np.arange(max(int(round(dur * SR)), 1)) / SR


def _filter(x: np.ndarray, lo: float | None = None, hi: float | None = None,
            order: int = 2) -> np.ndarray:
    """Zero-phase Butterworth-magnitude filter applied in the frequency domain.

    ``lo`` = high-pass corner, ``hi`` = low-pass corner (both = band-pass).
    The signal is zero-padded so nothing wraps around.
    """
    n = x.shape[-1]
    nfft = 1 << int(math.ceil(math.log2(n + SR // 2)))
    f = np.fft.rfftfreq(nfft, 1 / SR)
    h = np.ones_like(f)
    if lo:
        r = (f / lo) ** order
        h *= r / np.sqrt(1 + r * r)
    if hi:
        r = (f / hi) ** order
        h /= np.sqrt(1 + r * r)
    return np.fft.irfft(np.fft.rfft(x, nfft) * h, nfft)[..., :n]


def _convolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    n = x.shape[-1]
    nfft = 1 << int(math.ceil(math.log2(n + len(ir))))
    return np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(ir, nfft), nfft)[..., :n]


def _env(n: int, attack: float = 0.005, release: float = 0.1) -> np.ndarray:
    t = np.arange(n) / SR
    return np.minimum(1, t / max(attack, 1e-4)) * np.exp(-t / release)


# ---------------------------------------------------------------- the kinds

def _click(rng, freq, dur, noise):
    t = _times(dur)
    s = np.sin(2 * np.pi * freq * t) * (1 - noise) + _filter(rng.normal(0, 1, len(t)), lo=1500) * noise * 0.5
    return s * _env(len(t), 0.0008, dur / 5)


def _thud(rng, freq, dur):
    t = _times(dur)
    sweep = freq * (1 + 0.6 * np.exp(-t * 30))
    s = np.sin(2 * np.pi * np.cumsum(sweep) / SR) + 0.3 * _filter(rng.normal(0, 1, len(t)), hi=600)
    return s * _env(len(t), 0.002, 0.08)


def _partials(freq, dur, table, attack):
    t = _times(dur)
    s = sum(a * np.sin(2 * np.pi * freq * r * t) * np.exp(-t / (dur * d)) for r, a, d in table)
    return s * _env(len(t), attack, 10)


def _metal(rng, freq, dur):
    return _partials(freq, dur, ((1, 1, 0.35), (1.58, 0.6, 0.25), (2.31, 0.45, 0.18), (3.7, 0.25, 0.1)), 0.001)


def _bell(rng, freq, dur):
    return _partials(freq, dur, ((1, 1, 0.4), (2, 0.35, 0.25), (3.01, 0.15, 0.15), (4.2, 0.08, 0.08)), 0.003)


def _marimba(rng, freq, dur):
    t = _times(dur)
    s = np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.18) + 0.3 * np.sin(2 * np.pi * freq * 4 * t) * np.exp(-t / 0.03)
    return s * _env(len(t), 0.002, 10)


def _motor(rng, freq, freq_end, dur, wobble):
    t = _times(dur)
    f = np.linspace(freq, freq_end, len(t)) * (1 + wobble * np.sin(2 * np.pi * 7 * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    saw = sum(np.sin(k * ph) / k for k in range(1, 9))
    s = _filter(saw, hi=900) + 0.15 * _filter(rng.normal(0, 1, len(t)), lo=200, hi=1200)
    e = np.minimum(1, np.minimum(t / min(0.12, dur / 3), (dur - t) / min(0.15, dur / 3)))
    return s * np.clip(e, 0, 1)


def _swish(rng, dur, lo, hi):
    t = _times(dur)
    n = rng.normal(0, 1, len(t))
    a = _filter(n, lo=lo, hi=hi) * 0.8 + _filter(n, hi=300) * 0.6
    a *= 1 + 0.35 * np.sin(2 * np.pi * 23 * t)
    return a * np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 1.5


def _sparkle(rng, freq, dur):
    t = _times(dur)
    s = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 2.76 * t) * np.exp(-t * 12)
    return s * _env(len(t), 0.002, dur / 4)


def _pad(rng, dur, freqs):
    t = _times(dur)
    s = sum(np.sin(2 * np.pi * f * t + k) + 0.6 * np.sin(2 * np.pi * f * 1.003 * t) for k, f in enumerate(freqs))
    attack, release, tail = min(1.6, 0.45 * dur), min(0.6, 0.2 * dur), min(0.3, 0.1 * dur)
    return _filter(s, hi=1800) * np.clip(t / attack, 0, 1) * np.clip((dur - tail - t) / release, 0, 1)


SYNTHS = {
    "click": _click, "thud": _thud, "metal": _metal, "bell": _bell, "marimba": _marimba,
    "motor": _motor, "swish": _swish, "sparkle": _sparkle, "pad": _pad,
}


# ---------------------------------------------------------------- validation

def _number(value: Any, what: str, lo: float | None = None, hi: float | None = None,
            lo_open: bool = False, hi_open: bool = False) -> float:
    """A finite real number (never a bool or a string) inside the given bounds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{what}={value!r} must be a finite number")
    if lo is not None and (value <= lo if lo_open else value < lo):
        raise ValueError(f"{what}={value!r} must be {'>' if lo_open else '>='} {lo}")
    if hi is not None and (value >= hi if hi_open else value > hi):
        raise ValueError(f"{what}={value!r} must be {'<' if hi_open else '<='} {hi}")
    return float(value)


NYQUIST = SR / 2
MAX_DURATION = 300.0  # seconds; memory grows linearly, see estimated_ram_mb()
MAX_EVENT_DUR = 120.0


def _params(kind: str, ev: dict[str, Any], where: str) -> dict[str, Any]:
    p = {**KIND_PARAMS[kind], **{k: ev[k] for k in KIND_PARAMS[kind] if k in ev}}
    out: dict[str, Any] = {}
    for key, value in p.items():
        what = f"{where}: {key}"
        if key == "freqs":
            if not isinstance(value, list) or not value:
                raise ValueError(f"{what}={value!r} must be a non-empty list of frequencies")
            out[key] = [_number(f, what, 0, NYQUIST, lo_open=True, hi_open=True) for f in value]
        elif key in ("freq", "freq_end", "lo", "hi"):
            out[key] = _number(value, what, 0, NYQUIST, lo_open=True, hi_open=True)
        elif key == "dur":
            out[key] = _number(value, what, 0, MAX_EVENT_DUR, lo_open=True)
        elif key == "noise":
            out[key] = _number(value, what, 0, 1)
        elif key == "wobble":
            out[key] = _number(value, what, 0, 0.5)
    if "lo" in out and out["lo"] >= out["hi"]:
        raise ValueError(f"{where}: lo={out['lo']} must be below hi={out['hi']}")
    return out


def validate_events(events: Any, duration: float) -> list[dict[str, Any]]:
    """Normalized copies of the events. Raises ValueError with an explicit message."""
    if not isinstance(events, list) or not events:
        raise ValueError("events must be a non-empty list of {t, kind, gain, pan}")
    out = []
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            raise ValueError(f"event {i}: must be an object, got {type(ev).__name__}")
        kind = ev.get("kind")
        if not isinstance(kind, str) or kind not in KIND_PARAMS:
            raise ValueError(f"event {i}: unknown kind {kind!r} (known: {sorted(KIND_PARAMS)})")
        extra = set(ev) - EVENT_KEYS - set(KIND_PARAMS[kind])
        if extra:
            raise ValueError(f"event {i} ({kind}): unknown parameter(s) {sorted(extra)} "
                             f"(allowed: {sorted(EVENT_KEYS | set(KIND_PARAMS[kind]))})")
        where = f"event {i} ({kind})"
        t = _number(ev.get("t"), f"{where}: t", 0, duration, hi_open=True)
        gain = _number(ev.get("gain", 1.0), f"{where}: gain", 0, 1000, lo_open=True)
        pan = _number(ev.get("pan", 0.5), f"{where}: pan (0 = left, 1 = right)", 0, 1)
        out.append({"t": t, "kind": kind, "gain": gain, "pan": pan, "params": _params(kind, ev, where)})
    return out


# ---------------------------------------------------------------- rendering

def synthesize(ev: dict[str, Any], index: int, seed: int) -> np.ndarray:
    """Mono signal of one event (its own seeded noise: independent of the others)."""
    rng = np.random.default_rng([seed, index])
    return SYNTHS[ev["kind"]](rng, **ev["params"]) * ev["gain"]


def _pan_gains(pan: float) -> tuple[float, float]:
    return math.cos(pan * math.pi / 2) * 1.41, math.sin(pan * math.pi / 2) * 1.41


def _place(dry: np.ndarray, ev: dict[str, Any], sig: np.ndarray) -> None:
    """Add one event to the dry stereo bus."""
    i = int(round(ev["t"] * SR))
    j = min(i + len(sig), dry.shape[1])
    gl, gr = _pan_gains(ev["pan"])
    dry[0, i:j] += sig[: j - i] * gl
    dry[1, i:j] += sig[: j - i] * gr


def room_ir(seed: int) -> np.ndarray:
    rng = np.random.default_rng([seed, 1 << 20])
    t = _times(0.7)
    ir = _filter(rng.normal(0, 1, len(t)) * np.exp(-t / 0.16), hi=5000)
    return ir / np.sqrt(np.sum(ir ** 2))


def _post(dry: np.ndarray, ir: np.ndarray, reverb_mix: float) -> np.ndarray:
    """Short room reverb + 30 Hz high-pass (linear: a sum's post is the sum of posts)."""
    return _filter(dry + reverb_mix * _convolve(dry, ir), lo=30)


def _fade(n_total: int) -> np.ndarray:
    fade = np.ones(n_total)
    k = min(int(0.3 * SR), n_total)
    fade[n_total - k:] = np.linspace(1, 0, k)
    return fade


def true_peak_estimate(stereo: np.ndarray, oversample: int = 4) -> float:
    """Peak between samples: x4 band-limited (FFT) upsampling, by 1 s blocks."""
    n = stereo.shape[1]
    block, pad = SR, SR // 20
    peak = float(np.max(np.abs(stereo))) if stereo.size else 0.0
    for s in range(0, n, block):
        a, b = max(s - pad, 0), min(s + block + pad, n)
        up = np.fft.irfft(np.fft.rfft(stereo[:, a:b]), (b - a) * oversample) * oversample
        keep = up[:, (s - a) * oversample:(min(s + block, n) - a) * oversample]
        if keep.size:
            peak = max(peak, float(np.max(np.abs(keep))))
    return peak


def normalization_gain(stereo: np.ndarray, peak_dbfs: float) -> float:
    """Gain that puts the TRUE (inter-sample) peak at ``peak_dbfs``."""
    peak = true_peak_estimate(stereo)
    if peak <= 0:
        raise ValueError("the render is silent: nothing to normalize")
    return 10 ** (peak_dbfs / 20) / peak


def _db(x: float) -> float:
    return 10 * math.log10(max(x, 1e-30))


def _event_contribution(ev, sig, ir, reverb_mix, gain, fade, n_total):
    """What one event alone adds to the final file: (start sample, stereo array).

    Computed by its own path (not through ``_place``), so a mixing bug that
    drops or moves an event leaves this expectation intact for the check.
    """
    pad = SR // 4
    start = int(round(ev["t"] * SR))
    seg = np.zeros((2, pad + len(sig) + len(ir) + pad))
    gl, gr = _pan_gains(ev["pan"])
    seg[0, pad:pad + len(sig)] = sig * gl
    seg[1, pad:pad + len(sig)] = sig * gr
    seg = _post(seg, ir, reverb_mix) * gain
    a = start - pad
    lo, hi = max(a, 0), min(a + seg.shape[1], n_total)
    if hi <= lo:
        return lo, np.zeros((2, 0))
    seg = seg[:, lo - a:hi - a] * fade[lo:hi]
    return lo, seg


ABSENT_BELOW_ALPHA = 0.5  # under half of the expected sound found in its window = absent (issue)
MASKED_BELOW_DB = -10.0   # event 10 dB under the other sounds in its window = masked (information)
QUANT_NOISE_POWER = (1 / FULL_SCALE) ** 2 / 12


def _window(start: int, contrib: np.ndarray, n: int, fps: float) -> tuple[int, int]:
    """±1 frame around the loudest instant of the event's own contribution."""
    peak = start
    if contrib.shape[1]:
        hop = max(SR // 200, 1)
        power = (contrib ** 2).sum(axis=0)
        frames = np.add.reduceat(power, np.arange(0, len(power), hop))
        peak = start + int(np.argmax(frames)) * hop + hop // 2
    half = int(round(SR / fps))
    return max(peak - half, 0), min(peak + half, n)


def event_presence(wav: np.ndarray, contribs: list[tuple[int, np.ndarray]],
                   fps: float = 30.0) -> list[dict[str, Any]]:
    """How much of each event's expected sound the file holds in its window.

    residual = file - expected contributions of ALL OTHER events (in the window);
    alpha = <residual, c> / <c, c> is the least-squares scale of the event in the
    file: ~1 when it is there, ~0 when it is missing or misplaced. Masking (the
    event far under the other sounds) is reported apart: it is not an absence.
    """
    n = wav.shape[1]
    total = np.zeros_like(wav)
    for start, c in contribs:
        total[:, start:start + c.shape[1]] += c
    out = []
    for start, c in contribs:
        a, b = _window(start, c, n, fps)
        cw = np.zeros((2, b - a))
        lo, hi = max(a, start), min(b, start + c.shape[1])
        if hi > lo:
            cw[:, lo - a:hi - a] = c[:, lo - start:hi - start]
        entry: dict[str, Any] = {"window": [round(a / SR, 4), round(b / SR, 4)]}
        if not cw.size or float(np.mean(cw ** 2)) <= QUANT_NOISE_POWER:
            entry.update(alpha=None, event_to_rest_db=None, present=False, masked=False,
                         reason="expected sound below the 16-bit floor of the file (faded out or too quiet)")
            out.append(entry)
            continue
        w = wav[:, a:b]
        residual = w - (total[:, a:b] - cw)
        alpha = float(np.sum(residual * cw) / np.sum(cw * cw))
        to_rest = _db(float(np.sum(cw * cw))) - _db(float(np.sum((w - cw) ** 2)))
        entry.update(alpha=round(alpha, 4), event_to_rest_db=round(to_rest, 2),
                     present=alpha >= ABSENT_BELOW_ALPHA, masked=to_rest < MASKED_BELOW_DB)
        if not entry["present"]:
            entry["reason"] = f"only {alpha:.2f} of the expected sound is in its window"
        out.append(entry)
    return out


def estimated_ram_mb(duration: float) -> int:
    """Upper estimate of a render's peak memory: about 12 float64 stereo copies of
    the bus at once (FFT padding up to x2, reverb and filter working arrays)."""
    return int(12 * 2 * (duration + 1) * SR * 8 / 2 ** 20) + 200


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM")
        ch = w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return data.reshape(-1, ch).T.astype(np.float64) / FULL_SCALE


def measure_loudness(path: Path) -> dict[str, Any]:
    """Integrated loudness and true peak via ffmpeg ebur128, or null + the reason."""
    if shutil.which("ffmpeg") is None:
        return {"integrated_lufs": None, "true_peak_dbtp": None, "reason": "ffmpeg not found: ebur128 meter unavailable"}
    from lib.render_checks import loudness

    try:
        out = loudness(path)
    except Exception as exc:  # the meter failed: say so, never guess
        return {"integrated_lufs": None, "true_peak_dbtp": None, "reason": f"ebur128 failed: {exc}"}
    out.setdefault("true_peak_dbtp", None)
    return out


def render(events: list[dict[str, Any]], duration: float, output: Path, *, seed: int = DEFAULT_SEED,
           peak_dbfs: float = -1.0, reverb_mix: float = 0.18, fps: float = 30.0) -> dict[str, Any]:
    """Render validated events to a 48 kHz stereo 16-bit WAV and measure it."""
    n_total = int(round(duration * SR))
    ir = room_ir(seed)
    sigs = [synthesize(ev, i, seed) for i, ev in enumerate(events)]
    dry = np.zeros((2, n_total + SR))
    for ev, sig in zip(events, sigs):
        _place(dry, ev, sig)
    fade = _fade(n_total)
    stereo = _post(dry, ir, reverb_mix)[:, :n_total] * fade
    del dry
    gain = normalization_gain(stereo, peak_dbfs)
    scaled = stereo * gain * FULL_SCALE
    del stereo
    if not np.all(np.isfinite(scaled)):
        raise ValueError("the render produced non-finite samples")
    pcm = np.clip(np.round(scaled), -FULL_SCALE, FULL_SCALE).astype("<i2")
    del scaled
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.T.tobytes())

    # Everything below is read back from the written file.
    wav = read_wav(output)
    peak = float(np.max(np.abs(wav)))
    peak_db = round(20 * math.log10(peak), 2) if peak > 0 else None
    loud = measure_loudness(output)
    tp = loud.get("true_peak_dbtp")

    contribs = [_event_contribution(ev, sig, ir, reverb_mix, gain, fade, n_total) for ev, sig in zip(events, sigs)]
    per_event, issues, masked, not_checked = [], [], [], {}
    for i, (ev, m) in enumerate(zip(events, event_presence(wav, contribs, fps))):
        per_event.append({"index": i, "t": ev["t"], "kind": ev["kind"], "gain": ev["gain"], "pan": ev["pan"], **m})
        if not m["present"]:
            issues.append(f"event {i} ({ev['kind']} at {ev['t']:.3f}s) absent from its window: {m['reason']}")
        elif m["masked"]:
            masked.append(f"event {i} ({ev['kind']} at {ev['t']:.3f}s) is in the file, masked "
                          f"{m['event_to_rest_db']} dB under the other sounds in its window (information only)")
    peak_ok = peak_db is not None and peak_db <= PEAK_LIMIT_DBFS
    if not peak_ok:
        issues.append(f"sample peak {peak_db} dBFS above the {PEAK_LIMIT_DBFS} dBFS limit")
    tp_ok = None if tp is None else tp <= PEAK_LIMIT_DBFS
    if tp_ok is False:
        issues.append(f"true peak {tp} dBTP above the {PEAK_LIMIT_DBFS} dBTP limit")
    if loud.get("integrated_lufs") is None:
        not_checked["integrated_lufs"] = loud.get("reason", "loudness not measured")
    if tp is None:
        not_checked["true_peak_dbtp"] = loud.get("reason", "true peak not measured")
    checks = {
        "peak_under_limit": peak_ok,
        "true_peak_under_limit": tp_ok,
        "all_events_present": all(e["present"] for e in per_event),
        "loudness_measured": loud.get("integrated_lufs") is not None,
    }
    status = "pass" if not issues and not not_checked else "revise"
    with open(output, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    return {
        "output": str(output),
        "format": "wav",
        "sample_rate": SR,
        "channels": 2,
        "duration_seconds": round(wav.shape[1] / SR, 4),
        "seed": seed,
        "sha256": digest,
        "status": status,
        "checks": checks,
        "issues": issues,
        "masked": masked,
        "not_checked": not_checked,
        "measurements": {
            "peak_dbfs": peak_db,
            "true_peak_dbtp": tp,
            "integrated_lufs": loud.get("integrated_lufs"),
            "peak_limit_dbfs": PEAK_LIMIT_DBFS,
            "absent_below_alpha": ABSENT_BELOW_ALPHA,
            "masked_below_db": MASKED_BELOW_DB,
            "window_frames_per_second": fps,
            "events_absent": sum(not e["present"] for e in per_event),
            "events_masked": len(masked),
        },
        "events": per_event,
    }


class SfxSynth(BaseTool):
    name = "sfx_synth"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "sound_effects"
    provider = "contrechamp"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.LOCAL

    dependencies = ["python:numpy"]
    install_instructions = (
        "numpy is a core dependency (pip install -r requirements.txt). "
        "ffmpeg is optional: without it loudness is reported as not checked."
    )
    agent_skills: list[str] = []

    capabilities = ["synthesize_sfx", "event_timeline_to_wav", "offline_generation"]
    supports = {
        "offline": True,
        "deterministic_seed": True,
        "stereo_pan": True,
        "kinds": sorted(KIND_PARAMS),
    }
    best_for = [
        "license-free foley for code-animated scenes (clicks, thuds, chimes, motors, swishes)",
        "sound effects locked to an animation timeline, frame-accurate",
    ]
    not_good_for = [
        "realistic recorded-sound textures (voices, crowds, nature)",
        "music beds",
    ]

    input_schema = {
        "type": "object",
        "required": ["events", "duration_seconds"],
        "properties": {
            "events": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["t", "kind"],
                    "properties": {
                        "t": {"type": "number", "minimum": 0, "description": "Start time in seconds"},
                        "kind": {"type": "string", "enum": sorted(KIND_PARAMS)},
                        "gain": {"type": "number", "exclusiveMinimum": 0, "default": 1.0},
                        "pan": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5,
                                "description": "0 = left, 0.5 = centre, 1 = right"},
                        "freq": {"type": "number"},
                        "freq_end": {"type": "number", "description": "motor only"},
                        "dur": {"type": "number", "exclusiveMinimum": 0},
                        "noise": {"type": "number", "description": "click only, 0..1"},
                        "wobble": {"type": "number", "description": "motor only"},
                        "lo": {"type": "number", "description": "swish only, Hz"},
                        "hi": {"type": "number", "description": "swish only, Hz"},
                        "freqs": {"type": "array", "items": {"type": "number"}, "description": "pad only"},
                    },
                },
                "description": "Per-kind parameters and defaults: see KIND_PARAMS. Unknown kind or "
                "parameter is an error.",
            },
            "duration_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_DURATION},
            "output_path": {"type": "string", "default": "sfx_synth.wav"},
            "seed": {"type": "integer", "default": DEFAULT_SEED},
            "peak_dbfs": {"type": "number", "default": -1.0, "minimum": -30, "maximum": PEAK_LIMIT_DBFS},
            "reverb_mix": {"type": "number", "default": 0.18, "minimum": 0, "maximum": 1},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=estimated_ram_mb(MAX_DURATION), vram_mb=0,
                                       disk_mb=int(MAX_DURATION * SR * 4 / 2 ** 20) + 10, network_required=False)
    idempotency_key_fields = ["events", "duration_seconds", "seed", "peak_dbfs", "reverb_mix"]
    side_effects = ["writes a WAV file to output_path"]
    user_visible_verification = ["Listen to the WAV against the animation", "Read status, issues and not_checked"]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        try:
            duration = _number(inputs.get("duration_seconds"), "duration_seconds", 0, MAX_DURATION, lo_open=True)
            peak_dbfs = _number(inputs.get("peak_dbfs", -1.0), "peak_dbfs", -30, PEAK_LIMIT_DBFS)
            reverb_mix = _number(inputs.get("reverb_mix", 0.18), "reverb_mix", 0, 1)
            seed = inputs.get("seed", DEFAULT_SEED)
            if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
                raise ValueError(f"seed={seed!r} must be a non-negative integer")
            output = inputs.get("output_path", "sfx_synth.wav")
            if not isinstance(output, (str, Path)) or not str(output):
                raise ValueError(f"output_path={output!r} must be a path")
            events = validate_events(inputs.get("events"), duration)
            data = render(events, duration, Path(output), seed=seed, peak_dbfs=peak_dbfs, reverb_mix=reverb_mix)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        except Exception as exc:  # never let an exception escape: report it
            return ToolResult(success=False, error=f"sfx_synth failed: {type(exc).__name__}: {exc}")
        return ToolResult(
            success=True, data=data, artifacts=[data["output"]], seed=data["seed"],
            duration_seconds=round(time.time() - start, 2),
        )
