"""paper_scene, last pass: what two blind reviews of the second version of use case 2 still found."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from lib.paper_scene import validate
from lib.paper_scene import motion as M
from lib.paper_scene.build import Build
from lib.paper_scene.compile import char_point, char_state
from tests.tools.test_paper_scene import ROOT, issues, tiny


# ------------------------------------------------------- 1. off_frame sees what is drawn

def _drawn(x_right, *, animate=True):
    s = tiny(camera={"portrait": [{"t": 0, "center": [540, 960], "zoom": 1.5}]})   # frame x 180..900
    s["props"].append({"id": "tri", "type": "path", "init": {"draw": 0},
                       "params": {"points": [[300, 900], [x_right, 600], [x_right, 900]], "closed": True}})
    s["order"].append("tri")
    if animate:
        s["actions"] = [{"do": "animate", "target": "tri", "channel": "draw", "to": 1, "start": 1, "end": 2}]
    return s


def test_a_drawn_line_partly_outside_the_frame_fails():
    # its centre (650, 750) is inside: the old check, which looked at one point, said pass
    msg = " ".join(issues(validate(_drawn(1000)), "off_frame"))
    assert "tri" in msg and "cut" in msg


def test_a_drawn_line_inside_the_frame_passes():
    assert issues(validate(_drawn(850)), "off_frame") == []


def test_bars_cut_by_the_frame_once_shown_fail():
    s = tiny(camera={"portrait": [{"t": 0, "center": [540, 960], "zoom": 1.0},
                                  {"t": 2.0, "center": [540, 960], "zoom": 1.0},
                                  {"t": 3.3, "center": [300, 1400], "zoom": 2.0}]})
    s["props"].append({"id": "bars", "type": "bars", "at": [500, 1000], "params": {"n": 3, "width": 60, "unit": 40}})
    s["order"].append("bars")
    s["actions"] = [{"do": "animate", "target": "bars", "channel": f"h_{i}", "to": i, "start": 0.2 * i, "end": 0.2 * i + 0.2}
                    for i in (1, 2, 3)]
    msg = " ".join(issues(validate(s), "off_frame"))
    assert "bars" in msg and "cut" in msg and "3.30" in msg


# ------------------------------------------------------- 2. filled ghost bars

def test_dashed_bars_take_a_fill_opacity():
    s = tiny()
    s["props"].append({"id": "ghost", "type": "bars", "at": [300, 1200],
                       "params": {"n": 3, "dashed": True, "fill_alpha": 0.6}})
    s["order"].append("ghost")
    b = Build(s)
    assert b.report()["status"] == "pass", b.report()["errors"]
    assert b.compiled_json("portrait")["props"]["ghost"]["params"]["fill_alpha"] == 0.6


# ------------------------------------------------------- 3. settle after a push

def test_settle_brings_the_body_back_to_rest():
    s = tiny(characters=[{"id": "w", "at": [505, 1500]}],
             actions=[{"do": "push", "actor": "w", "target": "box", "to": 700, "start": 0.5, "end": 1.5},
                      {"do": "settle", "actor": "w", "start": 1.6, "end": 2.0}])
    b = Build(s)
    assert b.report()["status"] == "pass", b.report()["errors"]
    st = char_state(b.layouts["portrait"], "w", 2.0)
    assert st["lean"] == pytest.approx(0) and [st[k] for k in ("armL_u", "armL_f", "armR_u", "armR_f")] == \
        pytest.approx([-12, -6, 12, 6])


# ------------------------------------------------------- 4. the page turns towards the one who flips

def _flip(actor_x):
    s = tiny(characters=[{"id": "w", "at": [actor_x, 1500]}])
    s["props"].append({"id": "pile", "type": "stack", "at": [500, 1500], "params": {"w": 80, "sheet": 8}, "init": {"count": 3}})
    s["order"].append("pile")
    s["actions"] = [{"do": "flip", "actor": "w", "hand": "R" if actor_x < 500 else "L", "target": "pile",
                     "count": 2, "start": 1, "end": 2}]
    return s


@pytest.mark.parametrize("actor_x,side", [(450, -1), (550, 1)])
def test_the_flipped_page_lifts_on_the_flipping_actor_side(actor_x, side):
    b = Build(_flip(actor_x))
    flips = b.compiled_json("portrait")["props"]["pile"]["extra"]["flip_side"]
    assert flips == [[1.0, 2.0, side]]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("side", [-1, 1])
def test_the_drawn_page_rises_on_that_side(side):
    js = r"""
    const vm = require('vm'), fs = require('fs');
    const ctx = {InkTheater: {rng: () => () => 0.5, el: () => ({})}, PaperCut: {mix: (a) => a}};
    ctx.globalThis = ctx; vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
    const out = [0.1, 0.3, 0.5, 0.7, 0.9].map((u) => ctx.PaperScene.flipPage(80, 8, -24, u, +process.argv[2]));
    process.stdout.write(JSON.stringify(out));
    """
    pages = json.loads(subprocess.run(["node", "-e", js, str(ROOT / "ink-theater" / "paper-scene.js"), str(side)],
                                      capture_output=True, text=True, check=True).stdout)
    for pg in pages:                                       # all along the flip
        assert pg["edge"][1] < -24                         # the free edge is lifted above the pile
        assert pg["edge"][0] * side > 0                    # and it stays on the flipping actor's side


# ------------------------------------------------------- 5. a camera that follows

def test_a_camera_key_can_follow_a_character():
    s = tiny(camera={"portrait": [{"t": 0, "center": [460, 1300], "zoom": 2.0},
                                  {"t": 0.5, "follow": "w.center", "zoom": 2.0},
                                  {"t": 3.0, "follow": "w.center", "zoom": 2.0}]},
             actions=[{"do": "walk_to", "actor": "w", "to": 760, "start": 0.6, "end": 2.6}])
    b = Build(s)
    assert b.report()["status"] == "pass", b.report()["errors"]
    c = b.layouts["portrait"]
    for t in (1.0, 1.6, 2.2):
        cx, cy, z = M.camera_at(c.camera, t, 1080, 1920)
        wx, wy = char_point(c, "w", "center", t)
        assert abs(cx - wx) < 3 and abs(cy - wy) < 3      # he stays centred while he walks
