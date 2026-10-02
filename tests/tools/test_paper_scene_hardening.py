"""paper_scene, hardening after an adversarial review: hostile keys and values, checks
that could be bypassed, layouts that disagree on sound or time, exceptions."""
from __future__ import annotations

import copy
import json
import math
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.paper_scene import compile_scene, load, validate
from lib.paper_scene import motion as M
from tests.tools.test_paper_scene import FACTORY, FIX, ROOT, USAGE2, issues, tiny


def _fails(scene, check):
    rep = validate(scene)
    assert rep["status"] == "fail", rep
    return " ".join(issues(rep, check))


def _layout(name):
    s = tiny()
    lay = s["layouts"].pop("portrait")
    s["layouts"][name] = lay
    s["camera"] = {name: s["camera"].pop("portrait")}
    return s


# ------------------------------------------------------- 1. keys that become paths

@pytest.mark.parametrize("name", ["../../../ESCAPED", "a/b", "x*/alert(1)//", ""])
def test_a_layout_name_that_is_not_a_plain_name_is_refused(tmp_path, name):
    assert "layout name" in _fails(_layout(name), "vocabulary")
    out = tmp_path / "deep" / "out"
    compile_scene(_layout(name), out)
    assert sorted(p.name for p in out.iterdir()) == ["report.json"]
    assert not (tmp_path / "ESCAPED").exists()


@pytest.mark.parametrize("where", ["marks", "anchors", "repeat", "action_id", "beat_id"])
def test_other_keys_must_be_plain_names(where):
    s = tiny()
    if where == "marks":
        s["marks"] = {"p/1": [0, 1]}
    elif where == "anchors":
        s["props"][1]["anchors"] = {"../h": [0, -10]}
    elif where == "repeat":
        s["actions"] = [{"repeat": {"name": "r/1", "spans": [[0, 1]]},
                         "actions": [{"do": "shrug", "actor": "w", "start": 0, "end": 1}]}]
    elif where == "action_id":
        s["actions"] = [{"do": "shrug", "id": "a</script>", "actor": "w", "start": 0, "end": 1}]
    else:
        s["actions"] = [{"do": "shrug", "actor": "w", "start": 0, "end": 1,
                         "beat": {"id": "b c", "label": "l", "expected": "e"}}]
    assert validate(s)["status"] == "fail"


# ------------------------------------------------------- 2. script injection

def test_a_background_that_is_not_a_colour_is_refused():
    assert "colour" in _fails(tiny(style={"background": "red;}</style><script>alert(1)</script><style>"}),
                              "vocabulary")


@pytest.mark.parametrize("look", [{"skin": "\"/><script>"}, {"hat": "red"}, {"glasses": "red\" onload=\"x"}])
def test_look_colours_must_be_colours(look):
    s = tiny(characters=[{"id": "w", "at": [460, 1500], "look": look}])
    assert "colour" in _fails(s, "vocabulary")


def test_short_colours_are_accepted_and_expanded(tmp_path):
    s = tiny(style={"background": "#eee"})
    s["props"][1]["params"]["color"] = "#a50"
    res = compile_scene(s, tmp_path)
    assert res["report"]["status"] == "pass", res["report"]["errors"]
    html = (tmp_path / "portrait" / "index.html").read_text()
    assert "#eeeeee" in html and "#aa5500" in html


def test_hostile_text_never_reaches_the_html_as_markup(tmp_path):
    evil = "</script><img src=x onerror=alert(1)><script>alert(2)</script>"
    s = tiny(text_allowed=[evil], id="evil-scene")
    s["props"].append({"id": "lab", "type": "label", "at": [300, 400], "params": {"text": evil}})
    s["order"].append("lab")
    res = compile_scene(s, tmp_path)
    assert res["report"]["status"] == "pass", res["report"]["errors"]
    html = (tmp_path / "portrait" / "index.html").read_text()
    assert html.count("<script") == 5                     # 4 engine files + the data/mount script
    assert "<img" not in html and "</script><" not in html
    assert "alert" not in html.split("window.__PAPER_SCENE__")[0]   # nothing hostile before the data
    data = json.loads(re.search(r"window.__PAPER_SCENE__ = (\{.*?\});\n", html, re.S).group(1))
    assert data["props"]["lab"]["params"]["text"] == evil          # the text itself survives as data


# ------------------------------------------------------- 3. one sound, one beat list

