"""Measured post-render checks for the final review.

Every check here either measures something and reports what it read, or
returns ``None`` with a reason. Nothing defaults to a reassuring value: a
check that did not run must say so (see docs/fork/lot1-checks-that-bite.md).
"""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
from pathlib import Path
from functools import lru_cache
from typing import Any

from PIL import Image

from lib.config_model import OpenMontageConfig, ReviewConfig

# Thresholds live in config.yaml (section `review`, see lib/config_model.py
# ReviewConfig). Why the defaults: glued caption lines seen on a real broken
# render were 22 and 24 letters, long everyday words stay at 20; a clean
# freeze-frame seam measured SSIM 0.99, a wrong one 0.83 and a darkened one
# 0.93 with an 11-point luminance jump.
_CONFIG_PATH: Path | None = None  # None -> repo config.yaml


@lru_cache(maxsize=1)
def settings() -> ReviewConfig:
    return OpenMontageConfig.load(_CONFIG_PATH).review


LEGACY_SAMPLE_POINTS = [0.10, 0.35, 0.65, 0.90]


# --- Pure helpers -------------------------------------------------------------


def _word_key(word: str) -> str:
    return re.sub(r"[^\w]", "", word).lower()


def glued_words(text: str | None, min_len: int | None = None,
                expected: str | None = None) -> list[str]:
    """Tokens that look like several words stuck together.

    Without ``expected`` only long tokens and missing spaces after punctuation
    are caught. A glued caption page of 3-4 short words ("EVERYRIDERGOES")
    stays under any sane length threshold; given the text that should be on
    screen, a token holding two consecutive expected words is glued.
    """
    if not text:
        return []
    if min_len is None:
        min_len = settings().glued_word_min_len
    keys = [_word_key(w) for w in (expected or "").split()]
    known = set(keys)
    pairs = {a + b for a, b in zip(keys, keys[1:]) if a and b and len(a + b) >= 4}
    glued = []
    for token in text.split():
        letters = re.sub(r"[^\w]", "", token)
        if letters.lower() in known:
            continue  # exactly a word that should be there, however long
        if len(letters) >= min_len:
            glued.append(token)
        elif re.search(r"[a-zà-ÿ][.,;:!?][A-Za-zÀ-ÿ0-9]", token):
            # "choix.En2016" — sentence punctuation with no space after it
            glued.append(token)
        elif re.search(r"[A-ZÀ-Þ]{2}[.,;:!?][A-ZÀ-Þ]{2}", token):
            # "DOWN.NOTEVERY" — same in capitals; "S.N.C.F" stays silent
            glued.append(token)
        elif pairs and any(p in letters.lower() for p in pairs):
            glued.append(token)
    return glued


def _normalize(text: str) -> str:
    # OCR reads a typographic apostrophe as a straight one and cannot see a
    # narrow no-break space: neither is a wording difference.
    for a in ("’", "ʼ", "‘"):
        text = text.replace(a, "'")
    text = text.replace("\u202f", " ").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def text_similarity(expected: str, read: str | None, exact: bool = False) -> float:
    """Ratio in [0, 1]. ``exact`` compares characters verbatim (sacred text)."""
    if read is None:
        return 0.0
    if exact:
        a, b = expected.strip(), read.strip()
    else:
        a, b = _normalize(expected), _normalize(read)
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def timeline_segments(cuts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Place cuts on the output timeline by accumulating their durations.

    ``in_seconds``/``out_seconds`` are read as a trim of the source; the
    timeline position is the running sum of ``(out - in) / speed``. This is
    the only reading that holds for both source-trim and timeline-position
    styles seen in real edit_decisions.
    """
    segments = []
    t = 0.0
    for cut in cuts or []:
        speed = float(cut.get("speed") or 1.0) or 1.0
        length = max(float(cut["out_seconds"]) - float(cut["in_seconds"]), 0.0) / speed
        segments.append({
            "id": cut.get("id"),
            "source": cut.get("source"),
            "start": round(t, 3),
            "end": round(t + length, 3),
            "continuity": bool(cut.get("continuity")),
        })
        t += length
    return segments


def continuity_pairs(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cuts that must be invisible: same source on both sides, or declared."""
    pairs = []
    for prev, nxt in zip(segments, segments[1:]):
        if nxt["continuity"] or (prev["source"] and prev["source"] == nxt["source"]):
            pairs.append({"from": prev["id"], "to": nxt["id"], "at": nxt["start"]})
    return pairs


def sample_times(segments: list[dict[str, Any]], duration: float) -> list[tuple[str | None, float]]:
    """One frame at the middle of each segment; legacy 4 points without cuts."""
    if not segments:
        return [(None, round(duration * p, 2)) for p in LEGACY_SAMPLE_POINTS]
    picked = segments
    cap = settings().max_sampled_segments
    if len(segments) > cap:
        step = len(segments) / cap
        picked = [segments[int(i * step)] for i in range(cap)]
    return [(s["id"], round((s["start"] + s["end"]) / 2, 2)) for s in picked]


# --- ffmpeg / tesseract wrappers ------------------------------------------------


def extract_frame(video: Path, t: float, out: Path) -> Path | None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", str(video),
           "-frames:v", "1", str(out)]
    subprocess.run(cmd, capture_output=True, timeout=30)
    return out if out.exists() and out.stat().st_size > 0 else None


