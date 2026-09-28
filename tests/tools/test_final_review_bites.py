"""The final review must tell a broken render from a good one.

Each test renders a small synthetic video with one known defect (or none)
and checks the verdict. Before the fork, all of these defects got
status "pass" with reassuring defaults."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lib import render_checks as rc
from schemas.artifacts import validate_artifact
from tests.lib.test_render_checks import SPEECH_WITH_GAPS, _frame, _video
from tools.video.video_compose import VideoCompose

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
needs_tesseract = pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")

BED = f"{SPEECH_WITH_GAPS}[v];anoisesrc=d=3.2:a=0.03[n];[v][n]amix=inputs=2:normalize=0"


def _cuts(*spec):
    return [{"id": i, "source": s, "in_seconds": a, "out_seconds": b} for i, s, a, b in spec]


def _review(video: Path, edit_decisions: dict) -> dict:
    review = VideoCompose()._run_final_review(output_path=video, edit_decisions=edit_decisions)
    validate_artifact("final_review", review)
    return review


def _two_shots(tmp_path, audio=BED):
    a = _frame(tmp_path / "a.png", pattern=0)
    b = _frame(tmp_path / "b.png", pattern=5)  # SSIM 0.85 with a: a different shot
    return _video(tmp_path / "v.mp4", [(a, 1.6), (b, 1.6)], audio=audio)


@needs_tesseract
def test_clean_render_passes_with_every_check_measured(tmp_path):
    video = _two_shots(tmp_path)
    ed = {"cuts": _cuts(("s1", "x", 0, 1.6), ("s2", "y", 0, 1.6)),
          "music": {"track": "bed"}, "metadata": {"language": "en"}}
    review = _review(video, ed)
    assert review["status"] == "pass", review["issues_found"]
    visual = review["checks"]["visual_spotcheck"]
    assert visual["frames_sampled"] == 2
    assert visual["unreadable_text"] is False
    assert review["checks"]["audio_spotcheck"]["music_present"] is True


def test_any_open_issue_blocks_pass(tmp_path):
    # a duration drift used to be a non-"critical" issue that still passed
    video = _two_shots(tmp_path)
    ed = {"cuts": _cuts(("s1", "x", 0, 1.6), ("s2", "y", 0, 1.6)),
          "total_duration_seconds": 10}
    review = _review(video, ed)
    assert any("Duration drift" in i for i in review["issues_found"])
    assert review["status"] == "revise"


@needs_tesseract
def test_glued_caption_is_unreadable_text(tmp_path):
    glued = _frame(tmp_path / "g.png", "EVERYCHOICEWEARSYOUDOWNTODAY")
    video = _video(tmp_path / "v.mp4", [(glued, 3.2)], audio=BED)
    ed = {"cuts": _cuts(("s1", "x", 0, 3.2)), "music": {"track": "bed"},
          "subtitles": {"enabled": True}, "metadata": {"language": "en"}}
    review = _review(video, ed)
    visual = review["checks"]["visual_spotcheck"]
    assert visual["unreadable_text"] is True
    assert visual["ocr_readings"][0]["text"]
    assert review["status"] == "revise"


@needs_tesseract
def test_expected_text_altered_by_one_word_is_caught_in_exact_mode(tmp_path):
    shown = _frame(tmp_path / "c.png", "YOU SET YOUR OWN LIMITS")
    video = _video(tmp_path / "v.mp4", [(shown, 3.2)], audio=BED)
    base = {"cuts": _cuts(("s1", "x", 0, 3.2)), "music": {"track": "bed"}}
    wrong = {**base, "metadata": {"language": "en", "expected_text": [
        {"start_seconds": 0, "end_seconds": 3.2, "text": "YOU SET YOUR OWN BOUNDS", "exact": True}]}}
    right = {**base, "metadata": {"language": "en", "expected_text": [
        {"start_seconds": 0, "end_seconds": 3.2, "text": "YOU SET YOUR OWN LIMITS", "exact": True}]}}
    assert _review(video, wrong)["checks"]["visual_spotcheck"]["unreadable_text"] is True
    good = _review(video, right)
    assert good["checks"]["visual_spotcheck"]["unreadable_text"] is False
    assert good["status"] == "pass", good["issues_found"]


@needs_tesseract
def test_short_glued_caption_page_is_caught_against_expected_text(tmp_path):
    # one 4-word caption page with its spaces lost: 18 letters, under the
    # 21-letter threshold, and a loose similarity of 0.92 with the expected text
    expected = {"start_seconds": 0, "end_seconds": 3.2, "text": "EVERY RIDER GOES DOWN"}
    base = {"cuts": _cuts(("s1", "x", 0, 3.2)), "music": {"track": "bed"},
            "metadata": {"language": "en", "expected_text": [expected]}}
    glued = _video(tmp_path / "g.mp4", [(_frame(tmp_path / "g.png", "EVERYRIDERGOESDOWN"), 3.2)], audio=BED)
    review = _review(glued, base)
    assert review["checks"]["visual_spotcheck"]["unreadable_text"] is True, review["checks"]["visual_spotcheck"]
    assert review["status"] == "revise"
    spaced = _video(tmp_path / "s.mp4", [(_frame(tmp_path / "s.png", "EVERY RIDER GOES DOWN"), 3.2)], audio=BED)
    good = _review(spaced, base)
    assert good["checks"]["visual_spotcheck"]["unreadable_text"] is False, good["issues_found"]
    assert good["status"] == "pass", good["issues_found"]


def test_ocr_unavailable_is_null_with_reason_and_blocks_pass(tmp_path, monkeypatch):
    real_which = shutil.which
    monkeypatch.setattr(rc.shutil, "which", lambda n: None if n == "tesseract" else real_which(n))
    video = _two_shots(tmp_path)
    ed = {"cuts": _cuts(("s1", "x", 0, 1.6), ("s2", "y", 0, 1.6)), "music": {"track": "bed"},
          "subtitles": {"enabled": True}}
    review = _review(video, ed)
    visual = review["checks"]["visual_spotcheck"]
    assert visual["unreadable_text"] is None
    assert "tesseract" in visual["not_checked"]["unreadable_text"]
    assert review["status"] == "revise"


def test_unmeasured_visual_fields_are_null_not_false(tmp_path):
    video = _two_shots(tmp_path)
    review = _review(video, {"cuts": _cuts(("s1", "x", 0, 1.6), ("s2", "y", 0, 1.6))})
    visual = review["checks"]["visual_spotcheck"]
    assert visual["broken_overlays"] is None and "broken_overlays" in visual["not_checked"]
    assert visual["missing_assets"] is None and "missing_assets" in visual["not_checked"]


def test_darkened_hold_is_a_visible_seam(tmp_path):
    a = _frame(tmp_path / "a.png", shade=140)
    dark = _frame(tmp_path / "d.png", shade=131)
    video = _video(tmp_path / "v.mp4", [(a, 1.6), (dark, 1.6)], audio=BED)
    ed = {"cuts": [{"id": "clip", "source": "c", "in_seconds": 0, "out_seconds": 1.6},
                   {"id": "hold", "source": "h", "in_seconds": 0, "out_seconds": 1.6,
                    "continuity": True}],
          "music": {"track": "bed"}}
    review = _review(video, ed)
    seams = review["checks"]["continuity"]["seams"]
    assert seams and seams[0]["ok"] is False
    assert review["status"] == "revise"


def test_broken_loop_is_caught_only_when_a_loop_is_requested(tmp_path):
    video = _two_shots(tmp_path)
    cuts = _cuts(("s1", "x", 0, 1.6), ("s2", "y", 0, 1.6))
    looped = _review(video, {"cuts": cuts, "music": {"track": "bed"}, "metadata": {"loop": True}})
    assert looped["checks"]["continuity"]["loop"]["ok"] is False
    assert looped["status"] == "revise"
    plain = _review(video, {"cuts": cuts, "music": {"track": "bed"}})
    assert plain["checks"]["continuity"]["loop"] is None


def test_music_declared_but_absent_is_an_issue(tmp_path):
    video = _two_shots(tmp_path, audio=SPEECH_WITH_GAPS)
    cuts = _cuts(("s1", "x", 0, 1.6), ("s2", "y", 0, 1.6))
    with_music = _review(video, {"cuts": cuts, "music": {"track": "bed"}})
    audio = with_music["checks"]["audio_spotcheck"]
    assert audio["music_present"] is False
    assert any("music" in i.lower() for i in with_music["issues_found"])
    no_music_planned = _review(video, {"cuts": cuts})
    assert not any("music" in i.lower() for i in no_music_planned["issues_found"])


def test_credits_registry_is_checked_when_declared(tmp_path):
    video = _two_shots(tmp_path)
    registry = {"monetized": False, "entries": [
        {"asset": "presse/photo.jpg", "title": "Photo", "author": "X", "license": "CC-BY-4.0"}]}
    ed = {"cuts": _cuts(("s1", "x", 0, 1.6), ("s2", "y", 0, 1.6)), "music": {"track": "bed"},
          "metadata": {"language": "en", "credits": registry}}
    review = _review(video, ed)
    assert any("requires attribution" in i for i in review["issues_found"])
    assert review["status"] != "pass"


def test_credits_without_registry_are_not_checked_not_passed(tmp_path):
    video = _two_shots(tmp_path)
    ed = {"cuts": _cuts(("s1", "x", 0, 1.6), ("s2", "y", 0, 1.6)), "music": {"track": "bed"},
          "metadata": {"language": "en"}}
    review = _review(video, ed)
    assert "credits" in review["checks"]["credits"]["not_checked"]
