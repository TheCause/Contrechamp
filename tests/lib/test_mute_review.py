"""The mute story review verifier must bite.

Each test feeds the verifier one known defect in a blind reviewer's answer
(or none) and checks the verdict. Beats are the real brief of a 20 s
paper-cut short (tests/fixtures/mute_review); the sheets index is a
sheets.json as the mute_review tool writes it (marker, video fingerprint,
sheet files on disk). The four real blind reviews of that short are in
blind_reviews.json.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from lib import mute_review as mr
from schemas.artifacts import validate_artifact

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mute_review"
BEATS = json.loads((FIXTURES / "paper_factory_beats.json").read_text(encoding="utf-8"))["beats"]
BLIND = json.loads((FIXTURES / "blind_reviews.json").read_text(encoding="utf-8"))["reviews"]

# Regular sheets s00..s18 + a dense sheet over the hands-on-cheeks gesture.
CELLS = mr.grid_index([f"s{k:02d}" for k in range(0, 20, 2)])
CELLS["d_hands_on_cheeks_1"] = [{"cell": i + 1, "t": 15.25 + i * 0.125} for i in range(8)]

# A cell inside each beat window: (sheet, cell).
CELL = {
    "shutter": ("s00", 4),              # 0.75 s
    "sun_rolled": ("s02", 1),           # 2.00 s
    "sky_painted": ("s06", 1),          # 6.00 s
    "crane": ("s10", 5),                # 11.00 s
    "admiration": ("s14", 1),           # 14.00 s
    "bump_fall": ("s14", 6),            # 15.25 s
    "hands_on_cheeks": ("d_hands_on_cheeks_1", 4),  # 15.625 s
    "stars_night": ("s16", 5),          # 17.00 s
    "looks": ("s18", 2),                # 18.25 s
    "shrug": ("s18", 5),                # 19.00 s
}


def _sheets(tmp_path: Path, cells: dict = CELLS, video: bytes = b"rendered video") -> tuple[Path, Path]:
    """A sheets.json as the tool writes it, its sheet files, and the video."""
    out = tmp_path / "sheets"
    out.mkdir(exist_ok=True)
    video_path = tmp_path / "render.mp4"
    video_path.write_bytes(video)
    for sid in cells:
        (out / f"{sid}.png").write_bytes(b"png")
    manifest = {
        "generator": mr.GENERATOR,
        "video": "render.mp4",
        "video_sha256": hashlib.sha256(video).hexdigest(),
        "sheets": {sid: {"path": f"{sid}.png", "kind": "regular", "cells": c} for sid, c in cells.items()},
    }
    path = out / "sheets.json"
    path.write_text(json.dumps(manifest))
    return path, video_path


@pytest.fixture
def sheets(tmp_path):
    return _sheets(tmp_path)


def _verify(beats, review, sheets):
    manifest, video = sheets
    return mr.verify(beats, review, manifest, video)


def _healthy_review() -> dict:
    return {
        "version": "1.0",
        "reviewer": {"id": "blind-1", "audio_access": False, "authored_video": False,
                     "saw_script_or_code": False},
        "verdicts": [
            {
                "beat_id": b["id"],
                "observed": f"Seen in the cited cell: {b['label']}.",
                "who_does_what": [{"who": "green worker", "does": b["label"]}],
                "verdict": "lisible",
                "evidence": [{"sheet": CELL[b["id"]][0], "cell": CELL[b["id"]][1]}],
            }
            for b in BEATS
        ],
    }


def _verdict(review: dict, beat_id: str) -> dict:
    return next(v for v in review["verdicts"] if v["beat_id"] == beat_id)


def _final_review(status: str = "pass", issues: list[str] | None = None, **checks) -> dict:
    action = {"pass": "present_to_user", "revise": "revise_edit", "fail": "re_author"}[status]
    return {
        "version": "1.0",
        "output_path": "out.mp4",
        "status": status,
        "checks": {
            "technical_probe": {"valid_container": True, "issues": []},
            "visual_spotcheck": {"frames_sampled": 4, "issues": []},
            "audio_spotcheck": {"issues": []},
            "promise_preservation": {"issues": []},
            "subtitle_check": {"issues": []},
            **checks,
        },
        "issues_found": list(issues or []),
        "recommended_action": action,
    }


# ---- silent on the healthy case ---------------------------------------------

def test_healthy_review_passes_with_every_beat_read(sheets):
    review = _healthy_review()
    validate_artifact("mute_review", review)
    check = _verify(BEATS, review, sheets)
    assert check["status"] == "pass", check["issues"]
    assert check["review_ran"] is True
    assert check["issues"] == [] and check["not_checked"] == {} and check["rejected"] == []
    assert check["blocking_beats"] == []
    final = mr.apply_to_final_review(_final_review(), check)
    validate_artifact("final_review", final)
    assert final["status"] == "pass"


# ---- the real blind test ------------------------------------------------------

@pytest.mark.parametrize("version", ["v1", "v2"])
def test_real_blind_review_of_a_defective_render_fails_on_the_attribution(tmp_path, version):
    case = BLIND[version]
    check = _verify(BEATS, case["review"], _sheets(tmp_path, case["sheet_cells"]))
    assert check["status"] == "fail", check["issues"]
    assert {"bump_fall", "stars_night"} <= set(check["blocking_beats"])
    for bid in ("bump_fall", "stars_night"):
        assert next(b for b in check["beats"] if b["id"] == bid)["verdict"] == "contradicts"


@pytest.mark.parametrize("version", ["v3", "final"])
def test_real_blind_review_of_a_sound_render_never_fails(tmp_path, version):
    case = BLIND[version]
    check = _verify(BEATS, case["review"], _sheets(tmp_path, case["sheet_cells"]))
    assert check["status"] != "fail", check["issues"]
    assert check["blocking_beats"] == []
    assert check["review_ran"] is True and check["rejected"] == []


# ---- bites ------------------------------------------------------------------

def test_contradicting_beat_blocks(sheets):
    review = _healthy_review()
    _verdict(review, "bump_fall").update(
        verdict="contradicts",
        observed="The jar falls at the red worker's feet; the stars burst around him.")
    check = _verify(BEATS, review, sheets)
    assert check["status"] == "fail"
    assert check["blocking_beats"] == ["bump_fall"]
    assert any("bump_fall" in i and "contradicts" in i for i in check["issues"])
    final = mr.apply_to_final_review(_final_review(), check)
    validate_artifact("final_review", final)
    assert final["status"] == "fail"


def test_absent_beat_blocks(sheets):
    review = _healthy_review()
    v = _verdict(review, "bump_fall")
    v.update(verdict="absent", who_does_what=[],
             observed="The crate stays still; nobody comes near it.")
    check = _verify(BEATS, review, sheets)
    assert check["status"] == "fail"
    assert check["blocking_beats"] == ["bump_fall"]
    final = mr.apply_to_final_review(_final_review(), check)
    validate_artifact("final_review", final)
    assert final["status"] == "fail"
    assert final["recommended_action"] == "re_author"


def test_partial_beat_asks_for_revision(sheets):
    review = _healthy_review()
    _verdict(review, "looks")["verdict"] = "partiel"
    check = _verify(BEATS, review, sheets)
    assert check["status"] == "revise"
    assert mr.apply_to_final_review(_final_review(), check)["status"] == "revise"


def test_beat_without_verdict_is_not_checked_not_passed(sheets):
    review = _healthy_review()
    review["verdicts"] = [v for v in review["verdicts"] if v["beat_id"] != "shrug"]
    check = _verify(BEATS, review, sheets)
    assert check["not_checked"] == {"shrug": "no verdict for this beat"}
    assert next(b for b in check["beats"] if b["id"] == "shrug")["verdict"] is None
    assert check["status"] == "revise"
    assert mr.apply_to_final_review(_final_review(), check)["status"] == "revise"


def test_review_not_run_is_not_checked_never_pass(sheets):
    check = _verify(BEATS, None, sheets)
    assert check["status"] == "not_checked"
    assert check["review_ran"] is False
    assert check["not_checked"] == {b["id"]: "mute review not run" for b in BEATS}
    final = mr.apply_to_final_review(_final_review(), check)
    validate_artifact("final_review", final)
    assert final["status"] == "revise"


@pytest.mark.parametrize("sheet,cell", [("s14", 9), ("s15", 1), ("d_hands_on_cheeks_2", 1)])
def test_verdict_citing_missing_sheet_or_cell_is_rejected(sheets, sheet, cell):
    review = _healthy_review()
    _verdict(review, "bump_fall")["evidence"] = [{"sheet": sheet, "cell": cell}]
    check = _verify(BEATS, review, sheets)
    assert check["rejected"] == [{"beat_id": "bump_fall",
                                  "reason": f"cites sheet {sheet!r} cell {cell!r}, which does not exist"}]
    assert check["not_checked"]["bump_fall"] == "verdict rejected"
    assert check["status"] == "revise"


def test_evidence_outside_the_beat_window_is_rejected(sheets):
    review = _healthy_review()
    _verdict(review, "hands_on_cheeks")["evidence"] = [{"sheet": "s04", "cell": 1}]  # 4.0 s
    check = _verify(BEATS, review, sheets)
    assert check["rejected"][0]["beat_id"] == "hands_on_cheeks"
    assert "outside the beat window" in check["rejected"][0]["reason"]
    assert check["status"] == "revise"


def test_absent_with_bad_evidence_is_rejected_not_counted(sheets):
    review = _healthy_review()
    _verdict(review, "bump_fall").update(verdict="absent", who_does_what=[],
                                         evidence=[{"sheet": "s99", "cell": 1}])
    assert _verify(BEATS, review, sheets)["status"] == "revise"


@pytest.mark.parametrize("key", ["audio_access", "authored_video", "saw_script_or_code"])
def test_reviewer_who_is_not_blind_does_not_count(sheets, key):
    review = _healthy_review()
    review["reviewer"][key] = True
    check = _verify(BEATS, review, sheets)
    assert check["status"] == "not_checked"
    assert check["review_ran"] is False


def test_reviewer_without_declaration_does_not_count(sheets):
    review = _healthy_review()
    del review["reviewer"]
    assert _verify(BEATS, review, sheets)["status"] == "not_checked"


@pytest.mark.parametrize("break_it", [
    lambda r: _verdict(r, "crane")["evidence"][0].update(cell=True),   # a bool is not a cell
    lambda r: r.update(other_findings={"note": "x"}),                  # an object, not a list
    lambda r: _verdict(r, "crane").update(verdict="ok"),
])
def test_review_that_breaks_the_schema_is_refused(sheets, break_it):
    review = _healthy_review()
    break_it(review)
    check = _verify(BEATS, review, sheets)
    assert check["status"] == "not_checked"
    assert "schema" in check["issues"][0]


def test_judging_without_describing_is_rejected(sheets):
    review = _healthy_review()
    _verdict(review, "crane")["observed"] = "  "
    check = _verify(BEATS, review, sheets)
    assert check["rejected"][0]["beat_id"] == "crane"
    assert check["status"] == "revise"


def test_readable_beat_must_name_who_does_what(sheets):
    review = _healthy_review()
    _verdict(review, "crane")["who_does_what"] = []
    check = _verify(BEATS, review, sheets)
    assert "who does what" in check["rejected"][0]["reason"]


def test_two_verdicts_for_one_beat_are_rejected(sheets):
    review = _healthy_review()
    review["verdicts"].append(copy.deepcopy(_verdict(review, "shrug")))
    check = _verify(BEATS, review, sheets)
    assert check["not_checked"]["shrug"] == "verdict rejected"
    assert check["status"] == "revise"


# ---- where the sheet index comes from ----------------------------------------

def test_no_sheet_index_is_not_checked(sheets):
    assert mr.verify(BEATS, _healthy_review(), None, sheets[1])["status"] == "not_checked"


def test_inline_index_is_refused(sheets):
    # an index typed by hand is not the sheets the reviewer looked at
    check = mr.verify(BEATS, _healthy_review(), {"sheets": CELLS}, sheets[1])
    assert check["status"] == "not_checked"


def test_index_not_written_by_the_tool_is_refused(tmp_path):
    manifest, video = _sheets(tmp_path)
    data = json.loads(manifest.read_text())
    del data["generator"]
    manifest.write_text(json.dumps(data))
    assert mr.verify(BEATS, _healthy_review(), manifest, video)["status"] == "not_checked"


def test_index_whose_sheets_are_missing_on_disk_is_refused(tmp_path):
    manifest, video = _sheets(tmp_path)
    (manifest.parent / "s14.png").unlink()
    check = mr.verify(BEATS, _healthy_review(), manifest, video)
    assert check["status"] == "not_checked"
    assert "s14.png" in check["issues"][0]


def test_sheets_of_another_video_are_refused(tmp_path):
    manifest, _ = _sheets(tmp_path)
    other = tmp_path / "other.mp4"
    other.write_bytes(b"a different render")
    check = mr.verify(BEATS, _healthy_review(), manifest, other)
    assert check["status"] == "not_checked"
    assert "fingerprint" in check["issues"][0]


def test_video_not_given_leaves_the_fingerprint_unchecked(sheets):
    check = mr.verify(BEATS, _healthy_review(), sheets[0], None)
    assert check["not_checked"] == {"video_fingerprint": "no video given to compare with sheets.json"}
    assert check["status"] == "revise"


# ---- folding into final_review -------------------------------------------------

def test_new_review_supersedes_the_previous_story_issues(sheets):
    first = mr.apply_to_final_review(_final_review(), _verify(BEATS, None, sheets))
    assert first["status"] == "revise"
    second = mr.apply_to_final_review(first, _verify(BEATS, _healthy_review(), sheets))
    assert second["status"] == "pass"
    assert second["issues_found"] == []


def test_a_new_review_can_lift_a_story_fail(sheets):
    bad = _healthy_review()
    _verdict(bad, "bump_fall")["verdict"] = "contradicts"
    first = mr.apply_to_final_review(_final_review(), _verify(BEATS, bad, sheets))
    assert first["status"] == "fail"
    second = mr.apply_to_final_review(first, _verify(BEATS, _healthy_review(), sheets))
    assert second["status"] == "pass"


def test_story_pass_does_not_hide_other_issues(sheets):
    final = _final_review("revise", ["Duration drift: rendered 12s vs target 20s"])
    out = mr.apply_to_final_review(final, _verify(BEATS, _healthy_review(), sheets))
    assert out["status"] == "revise"


@pytest.mark.parametrize("final", [
    # the status does not follow from the issues: it must survive anyway
    _final_review("fail", ["atelier doctrine violation: bespoke project imports from the stock registry"],
                  atelier={"stock_reuse_detected": True}),
    _final_review("fail", []),
    _final_review("revise", []),
])
def test_story_pass_never_softens_the_incoming_status(sheets, final):
    out = mr.apply_to_final_review(final, _verify(BEATS, _healthy_review(), sheets))
    assert out["status"] == final["status"]
    assert out["recommended_action"] == final["recommended_action"]


def test_a_fail_set_after_the_render_review_is_not_lifted(sheets):
    # render-time review recorded "revise before story"; a later step (atelier
    # stock reuse) raised the status to fail without touching that record
    rendered = mr.apply_to_final_review(_final_review("revise", ["No audio stream in output"]),
                                        _verify(BEATS, None, sheets))
    rendered["status"], rendered["recommended_action"] = "fail", "re_author"
    out = mr.apply_to_final_review(rendered, _verify(BEATS, _healthy_review(), sheets))
    assert out["status"] == "fail"


def test_reviewing_a_subset_of_the_declared_beats_cannot_pass(sheets):
    declared = mr.apply_to_final_review(_final_review(), _verify(BEATS, None, sheets))
    one = [b for b in BEATS if b["id"] == "shutter"]
    review = _healthy_review()
    review["verdicts"] = [_verdict(review, "shutter")]
    check = _verify(one, review, sheets)
    assert check["status"] == "pass"  # on its own, one beat read
    out = mr.apply_to_final_review(declared, check)
    assert out["status"] == "revise"
    assert set(out["checks"]["story_check"]["not_checked"]) >= {b["id"] for b in BEATS} - {"shutter"}


def test_beat_label_does_not_trigger_the_render_keywords(sheets):
    beats = copy.deepcopy(BEATS)
    beats[0]["label"] = "suspiciously short glance"
    out = mr.apply_to_final_review(_final_review(), _verify(beats, None, sheets))
    assert out["recommended_action"] == "revise_edit"


# ---- beats ---------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    [],
    [{"id": "a", "label": "x", "start": 2, "end": 1, "expected": "y"}],
    [{"id": "a", "label": "x", "start": 0, "end": 1}],
    [{"id": "a", "label": "x", "start": 0, "end": 1, "expected": "y"},
     {"id": "a", "label": "x", "start": 1, "end": 2, "expected": "y"}],
    [{"id": "../escape", "label": "x", "start": 0, "end": 1, "expected": "y"}],
    [{"id": "a b", "label": "x", "start": 0, "end": 1, "expected": "y"}],
])
def test_malformed_beats_are_refused(bad):
    with pytest.raises(mr.MuteReviewError):
        mr.load_beats(bad)


def test_short_beats_are_the_brief_gestures():
    short = {b["id"] for b in mr.load_beats(BEATS) if mr.is_short(b)}
    assert short == {"shutter", "shrug", "hands_on_cheeks", "looks"}
