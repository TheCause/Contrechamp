"""Chapters dated on the FINAL video, where an intro inserted after the hook
shifts everything that follows (a channel's description once carried chapter
times off by the 4 s of its title sequence)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib import chapters as ch  # noqa: E402

SECTIONS = [
    {"id": "hook", "start": 0.0, "end": 6.0},
    {"id": "s1", "start": 6.5, "end": 70.0},
    {"id": "s2", "start": 70.5, "end": 140.0},
    {"id": "s3", "start": 140.5, "end": 200.0},
]
TITLES = {"hook": "Pluribus", "s1": "La ruche", "s2": "Les niveaux", "s3": "Rester immunisé"}


def test_an_intro_after_the_hook_shifts_the_following_chapters():
    r = ch.chapters(SECTIONS, TITLES, inserts=[{"at": 6.0, "duration": 4.0}])
    assert [(c["time"], c["title"]) for c in r["chapters"]] == [
        ("0:00", "Pluribus"), ("0:10", "La ruche"), ("1:14", "Les niveaux"), ("2:24", "Rester immunisé")]
    assert r["issues"] == []
    assert r["description"].splitlines()[1] == "0:10 La ruche"


def test_without_insert_times_are_the_narration_times():
    r = ch.chapters(SECTIONS, TITLES)
    assert [c["time"] for c in r["chapters"]] == ["0:00", "0:06", "1:10", "2:20"]


def test_youtube_rules_are_reported_not_silently_fixed():
    r = ch.chapters(SECTIONS[:2], TITLES)
    assert any("at least 3" in i for i in r["issues"])
    short = [{"id": "hook", "start": 0.0, "end": 5}, {"id": "s1", "start": 5.0, "end": 9},
             {"id": "s2", "start": 9.0, "end": 60}, {"id": "s3", "start": 60, "end": 90}]
    r = ch.chapters(short, TITLES)
    assert any("shorter than 10 s" in i and "hook" in i for i in r["issues"])


def test_a_first_chapter_not_at_zero_is_reported():
    r = ch.chapters(SECTIONS[1:], TITLES)
    assert any("must start at 0:00" in i for i in r["issues"])


def test_final_time_mapping():
    inserts = [{"at": 6.0, "duration": 4.0}, {"at": 100.0, "duration": 2.0}]
    assert ch.to_final_time(5.9, inserts) == 5.9
    assert ch.to_final_time(6.0, inserts) == 10.0
    assert ch.to_final_time(150.0, inserts) == 156.0
    assert ch.format_time(3725) == "1:02:05"


def test_a_hook_whose_first_word_starts_after_zero_still_opens_at_zero():
    # real narrations start a few hundredths of a second in (0.18 s measured)
    sections = [dict(SECTIONS[0], start=0.18)] + SECTIONS[1:]
    r = ch.chapters(sections, TITLES, inserts=[{"at": 6.0, "duration": 4.0}])
    assert r["chapters"][0]["time"] == "0:00" and r["issues"] == []
