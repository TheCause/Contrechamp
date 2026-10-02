"""paper_scene, final adversarial review: overrides that change what happens, counts drawn short,
off_frame holes between camera keys, invisible counted objects, pointing at oneself."""
from __future__ import annotations

import copy

import pytest

from lib.paper_scene import load, validate
from lib.paper_scene.expr import ExprError, evaluate
from tests.tools.test_paper_scene import USAGE2, issues, tiny


def _sk():
    return copy.deepcopy(load(USAGE2))


def _prop(s, pid):
    return next(p for p in s["props"] if p["id"] == pid)


# ------------------------------------------------------- 1. a layout changes geometry only

@pytest.mark.parametrize("override", [
    ("jar", {"init": {"count": 12}}),
    ("ghosts", {"init": {"reveal_1": 1}}),
    ("triangle", {"init": {"draw": 1, "fill": 1}}),
    ("sheets", {"init": {"opacity": 0}}),
    ("jar", {"params": {"capacity": 1}}),
    ("jar", {"params": {"item": "star"}}),
    ("strips", {"params": {"labels": ["a", "b", "c", "d", "e"]}}),
    ("strips", {"params": {"color": "#000000"}}),
])
def test_a_layout_override_that_changes_what_happens_is_refused(override):
    s = _sk()
    pid, ov = override
    lay = _prop(s, pid).setdefault("layouts", {}).setdefault("portrait", {})
    for k, v in ov.items():
        lay.setdefault(k, {}).update(v)
    assert "geometry" in " ".join(issues(validate(s), "vocabulary"))


def test_geometry_overrides_still_pass():
    assert validate(_sk())["status"] == "pass"         # the skeleton moves and resizes per layout


# ------------------------------------------------------- 2. what is counted is drawn

def _jar(capacity, drops, spills=0):
    s = tiny()
    s["props"].append({"id": "jar", "type": "container", "at": [600, 1500],
                       "params": {"item": "coin", "w": 72, "h": 80, "capacity": capacity}})
    s["order"].append("jar")
    s["actions"] = [{"do": "drop_in", "target": "jar", "count": drops, "from": [600, 900], "start": 0.5, "end": 2.0}]
    if spills:
        s["actions"].append({"do": "overflow", "target": "jar", "count": spills, "start": 2.2, "end": 3.0})
    return s


def test_more_coins_than_the_jar_draws_fail():
    assert "capacity" in " ".join(issues(validate(_jar(2, 12)), "counts"))


def test_more_spilled_coins_than_drawn_fail():
    assert "overflow" in " ".join(issues(validate(_jar(12, 6, spills=10)), "counts"))


def test_counts_within_what_is_drawn_pass():
    assert issues(validate(_jar(12, 6, spills=4)), "counts") == []


# ------------------------------------------------------- 3. off_frame between camera keys, on the body

def _pan(char_x=None, bars=False, end_y=1300):
    s = tiny(camera={"portrait": [{"t": 0, "center": [540, 960], "zoom": 1.0}, {"t": 1.0, "center": [540, 960], "zoom": 1.0},
                                  {"t": 3.0, "center": [270, end_y], "zoom": 2.0}]})
    if char_x is not None:
        s["characters"] = [{"id": "w", "at": [char_x, 1500]}]
    if bars:
        s["characters"] = [{"id": "w", "at": [300, 1500]}]
        s["props"].append({"id": "bars", "type": "bars", "at": [20, 600], "params": {"n": 2, "width": 30, "gap": 10, "unit": 40}})
        s["order"].append("bars")
        s["actions"] = [{"do": "animate", "target": "bars", "channel": "h_1", "to": 2, "start": 0.2, "end": 0.5}]
    return s


def test_bars_cut_in_the_middle_of_a_camera_move_fail():
    # in at 1 s, cut on the way, in again at 3 s: not a camera carrying it out of the shot
    assert "bars" in " ".join(issues(validate(_pan(bars=True, end_y=960)), "off_frame"))


def test_a_character_out_in_the_middle_of_a_camera_move_fails():
    assert "'w'" in " ".join(issues(validate(_pan(char_x=20)), "off_frame"))


def test_a_character_half_out_at_a_key_fails():
    s = tiny(characters=[{"id": "w", "at": [5, 1500], "scale": 2.0}])
    assert "'w'" in " ".join(issues(validate(s), "off_frame"))


