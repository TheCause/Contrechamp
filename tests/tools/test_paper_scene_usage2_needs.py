"""paper_scene, second pass: what the first real author of use case 2 could not express,
and what two blind reviews of that scene could not read."""
from __future__ import annotations

import copy
import json
import math
import shutil
import subprocess

import pytest

from lib.paper_scene import validate
from lib.paper_scene.build import Build
from lib.paper_scene.compile import char_point, char_state, ref_point
from tests.tools.test_paper_scene import ROOT, issues, tiny


def _repeat(actions, spans=((0.2, 1.2), (1.2, 2.2), (2.2, 3.2))):
    return {"repeat": {"name": "turn", "spans": [list(s) for s in spans]}, "actions": actions}


# ------------------------------------------------------- 1. arithmetic on $n

def test_a_value_can_be_computed_from_the_turn_number():
    s = tiny(actions=[_repeat([{"do": "pose", "actor": "w", "set": {"lean": "2*$n+1", "brow": "min($n, 2)/2"},
                                "start": 0.0, "end": "0.2*$n+0.3"}])])
    b = Build(s)
    assert b.report()["status"] == "pass", b.report()["errors"]
    c = b.layouts["portrait"]
    assert c.ch.value("w.lean", 3.2) == pytest.approx(7.0)        # 2*3+1 after turn 3
    assert c.ch.value("w.brow", 3.2) == pytest.approx(1.0)        # min(3, 2)/2
    ends = sorted(a["end"] for a in b.actions)
    assert ends == pytest.approx([0.2 + 0.5, 1.2 + 0.7, 2.2 + 0.9])   # duration grows with the turn


def test_pitch_can_follow_the_turn():
    s = tiny(actions=[_repeat([{"do": "shrug", "actor": "w", "start": 0, "end": 0.5,
                                "sound": [{"kind": "marimba", "at": 0, "freq": "392+$n*20"}]}])])
    b = Build(s)
    assert b.report()["status"] == "pass", b.report()["errors"]
    assert [e["freq"] for e in b.events] == [412, 432, 452]


@pytest.mark.parametrize("expr", ["__import__('os').system('x')", "$n+__import__('os')", "$n.real", "().__class__",
                                  "$n**9", "foo($n)", "$n+", "max($n)", "1/0*$n"])
def test_anything_beyond_the_arithmetic_is_refused(expr):
    s = tiny(actions=[_repeat([{"do": "pose", "actor": "w", "set": {"lean": expr}, "start": 0, "end": 0.5}])])
    rep = validate(s)
    assert rep["status"] == "fail" and rep["checks"]["vocabulary"]["status"] == "fail"


# ------------------------------------------------------- 2. per-layout target and position

def _two_layouts():
    return tiny(layouts={"portrait": {"aspect": "9:16", "width": 1080, "height": 1920, "ui_safe_bottom": 0},
                         "wide": {"aspect": "16:9", "width": 1920, "height": 1080}},
                camera={"portrait": [{"t": 0, "center": [540, 960], "zoom": 1}],
                        "wide": [{"t": 0, "center": [960, 540], "zoom": 1}]},
                props=[{"id": "box", "type": "shape", "at": [560, 1500], "params": {"shape": "rect", "rect": [-40, -80, 80, 80]},
                        "anchors": {"grip": [-40, -70]},
                        "layouts": {"wide": {"at": [560, 900], "anchors": {"grip": [-40, -76]}, "init": {"opacity": 0.5}}}},
                       {"id": "board", "type": "shape", "at": [800, 1200], "params": {"shape": "rect", "rect": [0, 0, 200, 100]},
                        "layouts": {"wide": {"at": [1500, 200]}}}],
                characters=[{"id": "w", "at": [460, 1500], "layouts": {"wide": {"at": [460, 900]}}}],
                order=["box", "board", "w"])


def test_an_action_can_aim_at_another_point_in_another_layout():
    s = _two_layouts()
    s["actions"] = [{"do": "point_at", "actor": "w", "hand": "R", "target": "board.center", "start": 1, "end": 1.4,
                     "layouts": {"wide": {"target": "box.top"}}},
                    {"do": "reach", "actor": "w", "hand": "R", "target": "box.grip", "start": 2, "end": 2.4}]
    b = Build(s)
    assert b.report()["status"] == "pass", b.report()["errors"]
    p, w = b.layouts["portrait"], b.layouts["wide"]
    assert p.ch.value("w.armR_u", 1.5) != pytest.approx(w.ch.value("w.armR_u", 1.5), abs=5)
    assert ref_point(w, "box.grip", 2.4) == pytest.approx((520, 824))     # the anchor of that layout
    assert w.ch.value("box.opacity", 0) == 0.5 and p.ch.value("box.opacity", 0) == 1.0


@pytest.mark.parametrize("key", ["start", "end", "count", "do", "sound"])
def test_a_layout_cannot_change_when_or_how_much(key):
    s = _two_layouts()
    s["actions"] = [{"do": "shrug", "actor": "w", "start": 1, "end": 1.4, "layouts": {"wide": {key: 2}}}]
    assert "layout override" in " ".join(issues(validate(s), "vocabulary"))


