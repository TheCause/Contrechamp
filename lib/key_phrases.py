"""Key-phrase cards: the channel shows a few essential sentences on screen
instead of burned captions.

Each card is an EXACT sentence of the script (a card text that is not in the
script is refused: on-screen text used to escape the script's approval and
carry pre-correction wording), timed on the voice (lib.script_timing
sentences), held at least ``min_seconds``, never overlapping the next card,
an insert or the end of the video, and declared as expected on-screen text
so the final review reads it back. Captions under a card are removed: the
same sentence shown twice also blurred the OCR read-back (0.985 alone, 0.79
with the caption page under it).
"""
from __future__ import annotations

import unicodedata
from typing import Any

from lib.chapters import to_final_time

MIN_SECONDS = 2.0
MIN_EMIT_SECONDS = 0.5
GAP_SECONDS = 0.1


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    for a in ("’", "ʼ", "‘"):
        s = s.replace(a, "'")
    s = s.replace(" ", " ").replace(" ", " ")
    return " ".join(s.split())


def plan(
    timing: dict,
    phrases: list[str | int],
    *,
    min_seconds: float = MIN_SECONDS,
    inserts: list[dict] | None = None,
    video_duration: float | None = None,
    captions: list[dict] | None = None,
    exact_ocr: bool = False,
) -> dict[str, Any]:
    """phrases: sentence texts (exact) or sentence indices from ``timing``."""
    sentences = timing.get("sentences") or []
    chosen = []
    for p in phrases:
        if isinstance(p, int):
            match = next((s for s in sentences if s.get("index") == p), None)
            if match is None:
                raise ValueError(f"Sentence index {p} not in the timing")
        else:
            found = [s for s in sentences if _norm(s["text"]) == _norm(p)]
            if not found:
                raise ValueError(f"{p!r} is not a sentence of the script (cards show exact script text)")
            if len(found) > 1:
                raise ValueError(
                    f"{p!r} appears {len(found)} times in the script "
                    f"(indices {[s.get('index') for s in found]}): choose it by index")
            match = found[0]
        chosen.append(match)
    chosen.sort(key=lambda s: s["start"])

    overlays, expected, issues = [], [], []
    for i, s in enumerate(chosen):
        start = to_final_time(s["start"], inserts)
        end = max(to_final_time(s["end"], inserts), start + min_seconds)
        if i + 1 < len(chosen):
            end = min(end, to_final_time(chosen[i + 1]["start"], inserts) - GAP_SECONDS)
        for ins in inserts or []:
            if ins["at"] > s["start"]:  # an insert after the card starts: stop before it
                end = min(end, to_final_time(ins["at"], [x for x in inserts if x is not ins]))
        if video_duration is not None:
            end = min(end, video_duration)
        start, end = round(start, 3), round(end, 3)
        if end - start < min_seconds:
            issues.append(
                f"Card {s['text']!r} is shorter than {min_seconds:.1f} s ({end - start:.1f} s): "
                "the next card, an insert or the end of the video comes too soon")
        if end - start < MIN_EMIT_SECONDS:
            issues.append(f"Card {s['text']!r} not emitted: {max(end - start, 0):.2f} s cannot be read")
            continue
        overlays.append({"type": "key_phrase", "text": s["text"], "in_seconds": start, "out_seconds": end})
        expected.append({"text": s["text"], "start_seconds": start, "end_seconds": end, "exact": exact_ocr})

    result: dict[str, Any] = {"overlays": overlays, "expected_text": expected, "issues": issues}
    if captions is not None:
        windows = [(o["in_seconds"] * 1000, o["out_seconds"] * 1000) for o in overlays]
        result["captions"] = [
            c for c in captions
            if not any(a < c["endMs"] and c["startMs"] < b for a, b in windows)
        ]
    return result
