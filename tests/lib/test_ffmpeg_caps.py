"""ffmpeg being on PATH does not mean it can burn subtitles or draw text.

Seen on real installs: Homebrew's default ffmpeg formula ships without
libass/freetype, so the `subtitles` and `drawtext` filters are absent while
preflight reported FFmpeg composition as available."""

from __future__ import annotations

import subprocess

from lib import ffmpeg_caps

FULL = """Filters:
 ... drawtext          V->V       Draw text on top of video frames.
 ... subtitles         V->V       Render text subtitles onto input video.
 TS. ssim              VV->V      Calculate the SSIM between two video streams.
"""
MINIMAL = """Filters:
 TS. ssim              VV->V      Calculate the SSIM between two video streams.
"""


def _fake(monkeypatch, listing):
    ffmpeg_caps.available_filters.cache_clear()
    monkeypatch.setattr(ffmpeg_caps.shutil, "which", lambda n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        ffmpeg_caps.subprocess, "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, listing, ""),
    )


def test_minimal_build_reports_missing_text_filters(monkeypatch):
    _fake(monkeypatch, MINIMAL)
    assert ffmpeg_caps.missing_filters() == ["drawtext", "subtitles"]
    warning = ffmpeg_caps.warning()
    assert "drawtext" in warning and "subtitles" in warning


def test_full_build_is_silent(monkeypatch):
    _fake(monkeypatch, FULL)
    assert ffmpeg_caps.missing_filters() == []
    assert ffmpeg_caps.warning() == ""


def test_burn_subtitles_fails_clearly_without_the_filter(monkeypatch, tmp_path):
    from tools.video.video_compose import VideoCompose

    _fake(monkeypatch, MINIMAL)
    video = tmp_path / "in.mp4"
    video.write_bytes(b"x")
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    result = VideoCompose()._burn_subtitles({"input_path": str(video), "subtitle_path": str(srt)})
    assert not result.success
    assert "subtitles" in result.error and "filter" in result.error
