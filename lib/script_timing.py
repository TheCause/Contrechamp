"""Date a script on its narration: words from the SCRIPT, times from the VOICE.

Captions built from a transcription carry every recognition error to the
screen ("Clode" for "Claude", "768" for "7 168"). The script is the validated
text; the transcription only knows *when* each word was said. This module
aligns the two word sequences and keeps the script's words:

- ``exact``        the heard word matches the script word: its times are used;
- ``substituted``  a different word was heard at the same place in an
                   equal-length run (a recognition error): its times are used,
                   the script's word is shown;
- ``spanned``      an unequal run was heard in place of the script run (a
                   number said in letters, "7 168" / "sept mille cent
                   soixante-huit"): the script words share the heard span;
- ``interpolated`` the script word was not heard (skipped by the voice, or
                   merged into another token): its times are spread between
                   the surrounding anchors, and it is reported.

The report says how much of the script is anchored (``timing_coverage``), how
much was heard verbatim (``fidelity``), and lists every run of unheard words
(``gaps``). A skipped passage is never silently timed as if it had been said.
"""
from __future__ import annotations

import difflib
import math
import re
import unicodedata
from typing import Any

_PUNCT = "«»\"“”„‹›.,;:!?…()[]{}—–-"
_SENTENCE_END = re.compile(r"[.!?…]+[\"'»”’)\]]*$")
_ABBREVIATIONS = {"m.", "mm.", "mme.", "mlle.", "dr.", "mr.", "mrs.", "ms.", "st.", "vs."}

DEFAULT_MIN_TIMING_COVERAGE = 0.9
DEFAULT_MAX_GAP_WORDS = 4
DEFAULT_MIN_FIDELITY = 0.8
FILLERS = {"euh", "heu", "hum", "hmm", "ben", "bah", "uh", "um"}


def normalize(token: str) -> str:
    """Comparable form of a word: NFC, lower case, straight apostrophe, no edge punctuation."""
    t = unicodedata.normalize("NFC", token).lower()
    t = t.replace("’", "'").replace("‘", "'").replace(" ", "").replace(" ", "")
    return t.strip(_PUNCT + " '")


def _ends_sentence(word: str) -> bool:
    w = word.strip()
    if not _SENTENCE_END.search(w):
        return False
    return not (w.lower() in _ABBREVIATIONS or re.fullmatch(r"[^\W\d_]\.", w))


def _script_blocks(script: Any) -> list[tuple[str | None, str]]:
    """[(section_id, text)] from a plain string or a script artifact."""
    if isinstance(script, str):
        return [(None, script)]
    sections = (script or {}).get("sections") or []
    return [(s.get("id"), s.get("text", "")) for s in sections if s.get("text", "").strip()]


def time_script(
    script: Any,
    word_timestamps: list[dict],
    *,
    min_timing_coverage: float = DEFAULT_MIN_TIMING_COVERAGE,
    max_gap_words: int = DEFAULT_MAX_GAP_WORDS,
    min_fidelity: float = DEFAULT_MIN_FIDELITY,
) -> dict:
    """Align ``script`` (text or script artifact) on transcriber ``word_timestamps``."""
    # Script words, with their section and sentence index.
    words: list[dict] = []
    sentence = 0
    for section_id, text in _script_blocks(script):
        for raw in text.split():
            words.append({"word": raw, "section": section_id, "sentence": sentence})
            if _ends_sentence(raw):
                sentence += 1
        if words and words[-1]["sentence"] == sentence:
            sentence += 1  # a section always closes its last sentence

    heard = _clean_heard(word_timestamps)
    report: dict[str, Any] = {
        "script_words": 0, "heard_words": len(heard), "exact": 0, "spoken_form": 0,
        "substituted": 0, "spanned": 0, "interpolated": 0, "fidelity": 0.0,
        "timing_coverage": 0.0, "gaps": [], "extras": [],
        "min_timing_coverage": min_timing_coverage, "max_gap_words": max_gap_words,
        "min_fidelity": min_fidelity,
    }
    # Punctuation-only script tokens ("!", "?", "—") are not words: they follow a neighbour.
    idx = [i for i, w in enumerate(words) if normalize(w["word"])]
    if not idx or not heard:
        report["status"] = "fail"
        report["reason"] = "no script words" if not idx else "no heard words to date the script with"
        return {"words": [], "sentences": [], "sections": [], "captions": [], "report": report}

    s_norm = [normalize(words[i]["word"]) for i in idx]
    h_norm = [normalize(w["word"]) for w in heard]
    extra_heard: list[int] = []
    for block in _blocks(_align(s_norm, h_norm), s_norm, h_norm):
        extra_heard += _anchor_block(block, words, idx, heard, s_norm, h_norm)

    _interpolate(words, idx)
    _follow_punctuation(words)

    counted = [words[i] for i in idx]
    n = len(counted)
    report["script_words"] = n
    for kind in ("exact", "spoken_form", "substituted", "spanned", "interpolated"):
        report[kind] = sum(1 for w in counted if w["match"] == kind)
    report["fidelity"] = round((report["exact"] + report["spoken_form"]) / n, 4)
    report["timing_coverage"] = round(
        (report["exact"] + report["spoken_form"] + report["substituted"] + report["spanned"]) / n, 4)
    report["gaps"] = _gaps(counted)
    report["extras"] = _extras(sorted(set(extra_heard)), heard, h_norm)
    too_long = [g for g in report["gaps"] if g["words"] > max_gap_words]
    too_many = [x for x in report["extras"] if x["words"] > max_gap_words]
    report["status"] = (
        "revise"
        if report["timing_coverage"] < min_timing_coverage or too_long or too_many
        or report["fidelity"] < min_fidelity
        else "pass"
    )

    return {
        "words": [
            {k: w[k] for k in ("word", "start", "end", "match", "section", "sentence", "heard") if k in w}
            for w in words
        ],
        "sentences": _spans(words, "sentence"),
        "sections": _spans(words, "section") if any(w["section"] for w in words) else [],
        "captions": _captions(words),
        "report": report,
    }


