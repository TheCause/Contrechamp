"""remotion_caption_burn: what it hands to the TalkingHead composition.

No node here: every test stops at the props the tool writes, which is where
the caption bugs were. Each fix is pinned by a test that fails on the code
before it, next to a test that stays silent on a healthy input.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib import render_checks as rc  # noqa: E402
from tools.video.remotion_caption_burn import RemotionCaptionBurn  # noqa: E402

needs_tesseract = pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")


def _words(*items):
    return [{"word": w, "start": s, "end": s + 0.3} for w, s in items]


def _breaks(captions):
    return [(c["word"], bool(c.get("pageBreakAfter"))) for c in captions]


# --- Page breaks at the end of a segment (b4095ac) ----------------------------


def test_words_path_breaks_the_page_at_each_segment_end():
    segments = [
        {"words": _words(("Every", 0.0), ("rider", 0.3), ("goes", 0.6), ("down", 0.9))},
        {"words": _words(("Not", 1.5), ("every", 1.8), ("rider", 2.1))},
    ]
    caps = RemotionCaptionBurn()._segments_to_word_captions(segments)
    assert caps[3]["word"] == "down" and caps[3].get("pageBreakAfter") is True
    assert caps[-1].get("pageBreakAfter") is True


def test_text_path_breaks_the_page_at_each_segment_end():
    segments = [
        {"text": "Every rider goes down", "start": 0.0, "end": 1.2},
        {"text": "Not every rider", "start": 1.5, "end": 2.4},
    ]
    caps = RemotionCaptionBurn()._segments_to_word_captions(segments)
    assert caps[3]["word"] == "down" and caps[3].get("pageBreakAfter") is True
    assert caps[-1].get("pageBreakAfter") is True


def test_no_page_break_inside_a_sentence():
    segments = [{"words": _words(("Every", 0.0), ("rider,", 0.3), ("goes", 0.6), ("down", 0.9))}]
    caps = RemotionCaptionBurn()._segments_to_word_captions(segments)
    assert _breaks(caps) == [("Every", False), ("rider,", False), ("goes", False), ("down", True)]


# --- staticFile() path without the public/ prefix (b4095ac) -------------------


def test_props_video_src_is_relative_to_public(tmp_path, monkeypatch):
    root = tmp_path / "remotion-composer"
    root.mkdir()
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stub")
    out = tmp_path / "out.mp4"

    def fake_run(self, cmd, **kwargs):
        stdout = ""
        if cmd[0] == "ffprobe":
            stdout = "1080x1920\n" if "stream=width,height" in cmd else "3.0\n"
        else:
            out.write_bytes(b"stub")
        return type("R", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()

    monkeypatch.setattr(RemotionCaptionBurn, "run_command", fake_run, raising=False)
    monkeypatch.setattr(RemotionCaptionBurn, "_find_remotion_root", lambda self: root)
    result = RemotionCaptionBurn()._render_remotion(
        str(video), str(out), [{"word": "Bonjour", "startMs": 0, "endMs": 300}],
        4, 52, "#22D3EE",
    )
    assert result.success, result.error
    props = json.loads((root / "public" / "demo-props" / "caption-burn-clip.json").read_text())
    assert not props["videoSrc"].startswith("public/"), props["videoSrc"]
    assert props["videoSrc"] == "talking-head/clip.mp4"
    assert (root / "public" / props["videoSrc"]).exists()


# --- Page breaks at sentence ends, and on SRT cues -----------------------------


def test_sentence_end_inside_a_segment_breaks_the_page():
    # a Whisper segment is not a sentence: "voit." must not share a page with "Oui"
    segments = [{"words": _words(("L'homme", 0.0), ("qu'il", 0.3), ("voit.", 0.6), ("Oui", 0.9))}]
    caps = RemotionCaptionBurn()._segments_to_word_captions(segments)
    assert _breaks(caps) == [("L'homme", False), ("qu'il", False), ("voit.", True), ("Oui", True)]


def test_text_path_breaks_after_each_sentence():
    segments = [{"text": "Bonjour à tous. Ça va bien", "start": 0.0, "end": 2.0}]
    caps = RemotionCaptionBurn()._segments_to_word_captions(segments)
    assert _breaks(caps) == [
        ("Bonjour", False), ("à", False), ("tous.", True),
        ("Ça", False), ("va", False), ("bien", True),
    ]


def test_abbreviations_do_not_break_the_page():
    segments = [{"text": "M. Dupont et J. Martin arrivent", "start": 0.0, "end": 2.0}]
    caps = RemotionCaptionBurn()._segments_to_word_captions(segments)
    assert [w for w, b in _breaks(caps) if b] == ["arrivent"]


def test_srt_breaks_the_page_at_each_cue(tmp_path):
    srt = tmp_path / "subs.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nUn deux\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nTrois quatre\n",
        encoding="utf-8",
    )
    caps = RemotionCaptionBurn()._srt_to_word_captions(str(srt))
    assert _breaks(caps) == [("Un", False), ("deux", True), ("Trois", False), ("quatre", True)]


# --- Stray punctuation tokens (French typography) ------------------------------


def test_isolated_punctuation_sticks_to_its_word():
    segments = [{"text": "Alors c'est bien vrai ? Oui , « vraiment » !", "start": 0.0, "end": 3.0}]
    caps = RemotionCaptionBurn()._segments_to_word_captions(segments)
    words = [c["word"] for c in caps]
    assert words == ["Alors", "c'est", "bien", "vrai\u202f?", "Oui,", "«\u202fvraiment\u202f»\u202f!"]
    assert caps[3]["pageBreakAfter"] is True  # "vrai ?" ends the sentence
    assert caps[3]["endMs"] > caps[3]["startMs"]


def test_ordinary_words_are_left_alone():
    segments = [{"text": "Every rider goes down", "start": 0.0, "end": 1.2}]
    caps = RemotionCaptionBurn()._segments_to_word_captions(segments)
    assert [c["word"] for c in caps] == ["Every", "rider", "goes", "down"]


# --- The rendered captions are read back (OCR), or say they were not ----------

CAPTIONS = [
    {"word": w, "startMs": i * 300, "endMs": (i + 1) * 300, **({"pageBreakAfter": True} if i == 3 else {})}
    for i, w in enumerate(["Every", "rider", "goes", "down"])
]


def _review_with(monkeypatch, tmp_path, readings=None, reason=None, frame=True):
    out = tmp_path / "out.mp4"
    out.write_bytes(b"stub")

    def fake_extract(video, t, dest):
        if not frame:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"png")
        return dest

    monkeypatch.setattr(rc, "extract_frame", fake_extract)
    monkeypatch.setattr(rc, "ocr_readings", lambda path, lang="eng": (readings, reason))
    return RemotionCaptionBurn()._review_captions(str(out), CAPTIONS, language="en")


def test_glued_caption_on_the_rendered_frame_is_reported(tmp_path, monkeypatch):
    review = _review_with(monkeypatch, tmp_path, readings=["EVERYRIDERGOES DOWN"])
    assert review["status"] == "revise"
    assert "EVERYRIDERGOES" in review["glued_words"]
    assert review["frames_checked"] >= 1


def test_spaced_caption_on_the_rendered_frame_passes(tmp_path, monkeypatch):
    review = _review_with(monkeypatch, tmp_path, readings=["EVERY RIDER GOES DOWN"])
    assert review["status"] == "pass", review
    assert review["glued_words"] == []


def test_missing_ocr_is_not_checked_never_pass(tmp_path, monkeypatch):
    review = _review_with(monkeypatch, tmp_path, reason="tesseract not installed")
    assert review["status"] == "not_checked"
    assert "tesseract" in review["reason"]


def test_no_frame_extracted_is_not_checked(tmp_path, monkeypatch):
    review = _review_with(monkeypatch, tmp_path, frame=False)
    assert review["status"] == "not_checked"
    assert review["reason"]


def test_execute_attaches_the_caption_review(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stub")
    out = tmp_path / "out.mp4"

    def fake_ffmpeg(self, input_path, output_path, captions):
        Path(output_path).write_bytes(b"stub")
        from tools.base_tool import ToolResult
        return ToolResult(success=True, data={"method": "ffmpeg_fallback"}, artifacts=[output_path])

    monkeypatch.setattr(RemotionCaptionBurn, "_render_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(rc, "ocr_readings", lambda path, lang="eng": (None, "tesseract not installed"))
    monkeypatch.setattr(rc, "extract_frame", lambda v, t, dest: dest)
    result = RemotionCaptionBurn().execute({
        "input_path": str(video), "output_path": str(out), "force_ffmpeg": True,
        "segments": [{"text": "Every rider goes down", "start": 0.0, "end": 1.2}],
    })
    assert result.success, result.error
    assert result.data["caption_review"]["status"] == "not_checked"


@needs_tesseract
def test_real_ocr_catches_a_short_glued_page(tmp_path, monkeypatch):
    from tests.lib.test_render_checks import _frame
    glued = _frame(tmp_path / "glued.png", "EVERYRIDERGOESDOWN")
    spaced = _frame(tmp_path / "spaced.png", "EVERY RIDER GOES DOWN")
    out = tmp_path / "out.mp4"
    out.write_bytes(b"stub")
    for image, status in ((glued, "revise"), (spaced, "pass")):
        def fake_extract(video, t, dest, image=image):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(image, dest)
            return dest
        monkeypatch.setattr(rc, "extract_frame", fake_extract)
        review = RemotionCaptionBurn()._review_captions(str(out), CAPTIONS, language="en")
        assert review["status"] == status, review
