"""Key-phrase cards: exact script sentences, timed on the voice, read back by OCR."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib import key_phrases as kp  # noqa: E402

TIMING = {"sentences": [
    {"index": 0, "text": "La ruche pense pour nous.", "start": 1.0, "end": 2.2},
    {"index": 1, "text": "Rester immunisé, c'est choisir.", "start": 2.3, "end": 5.0},
    {"index": 2, "text": "Voilà.", "start": 5.1, "end": 5.6},
]}


def test_cards_take_the_exact_sentence_and_its_voice_times():
    r = kp.plan(TIMING, ["Rester immunisé, c'est choisir."])
    assert r["overlays"] == [{"type": "key_phrase", "text": "Rester immunisé, c'est choisir.",
                              "in_seconds": 2.3, "out_seconds": 5.0}]
    assert r["expected_text"] == [{"text": "Rester immunisé, c'est choisir.",
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
        kp.plan(TIMING, ["Rester immunisé c'est choisir."])  # comma dropped


def test_inserts_shift_the_cards_to_final_time():
    r = kp.plan(TIMING, [1], inserts=[{"at": 2.0, "duration": 4.0}])
    assert (r["overlays"][0]["in_seconds"], r["overlays"][0]["out_seconds"]) == (6.3, 9.0)
