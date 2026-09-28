"""Chapters dated on the final video.

Section times come from the narration (lib.script_timing). The final video
may insert material into the narration timeline (a title sequence after the
hook, a sponsor card): every narration time at or after an insert moves by
its duration. YouTube's rules are checked and reported, never silently fixed:
the first chapter at 0:00, at least 3 chapters, each at least 10 s long.
"""
from __future__ import annotations

from typing import Any

MIN_CHAPTERS = 3
MIN_CHAPTER_SECONDS = 10.0


def to_final_time(t: float, inserts: list[dict] | None = None) -> float:
    """Map a narration time to the final video time."""
    return round(t + sum(i["duration"] for i in (inserts or []) if t >= i["at"]), 3)


def format_time(seconds: float) -> str:
    s = int(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def chapters(
    sections: list[dict],
    titles: dict[str, str],
    *,
    inserts: list[dict] | None = None,
    video_duration: float | None = None,
) -> dict[str, Any]:
    """sections: [{id, start, end}] in narration time; titles: {section id: title}."""
    items = [s for s in sections if s.get("id") in titles]
    out, issues = [], []
    for s in items:
        t = to_final_time(s["start"], inserts)
        out.append({"id": s["id"], "title": titles[s["id"]], "seconds": t, "time": format_time(t)})
    # The first chapter covers the start of the video (a hook starting at a
    # few hundredths of a second still starts the video).
    if out and out[0]["seconds"] < 1.0:
        out[0]["seconds"], out[0]["time"] = 0.0, "0:00"
    if not out or out[0]["seconds"] != 0.0:
        issues.append("YouTube: the first chapter must start at 0:00")
    if len(out) < MIN_CHAPTERS:
        issues.append(f"YouTube: at least {MIN_CHAPTERS} chapters are needed, got {len(out)}")
    ends = [c["seconds"] for c in out[1:]]
    last_end = video_duration if video_duration is not None else (
        to_final_time(items[-1]["end"], inserts) if items else None)
    ends.append(last_end)
    for c, end in zip(out, ends):
        if end is not None and end - c["seconds"] < MIN_CHAPTER_SECONDS:
            issues.append(
                f"YouTube: chapter {c['id']!r} ({c['title']}) is shorter than "
                f"{MIN_CHAPTER_SECONDS:.0f} s ({end - c['seconds']:.1f} s)")
    return {
        "chapters": out,
        "issues": issues,
        "description": "\n".join(f"{c['time']} {c['title']}" for c in out),
    }
