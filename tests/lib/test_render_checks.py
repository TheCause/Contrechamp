"""Render checks that must bite: each check has a case where it MUST fail
and a healthy case where it MUST stay silent. Fixtures are synthetic
(Pillow frames + ffmpeg), never real production content."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from lib import render_checks as rc

FFMPEG = shutil.which("ffmpeg")
TESSERACT = shutil.which("tesseract")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
needs_tesseract = pytest.mark.skipif(not TESSERACT, reason="tesseract not installed")

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _frame(path: Path, text: str = "", shade: int = 128, pattern: int = 0) -> Path:
    """A 1280x360 frame with a textured background and optional text."""
    img = Image.new("RGB", (1280, 360), (shade, shade, shade))
    draw = ImageDraw.Draw(img)
    for i in range(0, 1280, 40):  # texture so SSIM has structure to compare
        offset = (i * 7 + pattern * 97) % 360
        draw.rectangle([i, offset, i + 20, min(offset + 60, 359)], fill=(shade // 2,) * 3)
    if text:
        draw.rectangle([20, 140, 1260, 220], fill=(0, 0, 0))
        draw.text((40, 155), text, font=_font(40), fill=(255, 255, 255))
    img.save(path)
    return path


def _video(out: Path, frames: list[tuple[Path, float]], audio: str | None = None) -> Path:
    """Concatenate still frames (path, seconds) into an mp4; optional lavfi audio.

    Uses the concat *filter* on looped inputs: the concat demuxer does not
    switch images at the declared times, which silently voided seam tests.
    """
    cmd = [FFMPEG, "-y", "-v", "error"]
    for path, seconds in frames:
        cmd += ["-loop", "1", "-framerate", "24", "-t", str(seconds), "-i", str(path)]
    n = len(frames)
    graph = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0,format=yuv420p[v]"
    if audio:
        cmd += ["-f", "lavfi", "-i", audio]
    cmd += ["-filter_complex", graph, "-map", "[v]"]
    if audio:
        cmd += ["-map", f"{n}:a", "-shortest", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-crf", "12", str(out)]
    subprocess.run(cmd, check=True)
    return out


# --- Pure helpers -----------------------------------------------------------


def test_glued_words_flags_captions_without_spaces():
    assert rc.glued_words("chaquechoix.En2016,23laboratoires") != []
    assert rc.glued_words("médecinsprescriventplusd'antibiotiquesinutiles") != []


def test_glued_words_silent_on_normal_text():
    assert rc.glued_words("chaque choix. En 2016, 23 laboratoires") == []
    assert rc.glued_words("incompréhensiblement internationalisation") == []  # 20 letters each


def test_glued_words_catches_the_real_broken_caption_lines():
    # the two lines of a real broken render (22 and 24 letters, no space)
    assert rc.glued_words("médecinsprescriventplusd") != []
    assert rc.glued_words("'antibiotiquesinutiles") != []


def test_text_similarity_exact_mode_catches_one_word_change():
    expected = "Pour vous, vous vous prescrivez des limites."
    altered = "Pour vous, vous vous assignez des limites."
    assert rc.text_similarity(expected, expected, exact=True) == 1.0
    assert rc.text_similarity(expected, altered, exact=True) < 1.0


def test_text_similarity_loose_mode_ignores_case_and_spacing():
    assert rc.text_similarity("Hello  World", "hello world") == 1.0


def test_timeline_segments_accumulate_durations_whatever_in_out_means():
    # in/out as source trims (both segments start at 0)
    source_style = [
        {"id": "a", "source": "clip", "in_seconds": 0, "out_seconds": 28.0},
        {"id": "b", "source": "hold", "in_seconds": 0, "out_seconds": 9.0},
    ]
    segs = rc.timeline_segments(source_style)
    assert [(s["start"], s["end"]) for s in segs] == [(0.0, 28.0), (28.0, 37.0)]
    # in/out as timeline positions, with speed
    timeline_style = [
        {"id": "a", "source": "x", "in_seconds": 0, "out_seconds": 5},
        {"id": "b", "source": "y", "in_seconds": 5, "out_seconds": 11, "speed": 2.0},
    ]
    segs = rc.timeline_segments(timeline_style)
    assert [(s["start"], s["end"]) for s in segs] == [(0.0, 5.0), (5.0, 8.0)]


def test_continuity_pairs_only_same_source_or_declared():
    segs = rc.timeline_segments([
        {"id": "a", "source": "clip", "in_seconds": 0, "out_seconds": 4},
        {"id": "b", "source": "clip", "in_seconds": 4, "out_seconds": 6},
        {"id": "c", "source": "other", "in_seconds": 0, "out_seconds": 2},
        {"id": "d", "source": "hold", "in_seconds": 0, "out_seconds": 2, "continuity": True},
    ])
    assert [(p["from"], p["to"]) for p in rc.continuity_pairs(segs)] == [("a", "b"), ("c", "d")]


def test_sample_times_one_per_segment_midpoint():
    segs = rc.timeline_segments([
        {"id": "a", "source": "x", "in_seconds": 0, "out_seconds": 4},
        {"id": "b", "source": "y", "in_seconds": 0, "out_seconds": 2},
    ])
    assert rc.sample_times(segs, duration=6.0) == [("a", 2.0), ("b", 5.0)]
    # no cuts -> historical 4 points
    assert [t for _, t in rc.sample_times([], duration=10.0)] == [1.0, 3.5, 6.5, 9.0]


# --- OCR --------------------------------------------------------------------


@needs_tesseract
def test_ocr_reads_text_and_glued_caption_is_caught(tmp_path):
    good = _frame(tmp_path / "good.png", "EVERY CHOICE WEARS YOU")
    glued = _frame(tmp_path / "glued.png", "EVERYCHOICEWEARSYOUDOWNTODAY")
    text, reason = rc.ocr_frame(good, lang="eng")
    assert reason is None and "CHOICE" in text.upper()
    assert rc.glued_words(text) == []
    text, _ = rc.ocr_frame(glued, lang="eng")
    assert rc.glued_words(text) != []


@needs_tesseract
def test_ocr_reads_grey_and_cyan_caption_on_translucent_box(tmp_path):
    # the real broken render: light-grey words, one cyan highlighted word,
    # dark translucent box over a busy picture — unreadable at a 200 threshold
    path = tmp_path / "caption.png"
    img = Image.new("RGB", (1280, 360), (150, 150, 160))
    draw = ImageDraw.Draw(img)
    for i in range(0, 1280, 30):
        draw.line([(i, 0), (i + 200, 360)], fill=(90, 90, 100), width=6)
    draw.rectangle([40, 130, 1240, 230], fill=(45, 50, 65))
    font = _font(44)
    draw.text((70, 152), "leglutamate", font=font, fill=(190, 190, 195))
    x = 70 + draw.textlength("leglutamate", font=font)
    draw.text((x, 152), "saccumuleala", font=font, fill=(0, 190, 230))
    img.save(path)
    text, reason = rc.ocr_frame(path, lang="eng")
    assert reason is None
    readings, _ = rc.ocr_readings(path, lang="eng")
    assert any(rc.glued_words(r) for r in readings), readings


def test_ocr_missing_tesseract_returns_none_with_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(rc.shutil, "which", lambda name: None)
    text, reason = rc.ocr_frame(tmp_path / "x.png", lang="eng")
    assert text is None and "tesseract" in reason


@needs_tesseract
def test_ocr_missing_language_returns_none_with_reason(tmp_path):
    frame = _frame(tmp_path / "f.png", "HELLO")
    text, reason = rc.ocr_frame(frame, lang="zzz_not_a_language")
    assert text is None and "zzz_not_a_language" in reason


# --- Seams and loop ---------------------------------------------------------


@needs_ffmpeg
def test_seam_check_flags_darkened_hold(tmp_path):
    a = _frame(tmp_path / "a.png", shade=140)
    dark = _frame(tmp_path / "dark.png", shade=131)  # 9 luma points darker
    video = _video(tmp_path / "v.mp4", [(a, 1.0), (dark, 1.0)])
    segs = rc.timeline_segments([
        {"id": "clip", "source": "s", "in_seconds": 0, "out_seconds": 1},
        {"id": "hold", "source": "s", "in_seconds": 1, "out_seconds": 2},
    ])
    result = rc.check_seams(video, segs, work_dir=tmp_path / "w")
    assert result["seams"][0]["ok"] is False
    assert result["issues"]


@needs_ffmpeg
def test_seam_check_flags_wrong_frame(tmp_path):
    a = _frame(tmp_path / "a.png", shade=140, pattern=0)
    other = _frame(tmp_path / "o.png", shade=140, pattern=3)
    video = _video(tmp_path / "v.mp4", [(a, 1.0), (other, 1.0)])
    segs = rc.timeline_segments([
        {"id": "clip", "source": "s", "in_seconds": 0, "out_seconds": 1},
        {"id": "hold", "source": "t", "in_seconds": 0, "out_seconds": 1, "continuity": True},
    ])
    result = rc.check_seams(video, segs, work_dir=tmp_path / "w")
    assert result["seams"][0]["ok"] is False


@needs_ffmpeg
def test_seam_check_silent_on_exact_hold(tmp_path):
    a = _frame(tmp_path / "a.png", shade=140)
    video = _video(tmp_path / "v.mp4", [(a, 1.0), (a, 1.0)])
    segs = rc.timeline_segments([
        {"id": "clip", "source": "s", "in_seconds": 0, "out_seconds": 1},
        {"id": "hold", "source": "s", "in_seconds": 1, "out_seconds": 2},
    ])
    result = rc.check_seams(video, segs, work_dir=tmp_path / "w")
    assert result["seams"][0]["ok"] is True
    assert result["issues"] == []


@needs_ffmpeg
def test_loop_check_flags_broken_loop_and_passes_clean_loop(tmp_path):
    a = _frame(tmp_path / "a.png", pattern=0)
    b = _frame(tmp_path / "b.png", pattern=5)
    broken = _video(tmp_path / "broken.mp4", [(a, 1.0), (b, 1.0)])
    clean = _video(tmp_path / "clean.mp4", [(a, 1.0), (b, 1.0), (a, 1.0)])
    assert rc.check_loop(broken, work_dir=tmp_path / "w1")["ok"] is False
    assert rc.check_loop(clean, work_dir=tmp_path / "w2")["ok"] is True


# --- Music --------------------------------------------------------------------

SPEECH_WITH_GAPS = (
    "sine=f=440:d=1,apad=pad_dur=0.6[a];sine=f=550:d=1,apad=pad_dur=0.6[b];"
    "sine=f=660:d=1[c];[a][b][c]concat=n=3:v=0:a=1"
)


@needs_ffmpeg
def test_music_absent_when_gaps_are_digital_silence(tmp_path):
    frame = _frame(tmp_path / "f.png")
    video = _video(tmp_path / "v.mp4", [(frame, 3.2)], audio=SPEECH_WITH_GAPS)
    result = rc.detect_music(video)
    assert result["music_present"] is False
    assert result["silence_ratio"] > 0.05


@needs_ffmpeg
def test_music_present_when_a_bed_fills_the_gaps(tmp_path):
    frame = _frame(tmp_path / "f.png")
    bed = f"{SPEECH_WITH_GAPS}[v];anoisesrc=d=3.2:a=0.03[n];[v][n]amix=inputs=2:normalize=0"
    video = _video(tmp_path / "v.mp4", [(frame, 3.2)], audio=bed)
    result = rc.detect_music(video)
    assert result["music_present"] is True
