"""Review thresholds come from config.yaml, and a missing OCR can be accepted."""

from __future__ import annotations

import shutil

import pytest

from lib import ffmpeg_caps
from lib import render_checks as rc
from lib.config_model import ContrechampConfig


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"

    def write(text: str):
        path.write_text(text)
        monkeypatch.setattr(rc, "_CONFIG_PATH", path)
        rc.settings.cache_clear()
        return path

    yield write
    rc.settings.cache_clear()


def test_defaults_without_a_review_section(config_file):
    config_file("budget:\n  mode: warn\n")
    assert rc.settings().glued_word_min_len == 21
    assert rc.settings().ocr_required is True


def test_threshold_override_changes_the_verdict(config_file):
    word = "médecinsprescriventplusd"  # 24 letters, glued at the default 21
    config_file("review:\n  glued_word_min_len: 21\n")
    assert rc.glued_words(word) != []
    config_file("review:\n  glued_word_min_len: 30\n")
    assert rc.glued_words(word) == []


def test_out_of_range_threshold_is_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("review:\n  seam_min_ssim: 3\n")
    with pytest.raises(Exception):
        ContrechampConfig.load(path)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_ocr_not_required_records_but_does_not_block(config_file, tmp_path, monkeypatch):
    from tests.lib.test_render_checks import _frame, _video
    from tools.video.video_compose import VideoCompose

    real_which = shutil.which
    monkeypatch.setattr(rc.shutil, "which", lambda n: None if n == "tesseract" else real_which(n))
    video = _video(tmp_path / "v.mp4", [(_frame(tmp_path / "f.png"), 2.0)])
    ed = {"cuts": [{"id": "s", "source": "x", "in_seconds": 0, "out_seconds": 2}],
          "subtitles": {"enabled": True}}

    config_file("review:\n  ocr_required: true\n")
    strict = VideoCompose()._run_final_review(output_path=video, edit_decisions=ed)
    assert any("not checked" in i for i in strict["issues_found"])

    config_file("review:\n  ocr_required: false\n")
    lenient = VideoCompose()._run_final_review(output_path=video, edit_decisions=ed)
    assert not any("On-screen text" in i for i in lenient["issues_found"])
    assert "tesseract" in lenient["checks"]["visual_spotcheck"]["not_checked"]["unreadable_text"]


def test_preflight_warns_when_tesseract_is_missing(monkeypatch):
    monkeypatch.setattr(ffmpeg_caps.shutil, "which", lambda n: None)
    assert "tesseract" in ffmpeg_caps.ocr_warning()
    monkeypatch.setattr(ffmpeg_caps.shutil, "which", lambda n: "/usr/bin/" + n)
    assert ffmpeg_caps.ocr_warning() == ""
