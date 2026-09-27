"""Measured post-render checks for the final review.

Every check here either measures something and reports what it read, or
returns ``None`` with a reason. Nothing defaults to a reassuring value: a
check that did not run must say so (see docs/fork/lot1-controles-qui-mordent.md).
"""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

# A caption token with at least this many letters is two or more words glued
# together. Measured on a real broken render: glued lines were 22 and 24
# letters ("medecinsprescriventplusd"), so the bar must sit below 22. Long
# everyday words stay silent at 20 ("incomprehensiblement", "internationalisation").
GLUED_WORD_MIN_LEN = 21

# Continuity seam thresholds (same shot on both sides of the cut).
SEAM_MIN_SSIM = 0.90
SEAM_MAX_LUMA_JUMP = 4.0

# A silence this long below this level between speech means no music bed.
SILENCE_NOISE_DB = -50
SILENCE_MIN_SECONDS = 0.3
MUSIC_MAX_SILENCE_RATIO = 0.05

# Expected text: loose match floor, and below this the text is not there at all.
TEXT_MATCH_MIN = 0.85
TEXT_PRESENT_MIN = 0.5

# Mean luma (0-255) under which a sampled frame is black.
BLACK_FRAME_MAX_LUMA = 6.0

MAX_SAMPLED_SEGMENTS = 24
LEGACY_SAMPLE_POINTS = [0.10, 0.35, 0.65, 0.90]


# --- Pure helpers -------------------------------------------------------------


def glued_words(text: str | None, min_len: int = GLUED_WORD_MIN_LEN) -> list[str]:
    """Tokens that look like several words stuck together."""
    if not text:
        return []
    glued = []
    for token in text.split():
        letters = re.sub(r"[^\w]", "", token)
        if len(letters) >= min_len:
            glued.append(token)
        elif re.search(r"[a-zà-ÿ][.,;:!?][A-Za-zÀ-ÿ0-9]", token):
            # "choix.En2016" — sentence punctuation with no space after it
            glued.append(token)
    return glued


def _normalize(text: str) -> str:
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
    if len(segments) > MAX_SAMPLED_SEGMENTS:
        step = len(segments) / MAX_SAMPLED_SEGMENTS
        picked = [segments[int(i * step)] for i in range(MAX_SAMPLED_SEGMENTS)]
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
OCR_THRESHOLDS = (140, 200)


def _ocr_variants(path: Path, work: Path) -> list[Path]:
    with Image.open(path) as img:
        gray = img.convert("L")
    gray = gray.resize((gray.width * 2, gray.height * 2), Image.LANCZOS)
    work.mkdir(parents=True, exist_ok=True)
    variants = [("gray", gray)]
    for th in OCR_THRESHOLDS:
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
    ok = ssim is not None and ssim >= SEAM_MIN_SSIM and jump <= SEAM_MAX_LUMA_JUMP
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
           f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_SECONDS}", "-f", "null", "-"]
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
        "music_present": ratio <= MUSIC_MAX_SILENCE_RATIO,
        "silence_ratio": round(ratio, 3),
        "silences": [[round(s, 2), round(e, 2)] for s, e in silences[:20]],
    }
