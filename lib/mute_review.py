"""Mute story review: contract and verifier.

A separate agent, without the sound, who did not write the video, looks at
contact sheets (see tools/analysis/mute_review.py) and says for each story
beat of the brief whether it reads: ``lisible`` (readable), ``partiel``
(partly readable), ``absent``, or ``contradicts`` (readable, but it tells
something else than the brief: the wrong character does it, it happens on
the other side). This module checks that answer against the beats and the
sheets that were actually produced, and folds it into ``final_review``.

Lot 1 conventions apply: a review that did not run is ``not_checked``, never
``pass``; a beat without a verdict is ``not_checked``; an ``absent`` or
``contradicts`` beat blocks (``fail``); a ``partiel`` beat asks for a
revision. A verdict that cites a sheet or a cell that does not exist is
rejected: evidence that cannot be looked at is not evidence. The sheet index
must be the sheets.json the tool wrote, next to its sheet images, cut from
the same video (sha256).

Limit: the reviewer's independence (no sound, no script, did not make the
video) is self-declared, and the judgement itself is the reviewer's. The
verifier checks the form of the answer, not whether the reviewer is right.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

# lisible: reads as the brief says; partiel: there but ambiguous or weak;
# absent: not visible; contradicts: readable, but tells something else than
# the brief (who does it, which side, which direction).
VERDICTS = ("lisible", "partiel", "absent", "contradicts")
BLOCKING = ("absent", "contradicts")
GENERATOR = "contrechamp/mute_review"
BEAT_ID = re.compile(r"^[A-Za-z0-9_-]+$")
SHORT_BEAT_SECONDS = 0.8
# How far outside a beat window a cited cell may sit and still count as
# looking at that beat (one regular cell at 4 fps, plus rounding).
EVIDENCE_TOLERANCE_SECONDS = 0.5
STORY_PREFIX = "Story: "


class MuteReviewError(ValueError):
    """Malformed beats or sheet index (the input, not the review)."""


# ------------------------------------------------------------------ beats

def load_beats(data: Any) -> list[dict[str, Any]]:
    """Validate a beat list (or ``{"beats": [...]}``) and return it sorted.

    Each beat: ``{id, label, start, end, expected}``; ``end >= start``;
    ``start == end`` is a point beat (a glance, a look).
    """
    beats = data.get("beats") if isinstance(data, dict) else data
    if not isinstance(beats, list) or not beats:
        raise MuteReviewError("beats must be a non-empty list")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for i, beat in enumerate(beats):
        if not isinstance(beat, dict):
            raise MuteReviewError(f"beat #{i} is not an object")
        missing = [k for k in ("id", "label", "start", "end", "expected") if k not in beat]
        if missing:
            raise MuteReviewError(f"beat #{i} lacks {', '.join(missing)}")
        bid = beat["id"]
        if not isinstance(bid, str) or not BEAT_ID.match(bid):
            raise MuteReviewError(
                f"beat #{i}: id {bid!r} must be letters, digits, '_' or '-' only "
                "(it names sheet files)")
        if bid in seen:
            raise MuteReviewError(f"duplicate beat id {bid!r}")
        seen.add(bid)
        try:
            start, end = float(beat["start"]), float(beat["end"])
        except (TypeError, ValueError) as e:
            raise MuteReviewError(f"beat {bid!r}: start/end must be numbers") from e
        if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end < start:
            raise MuteReviewError(f"beat {bid!r}: need 0 <= start <= end, got {start}..{end}")
        if not str(beat["expected"]).strip():
            raise MuteReviewError(f"beat {bid!r}: expected must say what should be seen")
        out.append({**beat, "start": start, "end": end})
    return sorted(out, key=lambda b: (b["start"], b["end"]))


def is_short(beat: dict[str, Any], threshold: float = SHORT_BEAT_SECONDS) -> bool:
    """A beat brief enough to fall between two cells at 4 fps."""
    return beat["end"] - beat["start"] < threshold


# ------------------------------------------------------------- sheet index

def grid_index(
    sheet_ids: Iterable[str],
    seconds_per_sheet: float = 2.0,
    fps: float = 4.0,
    cells_per_sheet: int = 8,
) -> dict[str, list[dict[str, Any]]]:
    """Index for regular sheets named ``sNN`` (NN = first second).

    Used for sheets produced elsewhere with the same layout (cells numbered
    1..8, row-major, ``1 / fps`` apart). Sheets from the tool come with
    their own exact index.
    """
    index: dict[str, list[dict[str, Any]]] = {}
    for sid in sheet_ids:
        if not (sid.startswith("s") and sid[1:].isdigit()):
            raise MuteReviewError(f"sheet id {sid!r} is not of the form sNN")
        t0 = float(sid[1:])
        index[sid] = [
            {"cell": c + 1, "t": round(t0 + c / fps, 3)}
            for c in range(cells_per_sheet)
            if c / fps < seconds_per_sheet
        ]
    return index


def normalize_index(index: Any) -> dict[str, list[dict[str, Any]]]:
    """Accept ``{sheet: [cells]}``, ``{sheet: {"cells": [...]}}`` or the
    tool's manifest ``{"sheets": {sheet: {"cells": [...]}}}``."""
    if not isinstance(index, dict):
        raise MuteReviewError("sheet index must be an object")
    if isinstance(index.get("sheets"), dict):
        index = index["sheets"]
    out: dict[str, list[dict[str, Any]]] = {}
    for sid, value in index.items():
        cells = value.get("cells") if isinstance(value, dict) else value
        if not isinstance(cells, list):
            raise MuteReviewError(f"sheet {sid!r} has no cell list")
        for c in cells:
            if not (isinstance(c, dict) and isinstance(c.get("cell"), int)
                    and isinstance(c.get("t"), (int, float))):
                raise MuteReviewError(f"sheet {sid!r}: cells must be {{cell: int, t: seconds}}")
        out[sid] = cells
    return out