def _clean_heard(word_timestamps: list[dict]) -> list[dict]:
    """Drop untimed or empty heard words, keep times ordered, rejoin split tokens.

    faster-whisper splits French elisions and inversions: "l 'intention",
    "qu' il", "passe -t -il", "texte -là". A token starting with an
    apostrophe or a hyphen joins the previous one; a token ending with an
    apostrophe joins the next one.
    """
    out: list[dict] = []
    glue_next = False
    for w in word_timestamps:
        token = str(w.get("word", "")).strip()
        start, end = w.get("start"), w.get("end")
        if not normalize(token) and token not in ("'", "’"):
            continue
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if math.isnan(start) or math.isnan(end):
            continue
        start, end = float(start), max(float(end), float(start))
        joins_prev = token[:1] in ("'", "’") or (token[:1] == "-" and token[1:2].isalpha())
        if out and (glue_next or joins_prev):
            prev = out[-1]
            out[-1] = {**prev, "word": prev["word"] + token, "end": max(prev["end"], end)}
        else:
            out.append({**w, "word": token, "start": start, "end": end})
        glue_next = token[-1:] in ("'", "’")
    return out


_NUMBER_WORDS = {
    "zéro", "un", "une", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf",
    "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize", "vingt", "vingts",
    "trente", "quarante", "cinquante", "soixante", "cent", "cents", "mille", "million",
    "millions", "milliard", "milliards", "et", "virgule", "pour", "pourcent", "demi",
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "twenty", "thirty", "forty", "fifty", "hundred", "thousand",
    "percent", "point", "and",
}


def _is_digits(t: str) -> bool:
    return bool(re.fullmatch(r"[0-9][0-9 .,\u202f\u00a0]*%?", t))


def _is_number_words(t: str) -> bool:
    parts = [p for p in re.split(r"[-\s]", t) if p]
    return bool(parts) and all(p in _NUMBER_WORDS for p in parts) and t not in ("et", "and", "pour", "point")


def _number_forms(a: list[str], b: list[str]) -> bool:
    """One side written in digits, the other said in number words (either way)."""
    digits_a = any(map(_is_digits, a)) and all(_is_digits(x) or x in _NUMBER_WORDS for x in a)
    digits_b = any(map(_is_digits, b)) and all(_is_digits(x) or x in _NUMBER_WORDS for x in b)
    words_a = any(map(_is_number_words, a)) and all(_is_number_words(x) or x in _NUMBER_WORDS for x in a)
    words_b = any(map(_is_number_words, b)) and all(_is_number_words(x) or x in _NUMBER_WORDS for x in b)
    return (digits_a and words_b) or (words_a and digits_b)


