"""Script timing: the words on screen come from the SCRIPT, the times from the voice.

A transcription error must never reach the screen, and a script passage the
voice skipped must never read as timed.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib import script_timing as st  # noqa: E402


def _heard(*items):
    """(word, start) pairs -> transcriber word_timestamps, 0.25 s per word."""
    return [{"word": f" {w}", "start": s, "end": round(s + 0.25, 3)} for w, s in items]


SCRIPT = "L'homme qu'il voit. Claude répond vite !"


def test_screen_text_comes_from_the_script_not_the_transcription():
    heard = _heard(("l'homme", 0.0), ("qu'il", 0.3), ("voit.", 0.6),
                   ("Clode", 1.2), ("répond", 1.5), ("vite", 1.8))
    t = st.time_script(SCRIPT, heard)
    assert [w["word"] for w in t["words"]] == ["L'homme", "qu'il", "voit.", "Claude", "répond", "vite", "!"]
    claude = t["words"][3]
    assert claude["match"] == "substituted" and claude["start"] == 1.2
    assert t["report"]["status"] == "pass"
    assert t["report"]["fidelity"] < 1.0          # the voice/transcript did differ once


def test_sentences_are_dated_from_their_first_and_last_word():
    heard = _heard(("l'homme", 0.0), ("qu'il", 0.3), ("voit", 0.6),
                   ("claude", 1.2), ("répond", 1.5), ("vite", 1.8))
    t = st.time_script(SCRIPT, heard)
    assert [(s["text"], s["start"], s["end"]) for s in t["sentences"]] == [
        ("L'homme qu'il voit.", 0.0, 0.85),
        ("Claude répond vite !", 1.2, 2.05),
    ]


def test_a_skipped_passage_is_reported_not_silently_timed():
    script = "Un deux trois quatre cinq six sept huit neuf dix."
    heard = _heard(("un", 0.0), ("deux", 0.3), ("dix", 3.0))   # 7 words never heard
    t = st.time_script(script, heard)
    assert t["report"]["status"] == "revise"
    gap = t["report"]["gaps"][0]
    assert gap["text"].startswith("trois") and gap["words"] == 7
    # interpolated words stay between their anchors
    trois = t["words"][2]
    assert trois["match"] == "interpolated" and 0.55 <= trois["start"] < 3.0


def test_extra_heard_words_are_ignored():
    heard = _heard(("euh", 0.0), ("l'homme", 0.2), ("qu'il", 0.5), ("voit", 0.8),
                   ("claude", 1.4), ("répond", 1.7), ("vite", 2.0))
    t = st.time_script(SCRIPT, heard)
    assert "euh" not in [w["word"].lower() for w in t["words"]]
    assert t["words"][0]["start"] == 0.2
    assert t["report"]["status"] == "pass"


def test_sections_carry_their_own_times():
    script = {"sections": [{"id": "intro", "text": "Bonjour à tous."},
                           {"id": "s1", "text": "Voici la suite."}]}
    heard = _heard(("bonjour", 0.0), ("à", 0.3), ("tous", 0.6),
                   ("voici", 2.0), ("la", 2.3), ("suite", 2.6))
    t = st.time_script(script, heard)
    assert [(s["id"], s["start"], s["end"]) for s in t["sections"]] == [
        ("intro", 0.0, 0.85), ("s1", 2.0, 2.85)]


def test_nothing_heard_fails_instead_of_inventing_times():
    t = st.time_script(SCRIPT, [])
    assert t["report"]["status"] == "fail"
    assert t["words"] == []


def test_captions_use_script_words_and_break_at_sentence_ends():
    heard = _heard(("l'homme", 0.0), ("qu'il", 0.3), ("voit", 0.6),
                   ("claude", 1.2), ("répond", 1.5), ("vite", 1.8))
    caps = st.time_script(SCRIPT, heard)["captions"]
    assert [c["word"] for c in caps] == ["L'homme", "qu'il", "voit.", "Claude", "répond", "vite !"]
    assert caps[2]["pageBreakAfter"] is True
    assert caps[0]["startMs"] == 0 and caps[3]["startMs"] == 1200


def test_a_number_said_in_letters_takes_the_span_really_spoken():
    # The channel method writes numbers in letters for the voice, in digits on screen.
    script = "Il y a 7 168 cartes."
    heard = _heard(("il", 0.0), ("y", 0.2), ("a", 0.4), ("sept", 0.6), ("mille", 0.9),
                   ("cent", 1.2), ("soixante-huit", 1.5), ("cartes", 2.0))
    t = st.time_script(script, heard)
    sept, cent68 = t["words"][3], t["words"][4]
    assert (sept["word"], sept["match"], sept["start"]) == ("7", "spanned", 0.6)
    assert (cent68["word"], cent68["match"], cent68["end"]) == ("168", "spanned", 1.75)
    assert t["report"]["status"] == "pass" and t["report"]["gaps"] == []


def test_one_long_skipped_run_is_reported_even_when_coverage_is_high():
    said = [f"mot{i}" for i in range(60)]
    script = " ".join(said) + "."
    heard = _heard(*[(w, i * 0.3) for i, w in enumerate(said) if not 20 <= i < 25])
    t = st.time_script(script, heard)
    assert t["report"]["timing_coverage"] >= 0.9
    assert t["report"]["status"] == "revise"
    assert [g["words"] for g in t["report"]["gaps"]] == [5]


def test_a_short_skipped_run_in_a_well_covered_script_passes():
    said = [f"mot{i}" for i in range(60)]
    heard = _heard(*[(w, i * 0.3) for i, w in enumerate(said) if i != 30])
    t = st.time_script(" ".join(said) + ".", heard)
    assert t["report"]["status"] == "pass"
    assert [g["words"] for g in t["report"]["gaps"]] == [1]