def test_a_character_inside_all_along_the_move_passes():
    assert issues(validate(_pan(char_x=300)), "off_frame") == []


# ------------------------------------------------------- 4. spilled coins, invisible counted objects, min_size

def test_coins_spilled_out_of_the_frame_fail():
    s = tiny()
    s["props"].append({"id": "jar", "type": "container", "at": [1000, 1500],
                       "params": {"item": "coin", "w": 72, "h": 80, "capacity": 12}})
    s["order"].append("jar")
    s["actions"] = [{"do": "drop_in", "target": "jar", "count": 6, "from": [1000, 900], "start": 0.5, "end": 1.5},
                    {"do": "overflow", "target": "jar", "count": 6, "start": 2, "end": 3}]
    assert "jar" in " ".join(issues(validate(s), "off_frame"))


def test_a_counted_object_invisible_while_counted_fails():
    s = _jar(12, 6)
    _prop(s, "jar")["init"] = {"opacity": 0}
    assert "invisible" in " ".join(issues(validate(s), "min_size"))


def test_min_size_is_measured_over_the_whole_action():
    s = _jar(12, 3)
    s["props"][-1]["params"]["w"] = 72
    s["camera"] = {"portrait": [{"t": 0, "center": [600, 1450], "zoom": 2.0}, {"t": 1.0, "center": [600, 1450], "zoom": 2.0},
                                {"t": 2.2, "center": [540, 960], "zoom": 1.0}]}
    s["actions"][0]["end"] = 2.2        # big at the start, 26 px at mid-action, 25.9 px... then 25.9 at the end
    s["props"][-1]["params"]["w"] = 64  # coins 23 px at zoom 1: too small by the end of the action
    assert "coin" in " ".join(issues(validate(s), "min_size"))


def test_a_burst_is_measured_by_min_size():
    s = tiny()
    s["props"] += [{"id": "bb", "type": "burst", "params": {"area": [100, 100, 900, 700], "sizes": [3, 4, 5], "count": 10}}]
    s["order"].append("bb")
    s["actions"] = [{"do": "burst", "target": "bb", "from": "box.center", "start": 1}]
    assert "bb" in " ".join(issues(validate(s), "min_size"))


# ------------------------------------------------------- 5. pointing

@pytest.mark.parametrize("target", ["w.head", "w.hand_L", "w.center", "w"])
def test_pointing_at_ones_own_body_is_refused(target):
    s = tiny(actions=[{"do": "point_at", "actor": "w", "hand": "R", "target": target, "start": 1, "end": 1.4}])
    assert "own body" in " ".join(issues(validate(s), "pointing") + issues(validate(s), "references"))


def test_a_point_must_be_held_inside_the_scene():
    s = tiny(props=tiny()["props"] + [{"id": "board", "type": "shape", "at": [800, 1000],
                                        "params": {"shape": "rect", "rect": [0, 0, 200, 100]}}])
    s["order"].append("board")
    s["actions"] = [{"do": "point_at", "actor": "w", "hand": "R", "target": "board.center", "start": 3.5, "end": 4.0}]
    assert "0.4" in " ".join(issues(validate(s), "pointing"))


def test_pointing_reports_no_arrival_error_that_is_zero_by_construction():
    s = tiny(props=tiny()["props"] + [{"id": "board", "type": "shape", "at": [800, 1000],
                                        "params": {"shape": "rect", "rect": [0, 0, 200, 100]}}])
    s["order"].append("board")
    s["actions"] = [{"do": "point_at", "actor": "w", "hand": "R", "target": "board.center", "start": 1, "end": 1.4}]
    m = validate(s)["checks"]["pointing"]["measured"]["portrait"][0]
    assert "error_deg" not in m and "worst_deg_while_held" in m


# ------------------------------------------------------- minor: expression size

@pytest.mark.parametrize("expr", ["(" * 3000 + "$n" + ")" * 3000, "-" * 3000 + "$n", "+".join(["$n"] * 100000),
                                  "max(" * 3000 + "$n" + ",1)" * 3000], ids=["parens", "minus", "plus", "nested-max"])
def test_huge_expressions_are_a_clear_error(expr):
    with pytest.raises(ExprError):
        evaluate(expr, 3)
