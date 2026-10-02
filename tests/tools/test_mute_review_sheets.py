"""Contact sheets for the mute review: real ffmpeg, synthetic video.

The test video's brightness grows with the frame number, so the content of
each cell tells which frame it holds: a cell labelled 1.25 s must show the
frame of 1.25 s, not "some frame". Extraction failures must fail the tool,
never yield a smaller sheet.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageStat

from tools.analysis.mute_review import MuteReview
from tools.analysis.frame_sampler import FrameSampler
from tools.base_tool import ToolResult
from tools.video.video_compose import VideoCompose
from schemas.artifacts import validate_artifact

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")

FPS = 30
STEP = 2  # luma added per frame


def _ramp_video(path: Path, seconds: float = 3.0) -> Path:
    """9:16 grey video whose luma is 16 + STEP * frame_number."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"nullsrc=s=90x160:r={FPS}:d={seconds},format=gray,geq=lum='16+{STEP}*N'",
         "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", str(path)],
        check=True,
    )
    return path


def _frame_number(gray: float) -> float:
    # geq writes full-range gray: 16 + STEP * N comes back as is
    return (gray - 16) / STEP


def _cell_gray(sheet: Image.Image, cell: int, cols: int = 4, cw: int = 405, ch: int = 720, gap: int = 4) -> float:
    i = cell - 1
    x, y = (i % cols) * (cw + gap), (i // cols) * (ch + gap)
    box = sheet.convert("L").crop((x + cw // 4, y + ch // 2, x + 3 * cw // 4, y + 3 * ch // 4))
    return ImageStat.Stat(box).mean[0]


BEATS = [
    {"id": "long", "label": "long beat", "start": 0.2, "end": 1.2, "expected": "something long"},
    {"id": "blink", "label": "brief gesture", "start": 1.3, "end": 1.6, "expected": "a brief gesture"},
]


def test_sheets_carry_the_right_frame_in_each_labelled_cell(tmp_path):
    video = _ramp_video(tmp_path / "ramp.mp4")
    res = MuteReview().execute({"operation": "sheets", "input_path": str(video),
                                "output_dir": str(tmp_path / "sheets"), "beats": BEATS})
    assert res.success, res.error
    manifest = json.loads(Path(res.data["manifest"]).read_text())
    sheets = manifest["sheets"]
    assert set(sheets) == {"s00", "s02", "d_blink_1"}
    assert manifest["cell_size"] == "405x720"
    assert [c["t"] for c in sheets["s00"]["cells"]] == [0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75]
    assert len(sheets["s02"]["cells"]) == 4  # the video ends at 3 s
    # dense: 8 frames/s from 1.0 to 1.875 s, around the 1.3-1.6 s gesture
    assert [c["t"] for c in sheets["d_blink_1"]["cells"]] == [1.0 + k / 8 for k in range(8)]
    for sid, entry in sheets.items():
        img = Image.open(tmp_path / "sheets" / entry["path"])
        assert img.size == (4 * 405 + 3 * 4, 2 * 720 + 4)
        for c in entry["cells"]:
            n = _frame_number(_cell_gray(img, c["cell"]))
            assert abs(n - c["t"] * FPS) <= 1.0, (sid, c, n)


def test_dense_sheets_cover_every_short_beat_with_full_sheets():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "mute_review" / "paper_factory_beats.json"
    from lib.mute_review import load_beats
    beats = load_beats(json.loads(fixture.read_text())["beats"])
    plan = MuteReview.plan_sheets(20.0, beats)
    regular = [s for s in plan if s["kind"] == "regular"]
    assert [s["id"] for s in regular] == [f"s{k:02d}" for k in range(0, 20, 2)]
    assert all(len(s["times"]) == 8 for s in plan)
    for beat in beats:
        dense = [t for s in plan if s.get("beat_id") == beat["id"] for t in s["times"]]
        if beat["end"] - beat["start"] >= 0.8:
            assert dense == []
            continue
        # every 1/8 s step from the beat's start to its end is on a dense sheet
        assert dense[0] <= beat["start"] and dense[-1] >= beat["end"], (beat["id"], dense)
        assert all(abs(b - a - 0.125) < 1e-6 for a, b in zip(dense, dense[1:]))
        assert dense[-1] < 20.0
    hands = [s for s in plan if s.get("beat_id") == "hands_on_cheeks"]
    assert [s["times"] for s in hands] == [[15.375 + k / 8 for k in range(8)]]


def test_long_beat_gets_no_dense_sheet(tmp_path):
    video = _ramp_video(tmp_path / "ramp.mp4", seconds=2.0)
    res = MuteReview().execute({"operation": "sheets", "input_path": str(video),
                                "output_dir": str(tmp_path / "sheets"), "beats": BEATS[:1]})
    assert res.success, res.error
    assert res.data["dense_sheets"] == []


def test_unreadable_file_fails(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"\x00not a video" * 100)
    res = MuteReview().execute({"operation": "sheets", "input_path": str(bad),
                                "output_dir": str(tmp_path / "sheets")})
    assert not res.success
    assert "cannot read" in res.error
    assert not (tmp_path / "sheets" / "sheets.json").exists()


def test_missing_input_fails(tmp_path):
    res = MuteReview().execute({"operation": "sheets", "input_path": str(tmp_path / "nope.mp4")})
    assert not res.success


def test_a_frame_that_does_not_come_back_fails_the_sheets(tmp_path, monkeypatch):
    video = _ramp_video(tmp_path / "ramp.mp4", seconds=2.0)
    real = FrameSampler.execute

    def lossy(self, inputs):
        res = real(self, inputs)
        return ToolResult(success=True, data={**res.data, "frames": res.data["frames"][:-1]})

    monkeypatch.setattr(FrameSampler, "execute", lossy)
    res = MuteReview().execute({"operation": "sheets", "input_path": str(video),
                                "output_dir": str(tmp_path / "sheets")})
    assert not res.success
    assert "could not be extracted" in res.error
    assert not (tmp_path / "sheets" / "s00.png").exists()


def test_final_review_with_story_beats_cannot_pass_before_the_mute_review(tmp_path):
    video = _ramp_video(tmp_path / "ramp.mp4", seconds=2.0)
    ed = {"metadata": {"story_beats": BEATS}}
    review = VideoCompose()._run_final_review(output_path=video, edit_decisions=ed)
    validate_artifact("final_review", review)
    story = review["checks"]["story_check"]
    assert story["status"] == "not_checked"
    assert review["status"] != "pass"
    assert any(i.startswith("Story: ") for i in review["issues_found"])


def test_final_review_without_story_beats_has_no_story_check(tmp_path):
    video = _ramp_video(tmp_path / "ramp.mp4", seconds=2.0)
    review = VideoCompose()._run_final_review(output_path=video, edit_decisions={})
    validate_artifact("final_review", review)
    assert "story_check" not in review["checks"]
    assert not any(i.startswith("Story: ") for i in review["issues_found"])


def test_verify_operation_folds_the_review_into_final_review(tmp_path):
    video = _ramp_video(tmp_path / "ramp.mp4", seconds=2.0)
    tool = MuteReview()
    sheets = tool.execute({"operation": "sheets", "input_path": str(video),
                           "output_dir": str(tmp_path / "sheets"), "beats": BEATS})
    assert sheets.success, sheets.error
    final_path = tmp_path / "final_review.json"
    final = VideoCompose()._run_final_review(output_path=video,
                                             edit_decisions={"metadata": {"story_beats": BEATS}})
    final_path.write_text(json.dumps(final))
    review = {
        "version": "1.0",
        "reviewer": {"audio_access": False, "authored_video": False, "saw_script_or_code": False},
        "verdicts": [
            {"beat_id": "long", "observed": "grey frames brighten", "verdict": "lisible",
             "who_does_what": [{"who": "the frame", "does": "brightens"}],
             "evidence": [{"sheet": "s00", "cell": 3}]},
            {"beat_id": "blink", "observed": "nothing changes", "verdict": "absent",
             "who_does_what": [], "evidence": [{"sheet": "d_blink_1", "cell": 4}]},
        ],
    }
    res = tool.execute({"operation": "verify", "beats": BEATS, "review": review,
                        "sheet_index": sheets.data["manifest"], "final_review_path": str(final_path)})
    assert res.success, res.error
    assert res.data["status"] == "fail"
    written = json.loads(final_path.read_text())
    assert written["status"] == "fail"
    assert written["checks"]["story_check"]["review_ran"] is True
    assert written["checks"]["story_check"]["not_checked"] == {}  # fingerprint read from output_path

    # the same review with a hand-typed index is refused
    inline = tool.execute({"operation": "verify", "beats": BEATS, "review": review,
                           "sheet_index": json.loads(Path(sheets.data["manifest"]).read_text()),
                           "final_review_path": str(final_path)})
    assert not inline.success or inline.data["status"] == "not_checked"


def test_sheets_json_carries_the_tool_marker_and_the_video_fingerprint(tmp_path):
    import hashlib
    from lib.mute_review import GENERATOR

    video = _ramp_video(tmp_path / "ramp.mp4", seconds=2.0)
    res = MuteReview().execute({"operation": "sheets", "input_path": str(video),
                                "output_dir": str(tmp_path / "sheets")})
    assert res.success, res.error
    manifest = json.loads(Path(res.data["manifest"]).read_text())
    assert manifest["generator"] == GENERATOR
    assert manifest["video_sha256"] == hashlib.sha256(video.read_bytes()).hexdigest()


def test_duration_is_the_video_stream_not_the_container(tmp_path):
    # 3 s of picture, 6 s of sound: the container says 6 s
    pic = _ramp_video(tmp_path / "pic.mp4", seconds=3.0)
    video = tmp_path / "longsound.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(pic), "-f", "lavfi",
                    "-i", "sine=f=440:d=6", "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", str(video)], check=True)
    res = MuteReview().execute({"operation": "sheets", "input_path": str(video),
                                "output_dir": str(tmp_path / "sheets")})
    assert res.success, res.error
    manifest = json.loads(Path(res.data["manifest"]).read_text())
    assert manifest["duration"] == pytest.approx(3.0, abs=0.05)
    assert set(manifest["sheets"]) == {"s00", "s02"}


def test_beat_beyond_the_end_is_refused(tmp_path):
    video = _ramp_video(tmp_path / "ramp.mp4", seconds=2.0)
    late = [{"id": "late", "label": "late", "start": 8.0, "end": 8.3, "expected": "e"}]
    res = MuteReview().execute({"operation": "sheets", "input_path": str(video),
                                "output_dir": str(tmp_path / "sheets"), "beats": late})
    assert not res.success
    assert "late" in res.error and "after the end" in res.error


def test_sheets_of_an_earlier_run_are_removed(tmp_path):
    video = _ramp_video(tmp_path / "ramp.mp4", seconds=2.0)
    out = tmp_path / "sheets"
    first = MuteReview().execute({"operation": "sheets", "input_path": str(video),
                                  "output_dir": str(out), "beats": BEATS})
    assert first.success and (out / "d_blink_1.png").exists()
    second = MuteReview().execute({"operation": "sheets", "input_path": str(video), "output_dir": str(out)})
    assert second.success
    assert sorted(p.name for p in out.glob("*.png")) == ["s00.png"]


def test_unsafe_beat_id_is_refused(tmp_path):
    video = _ramp_video(tmp_path / "ramp.mp4", seconds=2.0)
    bad = [{"id": "../../escape", "label": "x", "start": 1.0, "end": 1.2, "expected": "e"}]
    res = MuteReview().execute({"operation": "sheets", "input_path": str(video),
                                "output_dir": str(tmp_path / "sheets"), "beats": bad})
    assert not res.success
    assert "id" in res.error
    assert not list(tmp_path.rglob("*escape*"))


def test_sheet_ids_are_unique_when_a_sheet_is_not_whole_seconds():
    plan = MuteReview.plan_sheets(2.0, [], fps=4, seconds_per_sheet=0.5, cells=2)
    ids = [s["id"] for s in plan]
    assert len(ids) == 4 and len(set(ids)) == 4, ids


def test_frames_left_by_an_earlier_run_do_not_pass_for_fresh_ones(tmp_path, monkeypatch):
    video = _ramp_video(tmp_path / "ramp.mp4", seconds=2.0)
    args = {"operation": "sheets", "input_path": str(video), "output_dir": str(tmp_path / "sheets")}
    assert MuteReview().execute(args).success
    # second run: ffmpeg "succeeds" but writes nothing
    monkeypatch.setattr(FrameSampler, "run_command", lambda self, cmd, **kw: None)
    res = MuteReview().execute(args)
    assert not res.success
    assert "could not be extracted" in res.error
