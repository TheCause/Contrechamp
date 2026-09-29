"""Real-ffmpeg tests for fixes adopted from upstream pull requests that came
without one (#554 video_trimmer range, #555 vintage_film profile)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.enhancement.color_grade import PROFILES, ColorGrade  # noqa: E402
from tools.video.video_trimmer import VideoTrimmer  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,start_time",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return {k: float(v) for k, v in json.loads(out)["format"].items()}


def _luma(path: Path, t: float) -> float:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", "scale=8:8,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return sum(raw) / len(raw)


@pytest.fixture(scope="module")
def black_then_white(tmp_path_factory) -> Path:
    """20 s: black for 10 s then white, one keyframe every 10 s (a long GOP)."""
    out = tmp_path_factory.mktemp("trim") / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=160x90:r=25:d=10",
                    "-f", "lavfi", "-i", "color=c=white:s=160x90:r=25:d=10",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
                    "-c:v", "libx264", "-g", "250", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def test_trim_returns_the_requested_range_starting_at_zero(black_then_white, tmp_path):
    out = tmp_path / "cut.mp4"
    r = VideoTrimmer().execute({"operation": "cut", "input_path": str(black_then_white),
                                "output_path": str(out), "start_seconds": 8, "end_seconds": 12})
    assert r.success, r.error
    info = _probe(out)
    assert info["start_time"] < 0.1, info            # timeline restarts at zero
    assert abs(info["duration"] - 4.0) < 0.2, info    # 8-12 s, not 8-18 s
    assert _luma(out, 1.0) < 40 and _luma(out, 3.0) > 200   # black 8-10, white 10-12


def test_trim_from_zero_keeps_the_fast_copy_path(black_then_white, tmp_path):
    out = tmp_path / "head.mp4"
    r = VideoTrimmer().execute({"operation": "cut", "input_path": str(black_then_white),
                                "output_path": str(out), "start_seconds": 0, "end_seconds": 10})
    assert r.success, r.error
    assert abs(_probe(out)["duration"] - 10.0) < 0.2


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_every_colour_profile_renders(profile, tmp_path):
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=1",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    r = ColorGrade().execute({"input_path": str(src), "output_path": str(tmp_path / "out.mp4"),
                              "profile": profile})
    assert r.success, f"{profile}: {r.error}"