# ------------------------------------------------------- 3. pointing that designates

def _point(then_walk=False):
    s = tiny(props=tiny()["props"] + [{"id": "board", "type": "shape", "at": [800, 1000],
                                       "params": {"shape": "rect", "rect": [0, 0, 200, 100]}}])
    s["order"].append("board")
    s["actions"] = [{"do": "pose", "actor": "w", "set": {"lean": 15}, "start": 0.2, "end": 0.6},
                    {"do": "point_at", "actor": "w", "hand": "R", "target": "board.center", "start": 1.0, "end": 1.4}]
    if then_walk:
        s["actions"].append({"do": "walk_to", "actor": "w", "to": 0, "start": 1.45, "end": 1.75})
    return s


def test_pointing_stretches_the_arm_turns_the_head_and_straightens_the_body():
    b = Build(_point())
    assert b.report()["status"] == "pass", b.report()["errors"]
    c = b.layouts["portrait"]
    st = char_state(c, "w", 1.4)
    assert st["armR_u"] == pytest.approx(st["armR_f"])        # one straight line, elbow not bent
    assert st["lean"] == pytest.approx(0, abs=0.5)            # the body stands up to point
    assert st["turn"] > 0.3 and st["gaze_y"] < 0              # head and eyes towards the board (up right)
    sx, sy = char_point(c, "w", "hand_R", 1.4)
    hx, hy = char_point(c, "w", "head", 1.4)
    tx, ty = ref_point(c, "board.center", 1.4)
    assert b.measured["pointing"]["portrait"][0]["error_deg"] < 3


def test_pointing_almost_straight_up_fails():
    s = _point()
    s["props"][-1]["at"] = [430, 900]          # the board right above his head
    assert "almost straight up" in " ".join(issues(validate(s), "pointing"))


def test_pointing_that_drifts_off_its_target_while_held_fails():
    msg = " ".join(issues(validate(_point(then_walk=True)), "pointing"))
    assert "off" in msg and "board" in msg


# ------------------------------------------------------- 4. seated (kinematics in both languages)

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_seated_worker_has_horizontal_thighs_and_bent_knees():
    js = r"""
    const vm = require('vm'), fs = require('fs');
    const ctx = {window: {}, InkTheater: {rng: () => () => 0.5, el: () => ({})}};
    ctx.window = ctx; vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
    const po = ctx.PaperCut.workerPose({seated: true, facing: 1});
    process.stdout.write(JSON.stringify(po));
    """
    po = json.loads(subprocess.run(["node", "-e", js, str(ROOT / "ink-theater" / "paper-cut.js")], capture_output=True,
                                   text=True, check=True).stdout)
    for s in ("L", "R"):
        hip, knee, foot = po["hip" + s], po["kn" + s], po["ft" + s]
        assert abs(knee[1] - hip[1]) < 1                         # thigh horizontal
        assert knee[0] - hip[0] > 12                             # towards where he faces
        assert abs(foot[0] - knee[0]) < 3 and foot[1] == 0       # shin down to the floor
        assert -26 < hip[1] < -14                                # pelvis low, at seat height
    from lib.paper_scene import motion as M
    assert M.SEAT_DROP == pytest.approx(42 + hip[1])            # Python hands use the same seat


# ------------------------------------------------------- 8. counted objects big enough to count

def _pile(sheet, jar_w):
    s = tiny()
    s["props"] += [{"id": "pile", "type": "stack", "at": [300, 1500], "params": {"w": 80, "sheet": sheet}},
                   {"id": "jar", "type": "container", "at": [800, 1500], "params": {"item": "coin", "w": jar_w, "h": 70,
                                                                                   "capacity": 10, "lid": None}}]
    s["order"] += ["pile", "jar"]
    s["actions"] = [{"do": "stack_add", "target": "pile", "from": [100, 1300], "start": 0.5, "end": 1.0},
                    {"do": "drop_in", "target": "jar", "count": 2, "from": [800, 1300], "start": 1.0, "end": 2.0}]
    return s


def test_counted_objects_too_small_on_screen_fail():
    msg = " ".join(issues(validate(_pile(5, 50)), "min_size"))
    assert "pile" in msg and "jar" in msg


def test_counted_objects_large_enough_pass():
    assert issues(validate(_pile(9, 70)), "min_size") == []


# ------------------------------------------------------- 9. the opening fade

def test_a_beat_that_starts_during_the_opening_fade_fails():
    s = tiny(style={"fade_in": 0.45}, beats=[{"id": "b", "label": "b", "start": 0.0, "end": 0.6, "expected": "x"}])
    assert "opening fade" in " ".join(issues(validate(s), "timing"))


def test_no_fade_or_a_later_beat_passes():
    s = tiny(style={"fade_in": 0}, beats=[{"id": "b", "label": "b", "start": 0.0, "end": 0.6, "expected": "x"}])
    assert validate(s)["status"] == "pass"
    s = tiny(style={"fade_in": 0.45}, beats=[{"id": "b", "label": "b", "start": 0.5, "end": 0.9, "expected": "x"}])
    assert validate(s)["status"] == "pass"
