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
    assert (sept["word"], sept["match"], sept["start"]) == ("7", "spoken_form", 0.6)
    assert (cent68["word"], cent68["match"], cent68["end"]) == ("168", "spoken_form", 1.75)
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


def test_french_elisions_split_by_the_transcriber_are_rejoined():
    # Real faster-whisper large-v3 output on French: "l 'intention", "Aujourd 'hui".
    script = "Mais l'intention d'un auteur. Aujourd'hui, c'est mon parti pris."
    heard = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(
        [" Mais", " l", " 'intention", " d", " 'un", " auteur.", " Aujourd", " 'hui,",
         " c", " 'est", " mon", " parti", " pris."])]
    t = st.time_script(script, heard)
    assert t["report"]["fidelity"] == 1.0, t["report"]
    intention = t["words"][1]
    assert (intention["word"], intention["start"], intention["end"]) == ("l'intention", 0.3, 0.85)


# --- adversarial review (28 Sept) ------------------------------------------------


def _said(words, t0=0.0, step=0.3):
    return [{"word": f" {w}", "start": round(t0 + i * step, 3), "end": round(t0 + i * step + 0.25, 3)}
            for i, w in enumerate(words)]


def test_a_script_said_entirely_wrong_is_not_pass():
    script = "Le chat dort sur le canapé rouge depuis ce matin."
    for heard in (_said("il pleut fort à Lyon et les bus sont en retard".split()),   # 11 words
                  _said("il pleut fort à Lyon les bus sont en retard".split())):     # 10 words
        t = st.time_script(script, heard)
        assert t["report"]["status"] == "revise", t["report"]


def test_a_passage_replaced_by_one_word_is_a_gap():
    script = "Voici la partie très importante sur la sécurité des données personnelles."
    heard = _said("voici la suite".split())
    t = st.time_script(script, heard)
    assert t["report"]["status"] == "revise"
    assert max(g["words"] for g in t["report"]["gaps"]) >= 8


def test_words_added_by_the_voice_are_reported():
    base = [f"mot{i}" for i in range(40)]
    looped = base[:20] + ["et", "puis"] * 6 + base[20:]
    t = st.time_script(" ".join(base) + ".", _said(looped))
    assert t["report"]["status"] == "revise"
    assert t["report"]["extras"][0]["words"] == 12


def test_a_filler_word_is_not_an_extra():
    base = [f"mot{i}" for i in range(20)]
    t = st.time_script(" ".join(base) + ".", _said(base[:10] + ["euh"] + base[10:]))
    assert t["report"]["status"] == "pass" and t["report"]["extras"] == []


def test_a_repeated_passage_is_aligned_on_the_right_occurrence():
    refrain = "le modèle lit le texte et le modèle répond".split()
    script_words = refrain + [f"a{i}" for i in range(10)] + refrain + [f"b{i}" for i in range(10)]
    heard = refrain + [f"a{i}" for i in range(10)] + refrain + [f"b{i}" for i in range(10)]
    skipped = heard[:12] + heard[22:]   # skip a3..a9 and "le modèle lit": 10 words
    t = st.time_script(" ".join(script_words) + ".", _said(skipped))
    gaps = t["report"]["gaps"]
    assert [g["words"] for g in gaps] == [10], gaps
    assert gaps[0]["text"].startswith("a3") and gaps[0]["text"].endswith("lit")


def test_punctuation_only_script_fails_cleanly():
    t = st.time_script("…", _said(["bonjour"]))
    assert t["report"]["status"] == "fail"
    t = st.time_script({"sections": [{"id": "a", "text": "—"}]}, _said(["bonjour"]))
    assert t["report"]["status"] == "fail"


def test_opening_quote_or_dash_starts_with_its_word():
    for script in ("« Bonjour », dit-il.", "— Bonjour, dit-il."):
        caps = st.time_script(script, _said(["bonjour", "dit-il"], t0=1.0))["captions"]
        starts = [c["startMs"] for c in caps]
        assert starts == sorted(starts), caps
        assert caps[0]["startMs"] == 1000 and caps[0]["endMs"] > caps[0]["startMs"], caps


def test_non_monotonic_heard_times_never_give_negative_durations():
    heard = [{"word": w, "start": s, "end": s + 0.2} for w, s in
             [("il", 0.0), ("y", 0.2), ("a", 0.4), ("sept", 1.0), ("mille", 0.9),
              ("cent", 0.8), ("soixante-huit", 0.7), ("cartes", 2.0)]]
    t = st.time_script("Il y a 7 168 cartes.", heard)
    assert all(w["end"] >= w["start"] for w in t["words"])
    assert all(c["endMs"] >= c["startMs"] for c in t["captions"])


def test_heard_word_without_times_is_skipped_not_a_crash():
    heard = _said(["bonjour", "à", "tous"]) + [{"word": " fin", "start": None, "end": None}]
    assert st.time_script("Bonjour à tous.", heard)["report"]["status"] == "pass"


def test_elision_split_after_the_apostrophe_is_rejoined():
    heard = _said(["l'", "homme", "qu'", "il", "voit"])
    assert st.time_script("L'homme qu'il voit.", heard)["report"]["fidelity"] == 1.0


def test_hyphenated_inversions_split_by_the_transcriber_are_rejoined():
    # measured on the M4: large-v3 writes "passe -t -il", "texte -là"
    heard = _said(["que", "se", "passe", "-t", "-il", "avec", "ce", "texte", "-là"])
    t = st.time_script("Que se passe-t-il avec ce texte-là ?", heard)
    assert t["report"]["fidelity"] == 1.0, t["report"]


def test_a_number_heard_in_digits_counts_as_said():
    # measured on the M4: "trente" heard "30"
    t = st.time_script("Il parle trente secondes.", _said(["il", "parle", "30", "secondes"]))
    assert t["report"]["fidelity"] == 1.0 and t["words"][2]["match"] == "spoken_form"


def test_captions_keep_the_script_typography():
    script = "Dupont — le maire — arrive à 50 % du temps."
    heard = _said("dupont le maire arrive à 50 du temps".split())
    caps = st.time_script(script, heard)["captions"]
    shown = " ".join(c["word"] for c in caps).replace(" ", " ")
    assert shown == script


def test_unrelated_words_of_the_same_count_are_not_timed_as_substitutions():
    script = [f"mot{i}" for i in range(20)]
    heard = script[:8] + ["pomme", "table", "vélo"] + script[11:]
    t = st.time_script(" ".join(script) + ".", _said(heard))
    assert [t["words"][i]["match"] for i in (8, 9, 10)] == ["interpolated"] * 3


def test_many_approximate_words_fall_under_the_fidelity_floor():
    script = "Claude calcule chaque matrice pendant que Gemini génère chaque image."
    heard = _said("clode calcul chaque matrise pendant que jemini génère chaque imag".split())
    t = st.time_script(script, heard)
    assert t["report"]["timing_coverage"] == 1.0
    assert t["report"]["fidelity"] < 0.8 and t["report"]["status"] == "revise"
