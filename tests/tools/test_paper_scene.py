"""paper_scene: every pre-render check fails on a real defect and stays silent on the healthy case.

Fixtures: tests/fixtures/paper_scene/factory.json (use case 1, the factory, transcribed)
and usage2_skeleton.json (a skeleton of use case 2: two layouts, narration marks,
repeated turns). The small scenes below are built per test, one defect at a time.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.paper_scene import compile_scene, load, validate
from lib.paper_scene import motion as M

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "paper_scene"
FACTORY = FIX / "factory.json"
USAGE2 = FIX / "usage2_skeleton.json"


def tiny(**over):
    """A healthy 4 s scene: a worker, a box, a crane and a sun; every check passes."""
    s = {
        "format": "paper_scene/1", "id": "tiny", "brief": "A worker next to a box.", "duration": 4.0,
        "layouts": {"portrait": {"aspect": "9:16", "width": 1080, "height": 1920, "ui_safe_bottom": 0}},
        "props": [
            {"id": "ground", "type": "shape", "params": {"shape": "rect", "rect": [0, 1500, 1080, 420]}},
            {"id": "box", "type": "shape", "at": [560, 1500], "params": {"shape": "rect", "rect": [-40, -80, 80, 80]}},
            {"id": "crane", "type": "crane", "params": {"mast_x": 900, "jib_y": 600, "jib_x0": 300, "jib_x1": 1000,
                                                        "floor": 1500}, "init": {"trolley": 700, "hook_y": 1100}},
            {"id": "sun", "type": "sun", "at": [700, 1300], "params": {"r": 95}},
        ],
        "characters": [{"id": "w", "at": [460, 1500], "scale": 1.0}],
        "order": ["ground", "crane", "sun", "box", "w"],
        "camera": {"portrait": [{"t": 0, "center": [540, 960], "zoom": 1.0}]},
        "actions": [],
    }
    s.update(over)
    return copy.deepcopy(s)


def issues(report, check):
    return report["checks"][check]["issues"]


# ------------------------------------------------------------------ the two cases

def test_factory_passes_every_check():
    rep = validate(load(FACTORY))
    assert rep["status"] == "pass", rep["errors"]
    assert all(c["status"] == "pass" for c in rep["checks"].values())


def test_usage2_skeleton_is_a_valid_scene():
    from schemas.artifacts import validate_artifact

    scene = load(USAGE2)
    validate_artifact("paper_scene", scene)
    rep = validate(scene)
    assert rep["status"] == "pass", rep["errors"]
    assert rep["duration"] == pytest.approx(19.62)          # calée sur la fin de la phrase 24
    assert set(scene["layouts"]) == {"landscape", "portrait"}


def test_usage2_turn_n_carries_n_items(tmp_path):
    res = compile_scene(load(USAGE2), tmp_path)
    ev = json.loads((tmp_path / "sfx_events.json").read_text())
    assert ev["peak_dbfs"] == -18.0                           # one gain for the ducked layer
    coins = [e for e in ev["events"] if e["kind"] == "marimba"]
    flips = [e for e in ev["events"] if e["kind"] == "click" and e.get("gain") == 0.2]
    assert len(coins) == 1 + 2 + 3 + 4 + 5 and len(flips) == 15
    turn3 = [e["freq"] for e in coins[3:6]]
    assert turn3[0] < turn3[1] < turn3[2]                     # pitch rises with the coin count
    assert res["layouts"] == ["landscape", "portrait"]


# ------------------------------------------------------------------ vocabulary

def test_unknown_prop_type_lists_the_known_types():
    s = tiny()
    s["props"][1]["type"] = "trolley"
    rep = validate(s)
    msg = " ".join(issues(rep, "vocabulary"))
    assert "unknown prop type 'trolley'" in msg and "container" in msg and "stack" in msg
    assert rep["checks"]["off_frame"]["status"] == "not_checked"   # never pass after a failed stage


def test_unknown_action_and_parameter_list_the_known_names():
    s = tiny(actions=[{"do": "jump", "actor": "w", "start": 0, "end": 1},
                      {"do": "walk_to", "actor": "w", "to": 500, "speed": 2, "start": 0, "end": 1}])
    msg = " ".join(issues(validate(s), "vocabulary"))
    assert "unknown action 'jump'" in msg and "walk_to" in msg and "shrug" in msg
    assert "unknown parameter 'speed'" in msg


def test_healthy_tiny_scene_passes():
    s = tiny(actions=[{"do": "walk_to", "actor": "w", "to": 400, "start": 0.5, "end": 1.5}])
    assert validate(s)["status"] == "pass"


# ------------------------------------------------------------------ references, timing

def test_absent_actor_and_unknown_anchor_are_errors():
    s = tiny(actions=[{"do": "walk_to", "actor": "ghost", "to": 400, "start": 0, "end": 1},
                      {"do": "reach", "actor": "w", "target": "box.handle", "start": 0, "end": 1}])
    msg = " ".join(issues(validate(s), "references"))
    assert "unknown character 'ghost'" in msg
    assert "box.handle" in msg and "center" in msg            # lists the anchors the box has


def test_time_outside_the_scene_and_unknown_mark():
    s = tiny(actions=[{"do": "walk_to", "actor": "w", "to": 400, "start": 3.5, "end": 4.5},
                      {"do": "walk_to", "actor": "w", "to": 300, "start": "p9.start", "end": 2}],
             marks={"p1": [0, 2]})
    msg = " ".join(issues(validate(s), "timing"))
    assert "outside the scene" in msg and "unknown mark 'p9'" in msg and "p1" in msg


# ------------------------------------------------------------------ limb overlap

def test_two_actions_on_the_same_arm_at_once_fail():
    s = tiny(actions=[{"do": "pose", "actor": "w", "set": {"arms": [0, 0, 90, 90]}, "start": 0.5, "end": 1.5},
                      {"do": "wave", "actor": "w", "hand": "R", "start": 1.0, "end": 2.0}])
    msg = " ".join(issues(validate(s), "limb_overlap"))
    assert "w.armR_u" in msg and "a0" in msg and "a1" in msg


def test_the_same_arm_one_action_after_the_other_passes():
    s = tiny(actions=[{"do": "pose", "actor": "w", "set": {"arms": [0, 0, 90, 90]}, "start": 0.5, "end": 1.0},
                      {"do": "wave", "actor": "w", "hand": "R", "start": 1.0, "end": 2.0}])
    assert issues(validate(s), "limb_overlap") == []


# ------------------------------------------------------------------ trap 1: jumps

def _push(offset):
    a = {"do": "push", "actor": "w", "target": "box", "to": 700, "start": 1.0, "end": 2.5}
    if offset is not None:
        a["offset"] = offset
    return tiny(actions=[a])


def test_push_from_where_the_actor_is_not_jumps():
    # the worker stands 100 px left of the box; the push puts him 150 px left: he would jump 50 px
    msg = " ".join(issues(validate(_push(-150)), "jump"))
    assert "w.x jumps by 50.0 at 1.00s" in msg and "walk him there first" in msg


def test_push_from_where_the_actor_stands_does_not_jump():
    assert issues(validate(_push(-100)), "jump") == []
    assert issues(validate(_push(None)), "jump") == []


def test_a_pose_with_no_duration_snaps():
    s = tiny(actions=[{"do": "pose", "actor": "w", "set": {"arms": [-12, -6, 120, 150]}, "start": 1.0, "end": 1.0}])
    assert "give it a duration" in " ".join(issues(validate(s), "jump"))
    s["actions"][0]["end"] = 1.4
    assert issues(validate(s), "jump") == []


def test_an_oscillation_after_a_reach_starts_where_the_arm_is():
    # regression: the oscillation's base was read before the reach before it was solved
    s = tiny(actions=[{"do": "reach", "actor": "w", "hand": "R", "target": "box.top", "start": 0.5, "end": 1.0},
                      {"do": "wave", "actor": "w", "hand": "R", "start": 1.0, "end": 2.0}])
    assert issues(validate(s), "jump") == []


# ------------------------------------------------------------------ trap 5: off frame

def _zoomed(to):
    return tiny(camera={"portrait": [{"t": 0, "center": [540, 1100], "zoom": 2.0}]},
                actions=[{"do": "walk_to", "actor": "w", "to": to, "start": 1.0, "end": 2.0}])


def test_an_action_outside_the_current_camera_frame_fails():
    # zoom 2 on x 540: the frame is x 270..810; walking to 900 leaves it
    msg = " ".join(issues(validate(_zoomed(900)), "off_frame"))
    assert "w (walking)" in msg and "outside the camera frame" in msg


def test_an_action_inside_the_camera_frame_passes():
    assert issues(validate(_zoomed(700)), "off_frame") == []


# ------------------------------------------------------------------ trap 6: contact

def _hook(hook_y):
    return tiny(actions=[{"do": "attach", "target": "sun", "to": "crane.hook", "by": "top", "start": 1.0,
                          "end": 2.0}],
                props=[p if p["id"] != "crane" else dict(p, init={"trolley": 700, "hook_y": hook_y})
                       for p in tiny()["props"]])


def test_a_hook_that_stops_above_the_load_is_contact_not_shown():
    # sun top at 1300 - 95 = 1205; hook point = hook_y + 20: 25 px above with hook_y 1160
    msg = " ".join(issues(validate(_hook(1160)), "contact"))
    assert "contact not shown" in msg and "25 px" in msg


def test_a_hook_that_touches_the_load_passes():
    assert issues(validate(_hook(1185)), "contact") == []


def _reach(box_x):
    return tiny(props=[p if p["id"] != "box" else dict(p, at=[box_x, 1500]) for p in tiny()["props"]],
                actions=[{"do": "reach", "actor": "w", "hand": "R", "target": "box.top", "start": 1.0, "end": 1.5}])


def test_a_hand_that_cannot_reach_its_target_fails():
    msg = " ".join(issues(validate(_reach(700)), "contact"))
    assert "w's R hand on box.top" in msg


def test_a_hand_within_reach_touches_its_target():
    assert issues(validate(_reach(520)), "contact") == []


# ------------------------------------------------------------------ trap 7: zoom speed

def _cam(keys):
    return tiny(camera={"portrait": [{"t": t, "center": [540, 960], "zoom": z} for t, z in keys]})


def test_a_strong_zoom_faster_than_1_2_s_fails():
    msg = " ".join(issues(validate(_cam([(0, 1.0), (1.0, 1.0), (1.85, 1.6)])), "zoom_speed"))
    assert "x1.60 in 0.85s" in msg


def test_a_strong_zoom_over_1_2_s_passes():
    assert issues(validate(_cam([(0, 1.0), (1.0, 1.0), (2.2, 1.6)])), "zoom_speed") == []


def test_a_fast_zoom_is_not_excused_by_a_slow_end_of_the_same_move():
    # x1.62 in 1.1 s, then a slow creep to 1.66: the fast part is not excused
    rep = validate(_cam([(0, 1.0), (2.0, 1.0), (3.1, 1.62), (4.0, 1.66)]))
    assert "x1.62 in 1.10s" in " ".join(issues(rep, "zoom_speed"))


# ------------------------------------------------------------------ trap 8: 9:16 bottom band

def _bottom(ground, declare=True):
    s = tiny(actions=[{"do": "shrug", "actor": "w", "start": 1.0, "end": 1.5}],
             characters=[{"id": "w", "at": [460, ground], "scale": 1.0}])
    if declare:
        s["layouts"]["portrait"]["ui_safe_bottom"] = 0.2
    else:
        del s["layouts"]["portrait"]["ui_safe_bottom"]
    return s


def test_an_action_in_the_bottom_fifth_of_a_reels_frame_fails():
    msg = " ".join(issues(validate(_bottom(1880)), "ui_safe_bottom"))
    assert "inside the bottom 20%" in msg


def test_an_action_above_the_bottom_fifth_passes():
    assert issues(validate(_bottom(1500)), "ui_safe_bottom") == []


def test_a_9_16_layout_must_declare_its_bottom_band():
    assert "must declare ui_safe_bottom" in " ".join(issues(validate(_bottom(1500, declare=False)),
                                                            "ui_safe_bottom"))


# ------------------------------------------------------------------ use case 2 checks

def _beat(start, end):
    return tiny(marks={"p1": [0.0, 2.0], "p2": [2.0, 4.0]},
                beats=[{"id": "b", "label": "b", "start": start, "end": end, "expected": "x", "phrase": "p1"}])


def test_a_beat_outside_its_phrase_fails():
    assert "not inside the phrase it illustrates, p1" in " ".join(issues(validate(_beat(1.5, 2.4)),
                                                                        "beat_in_phrase"))


def test_a_beat_inside_its_phrase_passes():
    assert issues(validate(_beat(0.5, 2.0)), "beat_in_phrase") == []


def _label(text, allowed):
    s = tiny()
    s["props"].append({"id": "lab", "type": "label", "at": [300, 400], "params": {"text": text}})
    s["order"].append("lab")
    if allowed is not None:
        s["text_allowed"] = allowed
    return s


def test_a_number_the_narration_does_not_say_is_refused():
    assert "'42 €' is not in text_allowed" in " ".join(issues(validate(_label("42 €", ["tour 1"])), "text_policy"))
    assert "list every allowed text" in " ".join(issues(validate(_label("tour 1", None)), "text_policy"))


def test_declared_labels_pass():
    assert issues(validate(_label("tour 1", ["tour 1"])), "text_policy") == []


# ------------------------------------------------------------------ the three outputs

def test_factory_outputs_agree_with_lots_b_and_c(tmp_path):
    from tools.audio.sfx_synth import validate_events

    res = compile_scene(load(FACTORY), tmp_path)
    assert res["report"]["status"] == "pass"
    beats = json.loads((tmp_path / "story_beats.json").read_text())["beats"]
    ref = json.loads((ROOT / "tests" / "fixtures" / "mute_review" / "paper_factory_beats.json").read_text())["beats"]
    key = lambda b: (b["id"], b["start"], b["end"], b["expected"])   # noqa: E731
    assert [key(b) for b in beats] == [key(b) for b in ref]
    from lib.mute_review import load_beats
    load_beats({"beats": beats})
    ev = json.loads((tmp_path / "sfx_events.json").read_text())
    assert validate_events(ev["events"], ev["duration_seconds"])
    html = (tmp_path / "portrait" / "index.html").read_text()
    assert 'data-duration="20"' in html and 'data-width="1080"' in html
    for name in ("ink-theater.js", "paper-cut.js", "paper-scene.js"):
        assert (tmp_path / "portrait" / name).read_bytes() == (ROOT / "ink-theater" / name).read_bytes()
    gsap = tmp_path / "portrait" / "gsap.min.js"
    assert gsap.is_symlink() and gsap.resolve().is_file()


def test_compile_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    compile_scene(load(FACTORY), a)
    compile_scene(load(FACTORY), b)
    for rel in ("portrait/index.html", "sfx_events.json", "story_beats.json", "scene.resolved.json"):
        assert (a / rel).read_bytes() == (b / rel).read_bytes(), rel


def test_a_failing_scene_writes_no_project(tmp_path):
    res = compile_scene(_push(-150), tmp_path)
    assert res["report"]["status"] == "fail"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report.json"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed: the render evaluator is not compared")
def test_render_evaluator_matches_the_checks_evaluator(tmp_path):
    """The checks look at the motion that is rendered: same channels, same camera, both languages."""
    compile_scene(load(FACTORY), tmp_path)
    html = (tmp_path / "portrait" / "index.html").read_text()
    data = json.loads(re.search(r"window.__PAPER_SCENE__ = (\{.*?\});\n", html, re.S).group(1))
    (tmp_path / "compiled.json").write_text(json.dumps(data))
    times = [round(i * 0.0731, 4) for i in range(275)]
    (tmp_path / "times.json").write_text(json.dumps(times))
    out = subprocess.run(["node", str(FIX / "eval_channels.js"), str(ROOT / "ink-theater" / "paper-scene.js"),
                          str(tmp_path / "compiled.json"), str(tmp_path / "times.json")],
                         capture_output=True, text=True, check=True).stdout
    js = json.loads(out)
    ch = M.Channels()
    ch.ch = data["ch"]
    compared = 0
    for key in data["ch"]:
        for t, v in zip(times, js["channels"][key]):
            p = ch.value(key, t)
            if isinstance(p, (int, float)):
                assert abs(p - v) < 1e-9, (key, t, p, v)
                compared += 1
            elif not (isinstance(p, str) and p.startswith("#")):
                assert p == v, (key, t, p, v)
    assert compared > 50000
    for t, cam in zip(times, js["camera"]):
        assert M.camera_at(data["camera"], t, 1080, 1920) == pytest.approx(cam, abs=1e-9)


# ------------------------------------------------------------------ the tool

def test_tool_is_registered_and_validates():
    from tools.tool_registry import registry

    registry.discover()
    tool = registry.get("paper_scene")
    assert tool is not None and tool.get_info()["status"] == "available"
    ok = tool.execute({"operation": "validate", "scene": str(FACTORY)})
    assert ok.success and ok.data["report"]["status"] == "pass"
    bad = tool.execute({"operation": "validate", "scene": _push(-150)})
    assert not bad.success and "jumps" in bad.error


def test_tool_compile_refuses_a_failing_scene(tmp_path):
    from tools.video.paper_scene import PaperScene

    r = PaperScene().execute({"operation": "compile", "scene": _push(-150), "output_dir": str(tmp_path)})
    assert not r.success and not (tmp_path / "portrait").exists()