def _similar(a: str, b: str) -> bool:
    return len(a) >= 3 and len(b) >= 3 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def _align(s: list[str], h: list[str]) -> list[tuple[str, int | None, int | None]]:
    """Global alignment with affine gaps (Gotoh): monotone, one block per skipped passage.

    Returns steps ("M", i, j) pair, ("D", i, None) script word unheard,
    ("I", None, j) heard word not in the script.
    """
    n, m = len(s), len(h)
    NEG = float("-inf")
    GO, GE = -3.0, -0.5

    def sub(i: int, j: int) -> float:
        a, b = s[i], h[j]
        if a == b:
            return 3.0
        if _similar(a, b) or _number_forms([a], [b]):
            return 0.5
        return -2.0

    M = [[NEG] * (m + 1) for _ in range(n + 1)]
    X = [[NEG] * (m + 1) for _ in range(n + 1)]  # gap in heard (D)
    Y = [[NEG] * (m + 1) for _ in range(n + 1)]  # gap in script (I)
    M[0][0] = 0.0
    for i in range(1, n + 1):
        X[i][0] = GO + GE * (i - 1)
    for j in range(1, m + 1):
        Y[0][j] = GO + GE * (j - 1)
    # Diagonal band: wide enough for the whole length difference plus local
    # drift, so a long skipped or added passage stays inside it.
    band = max(60, int(0.2 * max(n, m))) + abs(n - m)
    for i in range(1, n + 1):
        Mi, Xi, Yi, Mp, Xp, Yp = M[i], X[i], Y[i], M[i - 1], X[i - 1], Y[i - 1]
        centre = i * m / n
        for j in range(max(1, int(centre - band)), min(m, int(centre + band)) + 1):
            Mi[j] = max(Mp[j - 1], Xp[j - 1], Yp[j - 1]) + sub(i - 1, j - 1)
            Xi[j] = max(Mp[j] + GO, Xp[j] + GE, Yp[j] + GO)
            Yi[j] = max(Mi[j - 1] + GO, Yi[j - 1] + GE, Xi[j - 1] + GO)
    steps: list[tuple[str, int | None, int | None]] = []
    i, j = n, m
    state = max(("M", M[n][m]), ("X", X[n][m]), ("Y", Y[n][m]), key=lambda t: t[1])[0]
    while i > 0 or j > 0:
        if state == "M":
            steps.append(("M", i - 1, j - 1))
            prev = max(("M", M[i - 1][j - 1]), ("X", X[i - 1][j - 1]), ("Y", Y[i - 1][j - 1]),
                       key=lambda t: t[1])[0]
            i, j, state = i - 1, j - 1, prev
        elif state == "X":
            steps.append(("D", i - 1, None))
            prev = max(("M", M[i - 1][j] + GO), ("X", X[i - 1][j] + GE), ("Y", Y[i - 1][j] + GO),
                       key=lambda t: t[1])[0]
            i, state = i - 1, prev
        else:
            steps.append(("I", None, j - 1))
            prev = max(("M", M[i][j - 1] + GO), ("Y", Y[i][j - 1] + GE), ("X", X[i][j - 1] + GO),
                       key=lambda t: t[1])[0]
            j, state = j - 1, prev
    steps.reverse()
    return steps


def _blocks(steps, s: list[str], h: list[str]):
    """Group steps: each exact pair alone, every run of non-exact steps as one block."""
    block: dict[str, list[int]] = {"s": [], "h": []}
    for op, i, j in steps:
        if op == "M" and s[i] == h[j]:
            if block["s"] or block["h"]:
                yield block
                block = {"s": [], "h": []}
            yield {"s": [i], "h": [j], "exact": True}
        else:
            if i is not None:
                block["s"].append(i)
            if j is not None:
                block["h"].append(j)
    if block["s"] or block["h"]:
        yield block


def _anchor_block(block, words, idx, heard, s_norm, h_norm) -> list[int]:
    """Give times to the script words of one block; return heard indices left over (extras)."""
    S, H = block["s"], block["h"]
    if block.get("exact"):
        w, hw = words[idx[S[0]]], heard[H[0]]
        w["start"], w["end"], w["match"] = hw["start"], hw["end"], "exact"
        return []
    if not S:
        return list(H)
    if not H:
        return []
    s_txt = [s_norm[i] for i in S]
    h_txt = [h_norm[j] for j in H]
    if _number_forms(s_txt, h_txt) and len(H) <= 6 * len(S) + 2 and len(S) <= 6 * len(H) + 2:
        _spread(words, idx, S, heard, H, "spoken_form")
        return []
    if len(S) == len(H):
        left: list[int] = []
        for i, j in zip(S, H):
            if _similar(s_norm[i], h_norm[j]):
                w, hw = words[idx[i]], heard[j]
                w["start"], w["end"], w["match"] = hw["start"], hw["end"], "substituted"
                w["heard"] = hw["word"]
            else:
                left.append(j)
        return left
    joined = difflib.SequenceMatcher(None, "".join(s_txt), "".join(h_txt)).ratio()
    if joined >= 0.75 and len(S) <= 3 * len(H) and len(H) <= 3 * len(S):
        _spread(words, idx, S, heard, H, "spanned")
        return []
    return list(H)