def test_a_layout_cannot_change_what_is_heard_or_when():
    fac = load(FACTORY)
    stars = next(p for p in fac["props"] if p["id"] == "stars")
    jar = next(p for p in fac["props"] if p["id"] == "jar")
    s = tiny(layouts={"portrait": {"aspect": "9:16", "width": 1080, "height": 1920, "ui_safe_bottom": 0},
                      "wide": {"aspect": "16:9", "width": 1920, "height": 1080}},
             camera={"portrait": [{"t": 0, "center": [540, 960], "zoom": 1}],
                     "wide": [{"t": 0, "center": [960, 540], "zoom": 1}]})
    st = copy.deepcopy(stars)
    st["params"]["area"] = [45, 60, 1000, 900]
    st["layouts"] = {"wide": {"params": {"count": 10}}}
    s["props"] = [p for p in s["props"]] + [dict(jar, init={"count": 6}, at=[520, 1400]), st]
    s["order"] += ["jar", "stars"]
    s["actions"] = [{"do": "burst", "id": "boom", "start": 0.5, "target": "stars", "from": "jar.mouth",
                     "sound": [{"kind": "sparkle", "per_item": True}]}]
    # refused first as a non-geometric layout override (count); were it allowed, the
    # same-story comparison would refuse it as a timing error naming the action
    rep = validate(s)
    msg = " ".join(issues(rep, "vocabulary") + issues(rep, "timing"))
    assert rep["status"] == "fail" and "count" in msg


# ------------------------------------------------------- 4. zoom speed by sliding window

def _cam(keys):
    return tiny(camera={"portrait": [{"t": t, "center": [540, 960], "zoom": z} for t, z in keys]})


@pytest.mark.parametrize("keys", [
    [(0, 1), (1, 1), (1.42, 1.24), (1.43, 1.24), (1.85, 1.6)],          # split by a hold
    [(0, 1), (1, 1), (1.4, 1.24), (1.41, 1.2399), (1.8, 1.53)],          # split by a 0.0001 reversal
    [(0, 1), (0.3, 1.24), (0.6, 1.0), (0.9, 1.24), (1.2, 1.0)],          # back and forth
])
def test_a_split_zoom_is_still_a_fast_zoom(keys):
    assert "reads as a cut" in _fails(_cam(keys), "zoom_speed")


def test_slow_zooms_still_pass():
    assert issues(validate(_cam([(0, 1.5), (4.1, 1.45), (5.9, 1.0)])), "zoom_speed") == []
    assert issues(validate(_cam([(0, 1), (1, 1), (2.2, 1.6), (3.0, 1.6)])), "zoom_speed") == []


# ------------------------------------------------------- 5. static elements

def test_a_character_out_of_the_frame_at_a_camera_key_fails_without_any_action():
    assert "w" in _fails(tiny(characters=[{"id": "w", "at": [1500, 1500]}]), "off_frame")


def test_a_prop_outside_its_layout_fails():
    s = tiny()
    s["props"][1]["at"] = [1700, 1500]
    assert "box" in _fails(s, "off_frame")


def test_a_static_character_in_the_bottom_band_fails():
    s = tiny(characters=[{"id": "w", "at": [460, 1880]}])
    s["layouts"]["portrait"]["ui_safe_bottom"] = 0.2
    assert "w" in _fails(s, "ui_safe_bottom")


def test_a_9_16_recomposition_that_inherits_16_9_positions_fails():
    s = tiny(layouts={"landscape": {"aspect": "16:9", "width": 1920, "height": 1080},
                      "portrait": {"aspect": "9:16", "width": 1080, "height": 1920, "ui_safe_bottom": 0.2}},
             camera={"landscape": [{"t": 0, "center": [960, 540], "zoom": 1}],
                     "portrait": [{"t": 0, "center": [540, 960], "zoom": 1}]},
             characters=[{"id": "w", "at": [1500, 1000]}],
             props=[{"id": "box", "type": "shape", "at": [1700, 1000], "params": {"shape": "rect", "rect": [-40, -80, 80, 80]}}],
             order=["box", "w"])
    assert "[portrait]" in _fails(s, "off_frame")


# ------------------------------------------------------- 6. contact against the object

def test_a_declared_anchor_far_from_the_object_does_not_fake_a_contact():
    s = tiny(actions=[{"do": "reach", "actor": "w", "hand": "R", "target": "box.grip", "start": 1.0, "end": 1.5}])
    box = s["props"][1]
    box["at"], box["anchors"] = [900, 1500], {"grip": [-440, -60]}
    assert "box" in _fails(s, "contact")


# ------------------------------------------------------- 7. NaN, infinities, types

@pytest.mark.parametrize("mutate", [
    lambda s: s["characters"][0].__setitem__("at", [float("nan"), 1500]),
    lambda s: s["props"][1].__setitem__("at", [float("inf"), 1500]),
    lambda s: s["props"][1]["params"].__setitem__("rect", "x"),
    lambda s: s["props"][1]["params"].__setitem__("rect", [0, 0, float("nan"), 3]),
    lambda s: s["characters"][0].__setitem__("scale", "big"),
    lambda s: s["characters"][0].__setitem__("look", "x"),
    lambda s: s.__setitem__("duration", float("inf")),
    lambda s: s["camera"]["portrait"][0].__setitem__("zoom", float("nan")),
    lambda s: s["camera"]["portrait"][0].__setitem__("center", "a"),
    lambda s: s.__setitem__("actions", [{"do": "walk_to", "actor": "w", "to": float("nan"), "start": 0.5, "end": 1}]),
    lambda s: s.__setitem__("actions", [{"do": "wave", "actor": "w", "amp": "x", "start": 0.5, "end": 1}]),
    lambda s: s.__setitem__("actions", [{"do": "shrug", "actor": "w", "start": 0.5, "end": 1,
                                         "sound": [{"kind": "click", "gain": "loud"}]}]),
    # values that would make the compiler loop or overflow
    lambda s: s.__setitem__("actions", [{"do": "shrug", "actor": "w", "start": 0.5, "end": 1,
                                         "sound": [{"kind": "click", "every": 1e-7}]}]),
    lambda s: s.__setitem__("actions", [{"do": "drop_in", "target": "jar", "count": 10 ** 9, "from": [500, 900],
                                         "start": 0.5, "end": 1}]),
    lambda s: s.__setitem__("duration", 1e9),
])
def test_malformed_values_are_errors_not_crashes(mutate):
    s = tiny()
    mutate(s)
    assert validate(s)["status"] == "fail"


