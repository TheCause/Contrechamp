"""remotion_caption_burn: what it hands to the TalkingHead composition.

No node here: every test stops at the props the tool writes, which is where
the caption bugs were. Each fix is pinned by a test that fails on the code
before it, next to a test that stays silent on a healthy input.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.video.remotion_caption_burn import RemotionCaptionBurn  # noqa: E402


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