def _spread(words, idx, S, heard, H, kind: str) -> None:
    """Script words S share the span really spoken by heard words H."""
    run = [words[idx[i]] for i in S]
    t0 = heard[H[0]]["start"]
    t1 = max(t0, heard[H[-1]]["end"])
    total = sum(len(w["word"]) for w in run) or 1
    t = t0
    for w in run:
        share = (t1 - t0) * len(w["word"]) / total
        w["start"], w["end"] = round(t, 3), round(t + share, 3)
        w["match"] = kind
        w["heard"] = " ".join(heard[j]["word"] for j in H)
        t += share


def _extras(extra: list[int], heard: list[dict], h_norm: list[str]) -> list[dict]:
    """Runs of heard words that are not in the script (loops, invented passages)."""
    runs, cur = [], []
    for j in extra:
        if h_norm[j] in FILLERS:
            continue
        if cur and j != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(j)
    if cur:
        runs.append(cur)
    return [{"text": " ".join(heard[j]["word"] for j in r), "words": len(r),
             "start": heard[r[0]]["start"], "end": heard[r[-1]]["end"]} for r in runs]


def _interpolate(words: list[dict], idx: list[int]) -> None:
    """Spread unanchored words between their anchors, by character length."""
    real = [words[i] for i in idx]
    k = 0
    while k < len(real):
        if "start" in real[k]:
            k += 1
            continue
        run_end = k
        while run_end < len(real) and "start" not in real[run_end]:
            run_end += 1
        run = real[k:run_end]
        prev = real[k - 1] if k > 0 else None
        nxt = real[run_end] if run_end < len(real) else None
        t0 = prev["end"] if prev else max(0.0, (nxt["start"] if nxt else 0.0) - 0.3 * len(run))
        t1 = nxt["start"] if nxt else t0 + 0.3 * len(run)
        if t1 < t0:
            t1 = t0
        total = sum(len(w["word"]) for w in run) or 1
        t = t0
        for w in run:
            share = (t1 - t0) * len(w["word"]) / total
            w["start"], w["end"] = round(t, 3), round(t + share, 3)
            w["match"] = "interpolated"
            t += share
        k = run_end


def _follow_punctuation(words: list[dict]) -> None:
    for i, w in enumerate(words):
        if "start" in w:
            continue
        if i > 0 and "start" in words[i - 1]:
            t = words[i - 1]["end"]
        else:
            nxt = next((x for x in words[i + 1:] if "start" in x), None)
            t = nxt["start"] if nxt else 0.0
        w["start"], w["end"] = t, t
        w["match"] = "punctuation"


def _gaps(counted: list[dict]) -> list[dict]:
    gaps, run = [], []
    for w in counted + [{"match": "end"}]:
        if w["match"] == "interpolated":
            run.append(w)
        elif run:
            gaps.append({
                "text": " ".join(x["word"] for x in run), "words": len(run),
                "start": run[0]["start"], "end": run[-1]["end"],
            })
            run = []
    return gaps


def _spans(words: list[dict], key: str) -> list[dict]:
    spans: list[dict] = []
    for w in words:
        if spans and spans[-1]["_key"] == w[key]:
            spans[-1]["words"].append(w)
        else:
            spans.append({"_key": w[key], "words": [w]})
    out = []
    for s in spans:
        ws = s["words"]
        timed = [w for w in ws if w["match"] != "punctuation"] or ws
        item = {
            "text": " ".join(w["word"] for w in ws),
            "start": round(timed[0]["start"], 3),
            "end": round(timed[-1]["end"], 3),
        }
        if key == "sentence":
            item["index"] = s["_key"]
            item["section"] = ws[0]["section"]
            item["interpolated_words"] = sum(1 for w in ws if w["match"] == "interpolated")
        else:
            item = {"id": s["_key"], **item}
        out.append(item)
    return out


def _captions(words: list[dict]) -> list[dict]:
    """Remotion word captions, one segment per sentence (French punctuation handled there)."""
    from tools.video.remotion_caption_burn import RemotionCaptionBurn

    segments: dict[int, list[dict]] = {}
    for w in words:
        segments.setdefault(w["sentence"], []).append(
            {"word": w["word"], "start": w["start"], "end": w["end"]})
    return RemotionCaptionBurn()._segments_to_word_captions(
        [{"words": segments[k]} for k in sorted(segments)])
