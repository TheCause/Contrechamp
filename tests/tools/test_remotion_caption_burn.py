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
