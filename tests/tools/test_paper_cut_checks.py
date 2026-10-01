"""Paper-cut style: the checks bite on a real defect and stay silent on the healthy case.

The rendering checks themselves need Chrome (run scripts/paper_cut_checks.py on a
render machine); here we test their verdict logic, the render gate on REAL frames
(one rendered with gsap unreachable, one healthy) and the static guards.
"""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INK = ROOT / "ink-theater"
EXAMPLES = [INK / "examples" / "paper-cut-sunrise", INK / "examples" / "paper-cut-face"]
FIX = ROOT / "tests" / "fixtures" / "paper_cut"

_spec = importlib.util.spec_from_file_location("paper_cut_checks", ROOT / "scripts" / "paper_cut_checks.py")
checks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checks)


def test_identical_frames_pass(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"same pixels")
    b.write_bytes(b"same pixels")
    assert checks.compare(a, b)["verdict"] == "pass"


def test_one_differing_byte_fails(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"same pixels")
    b.write_bytes(b"same pixelz")
    assert checks.compare(a, b)["verdict"] == "fail"


def test_missing_frame_is_not_checked_never_pass(tmp_path):
    a = tmp_path / "a.png"
    a.write_bytes(b"x")
    assert checks.compare(a, tmp_path / "absent.png")["verdict"] == "not_checked"
    assert checks.compare(None, a)["verdict"] == "not_checked"


def test_one_mismatching_time_fails_the_session(tmp_path):
    same, other = tmp_path / "s.png", tmp_path / "o.png"
    same.write_bytes(b"1")
    other.write_bytes(b"2")
    r = checks.compare_sessions({1.0: same, 2.0: same}, {1.0: same, 2.0: other}, [1.0, 2.0])
    assert r["verdict"] == "fail" and r["mismatch_at"] == ["2"]


def test_gate_fails_on_the_real_offline_frame(tmp_path):
    # Rendered with gsap unreachable: uniform page. Twice "the same" must NOT pass.
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    shutil.copy(FIX / "offline_frame.png", a)
    shutil.copy(FIX / "offline_frame.png", b)
    assert checks.gate({1.0: a, 2.0: b}, [1.0, 2.0])["verdict"] == "fail"


def test_gate_fails_on_a_flat_png(tmp_path):
    from PIL import Image
    p = tmp_path / "flat.png"
    Image.new("RGB", (108, 192), (224, 224, 224)).save(p)
    assert checks.blank_frames([p])


def test_gate_fails_when_the_scene_does_not_move(tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    shutil.copy(FIX / "healthy_frame.jpg", a)
    shutil.copy(FIX / "healthy_frame.jpg", b)
    r = checks.gate({1.0: a, 2.0: b}, [1.0, 2.0])
    assert r["verdict"] == "fail" and "did not move" in r["reason"]


def test_gate_passes_on_real_moving_frames(tmp_path):
    from PIL import Image
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    im = Image.open(FIX / "healthy_frame.jpg")
    im.save(a)
    im.transpose(Image.FLIP_LEFT_RIGHT).save(b)
    assert checks.gate({1.0: a, 2.0: b}, [1.0, 2.0])["verdict"] == "pass"


def test_two_pixel_difference_still_fails_and_is_described(tmp_path):
    from PIL import Image
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    im = Image.open(FIX / "healthy_frame.jpg").convert("RGB")
    im.save(a)
    im.putpixel((10, 10), (0, 0, 0))
    im.putpixel((20, 20), (0, 0, 0))
    im.save(b)
    r = checks.compare(a, b)
    assert r["verdict"] == "fail" and r["pixels_differing"] == 2


def test_seek_skipped_output_is_detected():
    out = "◆  Capturing 2 frames\n   ⚠ No player API — seeks will be skipped\n"
    assert checks.seek_warnings(out)
    assert checks.seek_warnings("◇  2 snapshots saved") == []


def test_interleave_jumps_both_ways():
    assert checks.interleave([1, 2, 3, 4, 5]) == [1, 5, 2, 4, 3]


def test_scanner_flags_runtime_randomness():
    assert checks.forbidden_calls("var j = Math.random() * 2;") == ["Math.random("]
    assert checks.forbidden_calls("var t = Date.now();")
    assert checks.forbidden_calls("var t = performance.now ();")


def test_scanner_silent_on_seeded_code():
    assert checks.forbidden_calls("var r = InkTheater.rng(7); var j = r() * 2; // no Math.random here") == []


def test_paper_cut_sources_have_no_runtime_randomness():
    for f in [INK / "paper-cut.js"] + [ex / "index.html" for ex in EXAMPLES]:
        assert checks.forbidden_calls(f.read_text(encoding="utf-8")) == [], f


def test_examples_bundle_the_current_engine():
    for ex in EXAMPLES:
        for name in ["ink-theater.js", "paper-cut.js"]:
            assert (ex / name).read_bytes() == (INK / name).read_bytes(), f"{ex.name}/{name} copy drifted"
        assert (ex / "gsap.min.js").is_symlink(), "use the repo's vendored gsap via a link, do not add a copy"
        assert (ex / "gsap.min.js").resolve().is_file()


def test_ink_sketch_example_still_bundles_unchanged_engine():
    mocap = INK / "examples" / "mocap-figure"
    assert (mocap / "ink-theater.js").read_bytes() == (INK / "ink-theater.js").read_bytes()
