"""Synthesized sound effects from an event list.

Every sound is computed from oscillators and seeded noise: no sound file, no
licence, no network. The same events and seed always give the same WAV bytes.

The report carries MEASUREMENTS read back from the written file, never
assumptions: sample peak (dBFS), integrated loudness (LUFS, ffmpeg ebur128 —
``null`` + ``not_checked`` when the meter cannot run), and for each event the
energy found in its window compared to the background without it.
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

def validate_events(events: Any, duration: float) -> list[dict[str, Any]]:
    """Normalized copies of the events. Raises ValueError with an explicit message."""
    if not isinstance(events, list) or not events:
        raise ValueError("events must be a non-empty list of {t, kind, gain, pan}")
    out = []
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            raise ValueError(f"event {i}: must be an object, got {type(ev).__name__}")
        kind = ev.get("kind")
        if kind not in KIND_PARAMS:
            raise ValueError(f"event {i}: unknown kind {kind!r} (known: {sorted(KIND_PARAMS)})")
        extra = set(ev) - EVENT_KEYS - set(KIND_PARAMS[kind])
        if extra:
            raise ValueError(f"event {i} ({kind}): unknown parameter(s) {sorted(extra)} "
                             f"(allowed: {sorted(EVENT_KEYS | set(KIND_PARAMS[kind]))})")
        t = ev.get("t")
        if not isinstance(t, (int, float)) or not 0 <= t < duration:
            raise ValueError(f"event {i} ({kind}): t={t!r} must be a number in [0, {duration})")
        gain = ev.get("gain", 1.0)
        if not isinstance(gain, (int, float)) or gain <= 0:
            raise ValueError(f"event {i} ({kind}): gain={gain!r} must be > 0")
        pan = ev.get("pan", 0.5)
        if not isinstance(pan, (int, float)) or not 0 <= pan <= 1:
            raise ValueError(f"event {i} ({kind}): pan={pan!r} must be in [0, 1] (0 = left)")
        params = {**KIND_PARAMS[kind], **{k: ev[k] for k in KIND_PARAMS[kind] if k in ev}}
        if params.get("dur", 1) <= 0:
            raise ValueError(f"event {i} ({kind}): dur must be > 0")
        out.append({"t": float(t), "kind": kind, "gain": float(gain), "pan": float(pan), "params": params})
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


def normalization_gain(stereo: np.ndarray, peak_dbfs: float) -> float:
    peak = float(np.max(np.abs(stereo)))
    if peak <= 0:
        raise ValueError("the render is silent: nothing to normalize")
    return 10 ** (peak_dbfs / 20) / peak


def _db(x: float) -> float:
    return 10 * math.log10(max(x, 1e-12))


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


def event_presence(wav: np.ndarray, start: int, contrib: np.ndarray, fps: float,
                   margin_db: float) -> dict[str, Any]:
    """Energy in the event's window (±1 frame around its loudest instant), read
    from the file, against the same window with the event's expected
    contribution removed (the background it sits on)."""
    if contrib.shape[1] == 0:
        return {"window": None, "energy_db": None, "floor_db": None, "margin_db": None, "present": False}
    hop = max(SR // 200, 1)
    power = (contrib ** 2).sum(axis=0)
    frames = np.add.reduceat(power, np.arange(0, len(power), hop))
    peak = start + int(np.argmax(frames)) * hop + hop // 2
    half = int(round(SR / fps))
    a, b = max(peak - half, 0), min(peak + half, wav.shape[1])
    c = np.zeros((2, b - a))
    lo, hi = max(a, start), min(b, start + contrib.shape[1])
    if hi > lo:
        c[:, lo - a:hi - a] = contrib[:, lo - start:hi - start]
    w = wav[:, a:b]
    energy, floor = _db(float(np.mean(w ** 2))), _db(float(np.mean((w - c) ** 2)))
    margin = energy - floor
    return {"window": [round(a / SR, 4), round(b / SR, 4)], "energy_db": round(energy, 2),
            "floor_db": round(floor, 2), "margin_db": round(margin, 2), "present": margin >= margin_db}


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
           peak_dbfs: float = -1.0, reverb_mix: float = 0.18, fps: float = 30.0,
           presence_margin_db: float = 1.0) -> dict[str, Any]:
    """Render validated events to a 48 kHz stereo 16-bit WAV and measure it."""
    n_total = int(round(duration * SR))
    ir = room_ir(seed)
    sigs = [synthesize(ev, i, seed) for i, ev in enumerate(events)]
    dry = np.zeros((2, n_total + SR))
    for ev, sig in zip(events, sigs):
        _place(dry, ev, sig)
    fade = _fade(n_total)
    stereo = _post(dry, ir, reverb_mix)[:, :n_total] * fade
    gain = normalization_gain(stereo, peak_dbfs)
    pcm = np.clip(np.round(stereo * gain * FULL_SCALE), -FULL_SCALE, FULL_SCALE).astype("<i2")
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

    per_event, issues, not_checked = [], [], {}
    for i, (ev, sig) in enumerate(zip(events, sigs)):
        start, contrib = _event_contribution(ev, sig, ir, reverb_mix, gain, fade, n_total)
        m = event_presence(wav, start, contrib, fps, presence_margin_db)
        per_event.append({"index": i, "t": ev["t"], "kind": ev["kind"], "gain": ev["gain"], "pan": ev["pan"], **m})
        if not m["present"]:
            issues.append(f"event {i} ({ev['kind']} at {ev['t']:.3f}s) not found in its window: "
                          f"margin {m['margin_db']} dB < {presence_margin_db} dB over the background")
    peak_ok = peak_db is not None and peak_db <= PEAK_LIMIT_DBFS
    if not peak_ok:
        issues.append(f"sample peak {peak_db} dBFS above the {PEAK_LIMIT_DBFS} dBFS limit")
    if loud.get("integrated_lufs") is None:
        not_checked["integrated_lufs"] = loud.get("reason", "loudness not measured")
    checks = {
        "peak_under_limit": peak_ok,
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
        "not_checked": not_checked,
        "measurements": {
            "peak_dbfs": peak_db,
            "integrated_lufs": loud.get("integrated_lufs"),
            "true_peak_dbtp": loud.get("true_peak_dbtp"),
            "peak_limit_dbfs": PEAK_LIMIT_DBFS,
            "presence_margin_db": presence_margin_db,
            "window_frames_per_second": fps,
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
            "duration_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 3600},
            "output_path": {"type": "string", "default": "sfx_synth.wav"},
            "seed": {"type": "integer", "default": DEFAULT_SEED},
            "peak_dbfs": {"type": "number", "default": -1.0, "minimum": -30, "maximum": PEAK_LIMIT_DBFS},
            "reverb_mix": {"type": "number", "default": 0.18, "minimum": 0, "maximum": 1},
            "presence_margin_db": {"type": "number", "default": 1.0, "minimum": 0},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=1024, vram_mb=0, disk_mb=50, network_required=False)
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
            duration = inputs.get("duration_seconds")
            if not isinstance(duration, (int, float)) or not 0 < duration <= 3600:
                raise ValueError(f"duration_seconds={duration!r} must be a number in (0, 3600]")
            peak_dbfs = float(inputs.get("peak_dbfs", -1.0))
            if not -30 <= peak_dbfs <= PEAK_LIMIT_DBFS:
                raise ValueError(f"peak_dbfs={peak_dbfs} must be in [-30, {PEAK_LIMIT_DBFS}]")
            reverb_mix = float(inputs.get("reverb_mix", 0.18))
            if not 0 <= reverb_mix <= 1:
                raise ValueError(f"reverb_mix={reverb_mix} must be in [0, 1]")
            events = validate_events(inputs.get("events"), float(duration))
            data = render(
                events, float(duration), Path(inputs.get("output_path", "sfx_synth.wav")),
                seed=int(inputs.get("seed", DEFAULT_SEED)), peak_dbfs=peak_dbfs, reverb_mix=reverb_mix,
                presence_margin_db=float(inputs.get("presence_margin_db", 1.0)),
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(
            success=True, data=data, artifacts=[data["output"]], seed=data["seed"],
            duration_seconds=round(time.time() - start, 2),
        )
