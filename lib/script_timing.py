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
import re
import unicodedata
from typing import Any

_PUNCT = "«»\"“”„‹›.,;:!?…()[]{}—–-"
_SENTENCE_END = re.compile(r"[.!?…]+[\"'»”’)\]]*$")
_ABBREVIATIONS = {"m.", "mm.", "mme.", "mlle.", "dr.", "mr.", "mrs.", "ms.", "st.", "vs."}

DEFAULT_MIN_TIMING_COVERAGE = 0.9
DEFAULT_MAX_GAP_WORDS = 4


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

    heard = _rejoin_elisions([w for w in word_timestamps if normalize(w.get("word", ""))])
    report: dict[str, Any] = {
        "script_words": 0, "heard_words": len(heard), "exact": 0, "substituted": 0, "spanned": 0,
        "interpolated": 0, "fidelity": 0.0, "timing_coverage": 0.0, "gaps": [],
        "min_timing_coverage": min_timing_coverage, "max_gap_words": max_gap_words,
    }
    if not words or not heard:
        report["status"] = "fail"
        report["reason"] = "no script words" if not words else "no heard words to date the script with"
        return {"words": [], "sentences": [], "sections": [], "captions": [], "report": report}

    # Punctuation-only script tokens ("!", "?") are not words: they follow their neighbour.
    idx = [i for i, w in enumerate(words) if normalize(w["word"])]
    s_norm = [normalize(words[i]["word"]) for i in idx]
    h_norm = [normalize(w["word"]) for w in heard]

    matcher = difflib.SequenceMatcher(None, s_norm, h_norm, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                w = words[idx[i1 + k]]
                h = heard[j1 + k]
                w["start"], w["end"] = float(h["start"]), float(h["end"])
                w["match"] = "exact" if op == "equal" else "substituted"
                if op == "replace":
                    w["heard"] = h["word"].strip()
        elif op == "replace":
            # Same passage said in another form ("7 168" / "sept mille cent
            # soixante-huit"): the script words share the span really spoken.
            run = [words[idx[i]] for i in range(i1, i2)]
            t0, t1 = float(heard[j1]["start"]), float(heard[j2 - 1]["end"])
            total = sum(len(w["word"]) for w in run) or 1
            t = t0
            for w in run:
                share = (t1 - t0) * len(w["word"]) / total
                w["start"], w["end"] = round(t, 3), round(t + share, 3)
                w["match"] = "spanned"
                w["heard"] = " ".join(h["word"].strip() for h in heard[j1:j2])
                t += share

    _interpolate(words, idx)
    _follow_punctuation(words)

    counted = [words[i] for i in idx]
    n = len(counted)
    report["script_words"] = n
    for kind in ("exact", "substituted", "spanned", "interpolated"):
        report[kind] = sum(1 for w in counted if w["match"] == kind)
    report["fidelity"] = round(report["exact"] / n, 4)
    report["timing_coverage"] = round(
        (report["exact"] + report["substituted"] + report["spanned"]) / n, 4)
    report["gaps"] = _gaps(counted)
    too_long = [g for g in report["gaps"] if g["words"] > max_gap_words]
    report["status"] = (
        "revise" if report["timing_coverage"] < min_timing_coverage or too_long else "pass"
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


def _rejoin_elisions(heard: list[dict]) -> list[dict]:
    """faster-whisper splits French elisions: "l 'intention", "Aujourd 'hui".

    A heard token that starts with an apostrophe belongs to the previous one.
    """
    out: list[dict] = []
    for w in heard:
        token = w["word"].strip()
        if out and token[:1] in ("'", "’"):
            prev = out[-1]
            out[-1] = {**prev, "word": prev["word"].rstrip() + token, "end": w["end"]}
        else:
            out.append(dict(w))
    return out


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
        ref = words[i - 1] if i > 0 and "start" in words[i - 1] else next(
            (x for x in words[i + 1:] if "start" in x), None)
        w["start"], w["end"] = (ref["end"], ref["end"]) if ref else (0.0, 0.0)
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