def _cell_time(index: dict[str, Any], sheet: Any, cell: Any) -> float | None:
    cells = index.get(sheet) if isinstance(sheet, str) else None
    if not cells:
        return None
    for entry in cells:
        if entry.get("cell") == cell:
            return float(entry["t"])
    return None


# ---------------------------------------------------------------- verifier

def _independence_problem(reviewer: Any) -> str | None:
    if not isinstance(reviewer, dict):
        return "review has no reviewer declaration"
    for key, meaning in (
        ("audio_access", "heard the sound"),
        ("authored_video", "wrote the video"),
        ("saw_script_or_code", "saw the script or the scene code"),
    ):
        if key not in reviewer:
            return f"reviewer declaration lacks {key}"
        if reviewer[key] is not False:
            return f"reviewer {meaning} ({key}={reviewer[key]!r}); a mute review needs a blind reader"
    return None


def _check_verdict(
    v: dict[str, Any], beat: dict[str, Any], index: dict[str, Any]
) -> tuple[str | None, list[dict[str, Any]]]:
    """Return (rejection reason or None, evidence with times)."""
    if v.get("verdict") not in VERDICTS:
        return f"verdict {v.get('verdict')!r} is not one of {', '.join(VERDICTS)}", []
    observed = v.get("observed")
    if not isinstance(observed, str) or not observed.strip():
        return "no description of what was seen (describe before judging)", []
    who = v.get("who_does_what")
    if not isinstance(who, list):
        return "who_does_what missing (name who does what)", []
    if v["verdict"] != "absent" and not who:
        return "a readable beat must name who does what", []
    for item in who:
        if not (isinstance(item, dict) and str(item.get("who", "")).strip()
                and str(item.get("does", "")).strip()):
            return "each who_does_what entry needs 'who' and 'does'", []
    evidence = v.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return "no evidence (sheet + cell)", []
    timed: list[dict[str, Any]] = []
    for ev in evidence:
        if not isinstance(ev, dict):
            return "evidence entries must be {sheet, cell}", []
        t = _cell_time(index, ev.get("sheet"), ev.get("cell"))
        if t is None:
            return f"cites sheet {ev.get('sheet')!r} cell {ev.get('cell')!r}, which does not exist", []
        timed.append({"sheet": ev["sheet"], "cell": ev["cell"], "t": t})
    lo = beat["start"] - EVIDENCE_TOLERANCE_SECONDS
    hi = beat["end"] + EVIDENCE_TOLERANCE_SECONDS
    if not any(lo <= e["t"] <= hi for e in timed):
        times = ", ".join(f"{e['t']:.2f}s" for e in timed)
        return (f"evidence ({times}) lies outside the beat window "
                f"{beat['start']:.2f}-{beat['end']:.2f}s"), timed
    return None, timed


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_sheets(
    sheets_manifest: Any, video_path: str | Path | None
) -> tuple[dict[str, list[dict[str, Any]]] | None, str | None, str | None]:
    """Read the sheets.json written by the mute_review tool.

    Returns ``(index, refusal, fingerprint_not_checked)``. The index is only
    trusted when it is that file: the tool's marker, every sheet image
    present next to it, and (when the video is given) the same video.
    """
    if sheets_manifest is None:
        return None, "no sheets.json: the cited sheets cannot be checked", None
    if not isinstance(sheets_manifest, (str, Path)):
        return None, "sheet index must be the sheets.json file written by the mute_review tool", None
    path = Path(sheets_manifest)
    if not path.is_file():
        return None, f"sheets.json not found: {path}", None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"sheets.json unreadable: {e}", None
    if not isinstance(manifest, dict) or manifest.get("generator") != GENERATOR:
        return None, "sheets.json was not written by the mute_review tool", None
    try:
        index = normalize_index(manifest)
    except MuteReviewError as e:
        return None, str(e), None
    sheets = manifest["sheets"]
    missing = [str(s.get("path")) for s in sheets.values()
               if not (isinstance(s, dict) and s.get("path") and (path.parent / s["path"]).is_file())]
    if missing:
        return None, f"sheet files missing next to sheets.json: {', '.join(missing)}", None
    if video_path is None:
        return index, None, "no video given to compare with sheets.json"
    video = Path(video_path)
    if not video.is_file():
        return None, f"video not found: {video}", None
    if _sha256(video) != manifest.get("video_sha256"):
        return None, "video fingerprint differs: these sheets were not cut from this video", None
    return index, None, None


