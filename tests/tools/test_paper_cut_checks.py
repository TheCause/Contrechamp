"""Paper-cut style: the checks bite on a real defect and stay silent on the healthy case.

The rendering checks themselves need Chrome (run scripts/paper_cut_checks.py on a
render machine); here we test their verdict logic and the static guards.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INK = ROOT / "ink-theater"
EXAMPLE = INK / "examples" / "paper-cut-sunrise"

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


def test_scanner_flags_runtime_randomness():
    assert checks.forbidden_calls("var j = Math.random() * 2;") == ["Math.random("]
    assert checks.forbidden_calls("var t = Date.now();")
    assert checks.forbidden_calls("var t = performance.now ();")


def test_scanner_silent_on_seeded_code():
    assert checks.forbidden_calls("var r = InkTheater.rng(7); var j = r() * 2; // no Math.random here") == []


def test_paper_cut_sources_have_no_runtime_randomness():
    for f in [INK / "paper-cut.js", EXAMPLE / "index.html"]:
        assert checks.forbidden_calls(f.read_text(encoding="utf-8")) == [], f


def test_example_bundles_the_current_engine():
    for name in ["ink-theater.js", "paper-cut.js"]:
        assert (EXAMPLE / name).read_bytes() == (INK / name).read_bytes(), f"{name} copy drifted"


def test_ink_sketch_example_still_bundles_unchanged_engine():
    mocap = INK / "examples" / "mocap-figure"
    assert (mocap / "ink-theater.js").read_bytes() == (INK / "ink-theater.js").read_bytes()
