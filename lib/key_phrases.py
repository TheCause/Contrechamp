"""Key-phrase cards: the channel shows a few essential sentences on screen
instead of burned captions.

Each card is an EXACT sentence of the script (a card text that is not in the
script is refused: on-screen text used to escape the script's approval and
carry pre-correction wording), timed on the voice (lib.script_timing
sentences), held at least ``min_seconds``, never overlapping the next card,
and declared as expected on-screen text so the final review reads it back.
"""
from __future__ import annotations

import unicodedata
from typing import Any

from lib.chapters import to_final_time

MIN_SECONDS = 2.0
GAP_SECONDS = 0.1


def _same(a: str, b: str) -> bool:
    n = lambda s: " ".join(unicodedata.normalize("NFC", s).replace("’", "'").split())
    return n(a) == n(b)


def plan(
    timing: dict,
    phrases: list[str | int],
    *,
    min_seconds: float = MIN_SECONDS,
    inserts: list[dict] | None = None,
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
            match = next((s for s in sentences if _same(s["text"], p)), None)
            if match is None:
                raise ValueError(f"{p!r} is not a sentence of the script (cards show exact script text)")
        chosen.append(match)
    chosen.sort(key=lambda s: s["start"])

    overlays, expected, issues = [], [], []
    for i, s in enumerate(chosen):
        start = to_final_time(s["start"], inserts)
        end = max(to_final_time(s["end"], inserts), start + min_seconds)
        if i + 1 < len(chosen):
            end = min(end, to_final_time(chosen[i + 1]["start"], inserts) - GAP_SECONDS)
        start, end = round(start, 3), round(end, 3)
        if end - start < min_seconds:
            issues.append(
                f"Card {s['text']!r} is shorter than {min_seconds:.1f} s ({end - start:.1f} s): "
                "the next card starts too soon")
        overlays.append({"type": "key_phrase", "text": s["text"], "in_seconds": start, "out_seconds": end})
        expected.append({"text": s["text"], "start_seconds": start, "end_seconds": end, "exact": exact_ocr})
    return {"overlays": overlays, "expected_text": expected, "issues": issues}