def _review_schema_problem(review: Any) -> str | None:
    import jsonschema

    from schemas.artifacts import load_schema

    try:
        jsonschema.validate(instance=review, schema=load_schema("mute_review"))
    except jsonschema.ValidationError as e:
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        return f"review breaks schemas/artifacts/mute_review.schema.json at {where}: {e.message}"
    return None


def verify(
    beats: Any,
    review: dict[str, Any] | None,
    sheets_manifest: str | Path | None,
    video_path: str | Path | None = None,
) -> dict[str, Any]:
    """Check a mute review against the beats and the sheets that exist.

    ``sheets_manifest`` is the sheets.json written by the mute_review tool;
    ``video_path`` the render it was cut from (its fingerprint must match).
    Returns a ``story_check`` block for ``final_review.checks``.
    """
    beats = load_beats(beats)
    result: dict[str, Any] = {
        "status": "not_checked",
        "review_ran": False,
        "beats": [],
        "blocking_beats": [],
        "not_checked": {},
        "rejected": [],
        "other_findings": [],
        "issues": [],
    }

    def unchecked(reason: str) -> dict[str, Any]:
        for b in beats:
            result["beats"].append({"id": b["id"], "label": b["label"], "verdict": None})
            result["not_checked"][b["id"]] = reason
        result["issues"].append(f"{STORY_PREFIX}mute review not checked: {reason}")
        return result

    if review is None:
        return unchecked("mute review not run")
    problem = _review_schema_problem(review) or _independence_problem(review.get("reviewer"))
    if problem:
        return unchecked(problem)
    sheet_index, refusal, fingerprint = load_sheets(sheets_manifest, video_path)
    if refusal:
        return unchecked(refusal)
    if fingerprint:
        result["not_checked"]["video_fingerprint"] = fingerprint
        result["issues"].append(f"{STORY_PREFIX}sheets not matched to the render: {fingerprint}")

    result["review_ran"] = True
    by_id = {b["id"]: b for b in beats}
    given: dict[str, list[dict[str, Any]]] = {}
    for v in review["verdicts"]:
        bid = v.get("beat_id")
        if bid not in by_id:
            result["rejected"].append({"beat_id": bid, "reason": "unknown beat id"})
            continue
        given.setdefault(bid, []).append(v)

    for b in beats:
        entry: dict[str, Any] = {"id": b["id"], "label": b["label"], "verdict": None}
        vs = given.get(b["id"], [])
        if not vs:
            result["not_checked"][b["id"]] = "no verdict for this beat"
        elif len(vs) > 1:
            result["rejected"].append({"beat_id": b["id"], "reason": "several verdicts for one beat"})
            result["not_checked"][b["id"]] = "verdict rejected"
        else:
            v = vs[0]
            reason, evidence = _check_verdict(v, b, sheet_index)
            if reason:
                result["rejected"].append({"beat_id": b["id"], "reason": reason})
                result["not_checked"][b["id"]] = "verdict rejected"
            else:
                entry.update(
                    verdict=v["verdict"],
                    evidence=evidence,
                    observed=v["observed"],
                    who_does_what=v["who_does_what"],
                )
                if v.get("between_cells_risk"):
                    entry["between_cells_risk"] = v["between_cells_risk"]
        result["beats"].append(entry)

    result["other_findings"] = list(review.get("other_findings") or [])

    wording = {
        "absent": "is not visible",
        "contradicts": "contradicts the brief",
        "partiel": "reads only partly",
    }
    for verdict in ("absent", "contradicts", "partiel"):
        for e in result["beats"]:
            if e["verdict"] == verdict:
                result["issues"].append(
                    f"{STORY_PREFIX}beat '{e['id']}' ({e['label']}) {wording[verdict]} "
                    f"[{verdict}]: {e['observed']}")
    for r in result["rejected"]:
        result["issues"].append(f"{STORY_PREFIX}verdict for '{r['beat_id']}' rejected: {r['reason']}")
    for bid, reason in result["not_checked"].items():
        if reason == "no verdict for this beat":
            result["issues"].append(f"{STORY_PREFIX}beat '{bid}' not checked: {reason}")

    result["blocking_beats"] = [e["id"] for e in result["beats"] if e["verdict"] in BLOCKING]
    if result["blocking_beats"]:
        result["status"] = "fail"
    elif (any(e["verdict"] == "partiel" for e in result["beats"])
          or result["not_checked"] or result["rejected"]):
        result["status"] = "revise"
    else:
        result["status"] = "pass"
    return result