def test_a_scene_that_is_not_an_object_is_an_error():
    assert validate([])["status"] == "fail"


# ------------------------------------------------------- 8. no exception leaves the tool

def _paths(o, p=()):
    yield p
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _paths(v, p + (k,))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _paths(v, p + (i,))


def test_mutated_scenes_never_raise_from_the_tool():
    from tools.video.paper_scene import PaperScene

    base = load(USAGE2)
    rng = random.Random(7)
    vals = [None, -1, 0, 1e9, "x", "$n", [], {}, [1], True, "p1.end", [float("nan"), 1], float("inf")]
    allp = [p for p in _paths(base) if p]
    crashes = []
    for _ in range(400):
        s = copy.deepcopy(base)
        p = rng.choice(allp)
        cur = s
        for k in p[:-1]:
            cur = cur[k]
        if rng.random() < 0.3 and isinstance(cur, dict):
            del cur[p[-1]]
        else:
            cur[p[-1]] = copy.deepcopy(rng.choice(vals))
        try:
            PaperScene().execute({"operation": "validate", "scene": s})
        except Exception as e:  # noqa: BLE001
            crashes.append((p, repr(e)[:80]))
    assert crashes == []


# ------------------------------------------------------- 9. colours in the evaluator comparison

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_render_evaluator_matches_on_colours_too(tmp_path):
    compile_scene(load(FACTORY), tmp_path)
    html = (tmp_path / "portrait" / "index.html").read_text()
    data = json.loads(re.search(r"window.__PAPER_SCENE__ = (\{.*?\});\n", html, re.S).group(1))
    (tmp_path / "c.json").write_text(json.dumps(data))
    times = [round(i * 0.0377, 4) for i in range(int(data["duration"] / 0.0377) + 1)]
    (tmp_path / "t.json").write_text(json.dumps(times))
    js = json.loads(subprocess.run(["node", str(FIX / "eval_channels.js"), str(ROOT / "ink-theater" / "paper-scene.js"),
                                    str(tmp_path / "c.json"), str(tmp_path / "t.json")],
                                   capture_output=True, text=True, check=True).stdout)
    ch = M.Channels()
    ch.ch = data["ch"]
    colours = 0
    for key in data["ch"]:
        for t, v in zip(times, js["channels"][key]):
            p = ch.value(key, t)
            if isinstance(p, str):
                assert p == v, (key, t, p, v)
                colours += M.is_color(p)
    assert colours > 1000


# ------------------------------------------------------- 10. label

def test_a_label_compiles_with_its_font(tmp_path):
    s = tiny(text_allowed=["tour 1 — été"])
    s["props"].append({"id": "lab", "type": "label", "at": [300, 400], "params": {"text": "tour 1 — été", "size": 44}})
    s["order"].append("lab")
    res = compile_scene(s, tmp_path)
    assert res["report"]["status"] == "pass", res["report"]["errors"]
    html = (tmp_path / "portrait" / "index.html").read_text()
    assert "tour 1 — été" in html and "patrickhand.ttf" in html
    assert (tmp_path / "portrait" / "assets" / "patrickhand.ttf").is_file()


# ------------------------------------------------------- 11. the lever is worked by hand

def test_the_blue_worker_works_the_lever_while_the_crane_moves():
    from lib.paper_scene.build import Build

    b = Build(load(FACTORY))
    assert b.report()["status"] == "pass"
    c = b.layouts["portrait"]
    rot = [c.ch.value("lever.rot", 10.1 + 0.05 * i) for i in range(66)]           # 10.1 .. 13.35 s
    turns = sum(1 for a, m, z in zip(rot, rot[1:], rot[2:]) if (m - a) * (z - m) < 0)
    assert turns >= 3 and max(rot) - min(rot) > 0.2                               # it pumps, it does not just sit
    reaches = [k for k in b.measured["contact"]["portrait"] if k["t"] > 10.0 and k["t"] < 13.5]
    assert len(reaches) >= 4 and all(k["distance"] <= k["tol"] for k in reaches)  # the hand stays on it
