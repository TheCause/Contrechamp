"""Key-phrase cards: exact script sentences, timed on the voice, read back by OCR."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib import key_phrases as kp  # noqa: E402

TIMING = {"sentences": [
    {"index": 0, "text": "La machine pense pour nous.", "start": 1.0, "end": 2.2},
    {"index": 1, "text": "Choisir, c'est renoncer.", "start": 2.3, "end": 5.0},
    {"index": 2, "text": "Voilà.", "start": 5.1, "end": 5.6},
]}


def test_cards_take_the_exact_sentence_and_its_voice_times():
    r = kp.plan(TIMING, ["Choisir, c'est renoncer."])
    assert r["overlays"] == [{"type": "key_phrase", "text": "Choisir, c'est renoncer.",
                              "in_seconds": 2.3, "out_seconds": 5.0}]
    assert r["expected_text"] == [{"text": "Choisir, c'est renoncer.",
                                   "start_seconds": 2.3, "end_seconds": 5.0, "exact": False}]
    assert r["issues"] == []


def test_a_short_sentence_is_held_two_seconds():
    r = kp.plan(TIMING, [2])
    assert r["overlays"][0]["out_seconds"] == pytest.approx(7.1)


def test_a_card_never_overlaps_the_next_one_and_says_when_too_short():
    r = kp.plan(TIMING, [0, 1])
    first, second = r["overlays"]
    assert first["out_seconds"] <= round(second["in_seconds"] - 0.1, 3)
    assert any("shorter than 2.0 s" in i for i in r["issues"])


def test_a_phrase_that_is_not_in_the_script_is_refused():
    with pytest.raises(ValueError, match="not a sentence of the script"):
        kp.plan(TIMING, ["Choisir c'est renoncer."])  # comma dropped


def test_inserts_shift_the_cards_to_final_time():
    r = kp.plan(TIMING, [1], inserts=[{"at": 2.0, "duration": 4.0}])
    assert (r["overlays"][0]["in_seconds"], r["overlays"][0]["out_seconds"]) == (6.3, 9.0)


# --- adversarial review and real run (28 Sept) --------------------------------------


def test_a_card_squeezed_to_nothing_is_not_emitted():
    timing = {"sentences": [
        {"index": 0, "text": "Un.", "start": 1.0, "end": 1.2},
        {"index": 1, "text": "Deux.", "start": 1.25, "end": 3.0}]}
    r = kp.plan(timing, [0, 1])
    assert [o["text"] for o in r["overlays"]] == ["Deux."]
    assert any("Un." in i for i in r["issues"])


def test_a_repeated_sentence_must_be_chosen_by_index():
    timing = {"sentences": [
        {"index": 0, "text": "On recommence.", "start": 1.0, "end": 2.0},
        {"index": 1, "text": "On recommence.", "start": 9.0, "end": 10.0}]}
    with pytest.raises(ValueError, match="appears 2 times"):
        kp.plan(timing, ["On recommence."])
    assert kp.plan(timing, [1])["overlays"][0]["in_seconds"] == 9.0


def test_a_card_never_runs_into_the_insert_that_follows():
    r = kp.plan(TIMING, [0], inserts=[{"at": 2.5, "duration": 4.0}])
    assert r["overlays"][0]["out_seconds"] <= 2.5


def test_a_card_never_runs_past_the_end_of_the_video():
    r = kp.plan(TIMING, [2], video_duration=6.0)
    assert r["overlays"][0]["out_seconds"] <= 6.0
    assert any("shorter than" in i for i in r["issues"])


def test_french_spaces_and_apostrophes_do_not_break_the_match():
    timing = {"sentences": [{"index": 0, "text": "C'est vrai ?", "start": 0.0, "end": 2.5}]}
    for spelling in ("C’est vrai ?", "Cʼest vrai ?", "C'est  vrai ?"):
        assert kp.plan(timing, [spelling])["overlays"]


def test_captions_under_a_card_are_removed_not_shown_twice():
    captions = [{"word": "Choisir,", "startMs": 2300, "endMs": 2600},
                {"word": "c'est", "startMs": 2600, "endMs": 3000},
                {"word": "Voilà.", "startMs": 5100, "endMs": 5600}]
    r = kp.plan(TIMING, [1], captions=captions)
    assert [c["word"] for c in r["captions"]] == ["Voilà."]


def test_the_caption_page_before_a_card_stops_when_the_card_starts():
    # seen on a real render: "où le public l'entend." stayed on screen under the card
    captions = [{"word": "Avant.", "startMs": 500, "endMs": 900},
                {"word": "Choisir,", "startMs": 2300, "endMs": 2600},
                {"word": "Voilà.", "startMs": 5100, "endMs": 5600}]
    kept = kp.plan(TIMING, [1], captions=captions)["captions"]
    before = kept[0]
    assert before["word"] == "Avant." and before["holdUntilMs"] == 2300 and before["pageBreakAfter"]
    assert "holdUntilMs" not in captions[0]  # the caller's list is not mutated