# ------------------------------------------------------- final_review link

_RANK = {"pass": 0, "revise": 1, "fail": 2}
_STORY_OUTCOME = {
    "pass": ("pass", "present_to_user"),
    "revise": ("revise", "revise_edit"),
    "not_checked": ("revise", "revise_edit"),
    "fail": ("fail", "re_author"),
}


def fold_status(base: tuple[str, str], story_status: str) -> tuple[str, str]:
    """The stricter of the status without the story check and the story's.

    The story check can only make a final review stricter, never softer.
    """
    story = _STORY_OUTCOME.get(story_status, ("revise", "revise_edit"))
    return story if _RANK[story[0]] > _RANK.get(base[0], 2) else base


def _cover_declared_beats(story_check: dict[str, Any], declared: list[str]) -> dict[str, Any]:
    """Beats declared earlier but absent from this review stay not_checked."""
    sc = json.loads(json.dumps(story_check))
    reviewed = [b["id"] for b in sc.get("beats") or []]
    for bid in declared:
        if bid not in reviewed:
            sc.setdefault("beats", []).append({"id": bid, "verdict": None})
            sc.setdefault("not_checked", {})[bid] = "declared beat missing from this review"
            sc.setdefault("issues", []).append(f"{STORY_PREFIX}beat '{bid}' not checked: declared but not reviewed")
    for bid in reviewed:
        if bid not in declared:
            sc.setdefault("issues", []).append(f"{STORY_PREFIX}beat '{bid}' was reviewed but never declared")
            sc.setdefault("not_checked", {})[f"undeclared:{bid}"] = "beat reviewed but not declared"
    if sc.get("not_checked") and sc.get("status") == "pass":
        sc["status"] = "revise"
    return sc


def apply_to_final_review(final_review: dict[str, Any], story_check: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``final_review`` with ``checks.story_check`` set.

    Earlier story issues are replaced (a second review supersedes the
    first). Everything else is left as it came: the status without the
    story check (``metadata.status_before_story``, else the incoming status)
    is never softened, the story verdict can only make it stricter. Beats
    declared by an earlier story check must all be in the new one.
    """
    out = json.loads(json.dumps(final_review))
    checks = out.setdefault("checks", {})
    metadata = out.setdefault("metadata", {})
    incoming = (out.get("status", "fail"), out.get("recommended_action", "re_render"))
    previous = checks.get("story_check") or {}
    before = metadata.get("status_before_story")
    base = incoming
    if isinstance(before, dict) and before.get("status") in _RANK:
        recorded = (before["status"], before.get("recommended_action", incoming[1]))
        # Only trust the record if it, plus the previous story check, explains
        # the incoming status; otherwise something else set it, keep it.
        if fold_status(recorded, previous.get("status", "pass")) == incoming:
            base = recorded
    metadata["status_before_story"] = {"status": base[0], "recommended_action": base[1]}
    declared = [b["id"] for b in previous.get("beats") or [] if isinstance(b, dict) and b.get("id")]
    if declared:
        story_check = _cover_declared_beats(story_check, declared)
    checks["story_check"] = story_check
    other = [i for i in out.get("issues_found") or [] if not i.startswith(STORY_PREFIX)]
    out["issues_found"] = other + list(story_check.get("issues") or [])
    out["status"], out["recommended_action"] = fold_status(base, story_check.get("status"))
    return out