def extract_last_frame(video: Path, out: Path) -> Path | None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-sseof", "-0.5", "-i", str(video),
           "-update", "1", "-q:v", "1", str(out)]
    subprocess.run(cmd, capture_output=True, timeout=30)
    return out if out.exists() and out.stat().st_size > 0 else None


def frame_luma(path: Path) -> float:
    with Image.open(path) as img:
        gray = img.convert("L")
        hist = gray.histogram()
    total = sum(hist)
    return sum(i * n for i, n in enumerate(hist)) / total if total else 0.0


def frame_ssim(a: Path, b: Path) -> float | None:
    cmd = ["ffmpeg", "-v", "info", "-i", str(a), "-i", str(b), "-lavfi",
           "[0:v]format=gray[x];[1:v]format=gray[y];[x][y]scale2ref[x2][y2];[x2][y2]ssim",
           "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    match = re.search(r"All:([0-9.]+)", proc.stderr or "")
    return float(match.group(1)) if match else None


# Tesseract on a raw frame is easily fooled by the picture behind the text.
# Measured on real renders: glued grey+cyan captions are caught by the plain
# gray variant or the 200 threshold (the 200 threshold alone, or the raw
# frame, missed some). The 140 threshold is not needed to *detect* them, but
# gave the most complete reading of a caption line, which matters when the
# reading is compared to expected text. Every variant is read and kept.


def _ocr_variants(path: Path, work: Path) -> list[Path]:
    with Image.open(path) as img:
        gray = img.convert("L")
    gray = gray.resize((gray.width * 2, gray.height * 2), Image.LANCZOS)
    work.mkdir(parents=True, exist_ok=True)
    variants = [("gray", gray)]
    for th in settings().ocr_thresholds:
        variants.append((f"t{th}", gray.point(lambda v, th=th: 0 if v > th else 255)))
    paths = []
    for name, img in variants:
        out = work / f"{path.stem}_{name}.png"
        img.save(out)
        paths.append(out)
    return paths


def ocr_readings(path: Path, lang: str = "eng") -> tuple[list[str] | None, str | None]:
    """All readings of a frame (one per preprocessing variant), or (None, reason)."""
    binary = shutil.which("tesseract")
    if not binary:
        return None, "tesseract not installed"
    langs = subprocess.run([binary, "--list-langs"], capture_output=True, text=True, timeout=15)
    available = set((langs.stdout or "").split())
    missing = [code for code in lang.split("+") if code not in available]
    if missing:
        return None, f"tesseract language data missing: {'+'.join(missing)}"
    readings = []
    for variant in _ocr_variants(Path(path), Path(path).parent / ".ocr"):
        proc = subprocess.run([binary, str(variant), "stdout", "-l", lang, "--psm", "3"],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return None, f"tesseract failed: {(proc.stderr or '').strip()[:120]}"
        readings.append(re.sub(r"\s+", " ", proc.stdout).strip())
    return readings, None


def ocr_language(language: str | None) -> str:
    """Map a project language ("fr", "fr-FR", "fra") to a tesseract code."""
    codes = {"fr": "fra", "en": "eng", "es": "spa", "de": "deu", "it": "ita",
             "pt": "por", "nl": "nld", "zh": "chi_sim", "ja": "jpn", "ko": "kor"}
    if not language:
        return "eng"
    lang = language.strip().lower()
    return codes.get(lang.split("-")[0].split("_")[0], lang)


def _letters(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def ocr_frame(path: Path, lang: str = "eng") -> tuple[str | None, str | None]:
    """Best reading (most letters), or (None, reason) when OCR could not run."""
    readings, reason = ocr_readings(path, lang)
    if readings is None:
        return None, reason
    return max(readings, key=_letters), None


# --- Checks ---------------------------------------------------------------------


def _compare(a: Path, b: Path) -> dict[str, Any]:
    ssim = frame_ssim(a, b)
    jump = abs(frame_luma(a) - frame_luma(b))
    cfg = settings()
    ok = ssim is not None and ssim >= cfg.seam_min_ssim and jump <= cfg.seam_max_luma_jump
    return {"ssim": None if ssim is None else round(ssim, 4),
            "luma_jump": round(jump, 2), "ok": ok if ssim is not None else None}


def check_seams(video: Path, segments: list[dict[str, Any]], work_dir: Path,
                fps: float = 24.0) -> dict[str, Any]:
    """SSIM + luminance on both sides of every continuity cut."""
    result: dict[str, Any] = {"seams": [], "issues": []}
    half_frame = 0.5 / (fps or 24.0)
    for i, pair in enumerate(continuity_pairs(segments)):
        before = extract_frame(video, pair["at"] - 3 * half_frame, work_dir / f"seam_{i}_a.png")
        after = extract_frame(video, pair["at"] + half_frame, work_dir / f"seam_{i}_b.png")
        if not before or not after:
            seam = {**pair, "ok": None, "reason": "frame extraction failed"}
        else:
            seam = {**pair, **_compare(before, after)}
        result["seams"].append(seam)
        if seam["ok"] is False:
            result["issues"].append(
                f"Visible seam at {pair['at']:.2f}s ({pair['from']} -> {pair['to']}): "
                f"SSIM {seam['ssim']}, luminance jump {seam['luma_jump']}"
            )
        elif seam["ok"] is None:
            result["issues"].append(f"Seam at {pair['at']:.2f}s not checked: {seam.get('reason')}")
    return result


def check_loop(video: Path, work_dir: Path) -> dict[str, Any]:
    first = extract_frame(video, 0.0, work_dir / "loop_first.png")
    last = extract_last_frame(video, work_dir / "loop_last.png")
    if not first or not last:
        return {"ok": None, "reason": "frame extraction failed"}
    return _compare(last, first)


def _silences(video: Path) -> tuple[list[tuple[float, float]], float | None]:
    cmd = ["ffmpeg", "-v", "info", "-i", str(video), "-vn", "-af",
           f"silencedetect=noise={settings().silence_noise_db}dB:d={settings().silence_min_seconds}", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    err = proc.stderr or ""
    starts = [float(x) for x in re.findall(r"silence_start: (-?[0-9.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", err)]
    dur = re.search(r"Duration: (\d+):(\d+):([0-9.]+)", err)
    duration = None
    if dur:
        h, m, s = dur.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)
    if len(ends) < len(starts) and duration is not None:
        ends.append(duration)
    return list(zip(starts, ends)), duration


def detect_music(video: Path) -> dict[str, Any]:
    """A music bed leaves no digital silence between phrases."""
    silences, duration = _silences(video)
    if not duration:
        return {"music_present": None, "reason": "could not read audio duration"}
    silent = sum(max(e - s, 0.0) for s, e in silences)
    ratio = silent / duration
    return {
        "music_present": ratio <= settings().music_max_silence_ratio,
        "silence_ratio": round(ratio, 3),
        "silences": [[round(s, 2), round(e, 2)] for s, e in silences[:20]],
    }


def loudness(video: Path) -> dict[str, Any]:
    """Integrated loudness (LUFS) and true peak (dBTP) of the audio track."""
    cmd = ["ffmpeg", "-v", "info", "-nostats", "-i", str(video), "-vn",
           "-af", "ebur128=peak=true", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    summary = (proc.stderr or "").rsplit("Summary:", 1)
    if len(summary) < 2:
        return {"integrated_lufs": None, "reason": "ebur128 printed no summary (no audio stream?)"}
    i = re.search(r"I:\s+(-?[0-9.]+|-inf) LUFS", summary[1])
    peak = re.search(r"Peak:\s+(-?[0-9.]+|-inf) dBFS", summary[1])
    value = lambda m: None if not m or m.group(1) == "-inf" else float(m.group(1))
    lufs = value(i)
    if lufs is not None and lufs <= -69.9:
        # -70 LUFS is ebur128's absolute gate: nothing loud enough to measure
        return {"integrated_lufs": None, "true_peak_dbtp": value(peak),
                "reason": "no measurable loudness (under the -70 LUFS gate: silent)"}
    return {"integrated_lufs": lufs, "true_peak_dbtp": value(peak)}


def window_volume(video: Path, start: float, end: float) -> float | None:
    """Mean volume (dB) of the audio between start and end."""
    cmd = ["ffmpeg", "-v", "info", "-nostats", "-ss", f"{max(start, 0.0):.3f}", "-to", f"{end:.3f}",
           "-i", str(video), "-vn", "-af", "volumedetect", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    m = re.search(r"mean_volume:\s+(-?[0-9.]+|-inf) dB", proc.stderr or "")
    return None if not m or m.group(1) == "-inf" else float(m.group(1))


def check_last_sentence(
    video: Path,
    start: float,
    end: float,
    duration: float,
    *,
    last_word: tuple[float, float] | None = None,
    expected_text: str | None = None,
    transcribe: Any = None,
) -> dict[str, Any]:
    """The last narrated sentence must end inside the video and be HEARD.

    Volume alone cannot tell: a voice cut under a music bed keeps a normal
    level. When ``expected_text`` and a ``transcribe(wav_path) -> words``
    callable are given, the end of the final mix is transcribed and the last
    sentence must be found in it. The level check measures the last WORD
    (``last_word``), where a fade bites hardest, or the last second.
    """
    cfg = settings()
    out: dict[str, Any] = {"start": round(start, 2), "end": round(end, 2), "duration": round(duration, 2),
                           "issues": [], "not_checked": {}}
    if end > duration - cfg.last_sentence_min_margin_s:
        out["issues"].append(
            f"Last sentence ends at {end:.2f}s but the video ends at {duration:.2f}s: it is cut")
    whole = window_volume(video, 0.0, duration)
    tail_end = min(end, duration)
    w0, w1 = last_word if last_word else (max(start, tail_end - 1.0), tail_end)
    w1 = min(w1, duration)
    last = window_volume(video, w0, w1) if w1 > w0 else None
    out["mix_mean_db"], out["last_word_mean_db"] = whole, last
    if whole is None or last is None:
        out["not_checked"]["level"] = "could not measure the volume of the last word"
    elif whole - last > cfg.last_sentence_max_drop_db:
        out["issues"].append(
            f"Last word is {whole - last:.1f} dB under the mix: faded out or inaudible")
    if expected_text and transcribe is not None:
        try:
            from lib.script_timing import time_script

            wav = Path(video).with_name(Path(video).stem + ".last_sentence.wav")
            a, b = max(0.0, start - 0.3), min(duration, end + 0.6)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}",
                            "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                           check=True, capture_output=True, timeout=120)
            words = transcribe(wav)
            wav.unlink(missing_ok=True)
            report = time_script(expected_text, words)["report"]
            out["heard"] = " ".join(str(w.get("word", "")).strip() for w in words)
            out["heard_fidelity"] = report["fidelity"]
            if report["fidelity"] < cfg.last_sentence_min_heard:
                out["issues"].append(
                    f"Last sentence not heard in the final mix (fidelity {report['fidelity']:.2f}): "
                    f"expected {expected_text!r}, heard {out['heard']!r}")
        except Exception as exc:
            out["not_checked"]["heard"] = f"could not transcribe the end of the mix: {type(exc).__name__}: {exc}"
    else:
        out["not_checked"]["heard"] = "no expected text or no transcriber for the end of the mix"
    return out
