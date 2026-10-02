"""The named library of a paper scene: prop types, verbs, looks, pose fields.

Every name a scene may use is declared here, with the case that requires it
(U1 = the wordless factory short, U2 = the narrated "re-sent history" insert;
see docs/fork/lot4-paper-scene.md). A name that is not here is an error that
lists the known names.
"""

from __future__ import annotations

import math
from typing import Any

REQ = object()  # marks a required parameter

# Channels every prop has (position of its local origin, rotation in radians).
COMMON_CHANNELS = {"x": 0.0, "y": 0.0, "rot": 0.0, "scale": 1.0, "opacity": 1.0}

SHAPE_KINDS = ("rect", "circle", "ellipse", "poly", "gear")      # gear: U1 (teeth, r_in, spin)
SHAPE_PARAMS = {
    "shape": REQ, "rect": None, "r": None, "rx": None, "ry": None, "points": None,
    "teeth": 24, "r_in": None, "color": "#d8694b", "depth": 1.0, "amp": None, "step": 13,
    "alpha": 1.0, "edge": True, "shadow": True, "key": None, "rolls": None, "spin": 0.0, "at": None,
}

PROP_TYPES: dict[str, dict[str, Any]] = {
    # U1 (factory walls, hills, crate, lever) + U2 (wall, desk, chair, board)
    "shape": {"params": SHAPE_PARAMS, "channels": {"color": None}},
    # U1 (factory front, sign) + U2 (cart with rolling wheels, desk with lamp)
    "group": {"params": {"children": REQ, "spin": 0.0}, "channels": {}},
    # U2 (handwritten "tour 1".."tour 5")
    "label": {"params": {"text": REQ, "size": 40, "color": "#3b2a1e"},
              "channels": {}},
    # U1 (pencil lines on the blank sky) + U2 (triangle outline and tint)
    "path": {"params": {"points": REQ, "closed": False, "color": "#3b3437", "width": 3.0, "dashed": False,
                        "line_alpha": 1.0, "fill_color": None, "fill_alpha": 0.35},
             "channels": {"draw": 1.0, "fill": 0.0}},
    # U1 (sun glow, lit windows, jar glow)
    "glow": {"params": {"r": REQ, "color": "#ffd36b"}, "channels": {"alpha": 0.5}},
    # U1 (the shutter) + U2 (the hatch)
    "door": {"params": {"rect": REQ, "color": "#9aa3a8", "slat_color": "#6f787e", "slats": 14,
                        "inside_color": "#4c302b", "box_color": "#7d6a60"},
             "channels": {"open": 0.0}},
    # U1 (star jar that tips over) + U2 (coin jar that fills and overflows)
    "container": {"params": {"item": "star", "w": 50, "h": 60, "capacity": 6, "glass": "#cfe6f2",
                             "lid": "#c4553b", "glow": False, "item_color": None},
                  "channels": {"count": 0.0, "empty": 0.0, "spill": 0.0,
                               "lid_x": 0.0, "lid_y": 0.0, "lid_rot": 0.0, "lid_free": 0.0}},
    # U2 (sheets on the cart, pages read on the desk)
    "stack": {"params": {"w": 70, "sheet": 5.0, "color": "#f7f1e3", "line": "#b9ad97", "max": 12},
              "channels": {"count": 0.0, "flipped": 0.0}},
    # U2 (the staircase of strips, the dashed ghost row)
    "bars": {"params": {"n": REQ, "width": 40, "gap": 14, "unit": 30, "color": "#e98a5d",
                        "dashed": False, "labels": None, "label_size": 26, "label_color": "#3b2a1e"},
             "channels": {}},  # dynamic: h_i, reveal_i
    # U1 (the sky painted band by band)
    "bands": {"params": {"colors": REQ, "top0": 1330, "band_h": 222, "overlap": 14, "strokes": 9},
              "channels": {}},  # dynamic: p_i
    # U1 (the telescopic paint roller)
    "pole": {"params": {"rest": [16.0, -40.0], "bands": None, "head_color": "#f6ead0"},
             "channels": {"base_x": 0.0, "base_y": 0.0, "tip_dx": 16.0, "tip_dy": -40.0}},
    # U1 (the sun)
    "sun": {"params": {"r": 95, "rolls": True}, "channels": {"power": 0.7, "dim": 0.0}},
    # U1 (the conveyor belt)
    "conveyor": {"params": {"x": REQ, "w": REQ, "legs": [], "floor": REQ}, "channels": {"offset": 0.0}},
    # U1 (the crane)
    "crane": {"params": {"mast_x": REQ, "jib_y": REQ, "jib_x0": REQ, "jib_x1": REQ, "floor": REQ,
                         "color": "#f1b237"},
              "channels": {"trolley": 0.0, "hook_y": 0.0, "hook_open": 1.0}},
    # U1 (the stars bursting out of the jar)
    "burst": {"params": {"count": 40, "area": REQ, "avoid": [], "sizes": [9, 10, 11, 12, 13, 14, 16, 18, 21],
                         "seed": 42, "spacing": 85, "column": [[-150, 150], [300, 520]], "hold": 0.8,
                         "spread": [0.85, 1.3], "sparks": 22},
              "channels": {}},
    # U1 (night coming down)
    "curtain": {"params": {"top": "#1d2150", "bottom": "#3b3f7e"}, "channels": {"y": -60.0}},
    # U1 (night tint over the frame, lit windows)
    "tint": {"params": {"color": "#666edb", "alpha": 0.62, "glows": []}, "channels": {"night": 0.0}},
    # U1 (chimney smoke)
    "smoke": {"params": {"rise": 260, "r0": 14, "r1": 48, "color": "#fbf6ec"}, "channels": {}},
    # U1 (flowers in the grass, bricks on the wall)
    "scatter": {"params": {"area": REQ, "count": REQ, "item": REQ, "seed": 3, "colors": None, "size": 1.0},
                "channels": {}},
}
SCATTER_ITEMS = ("flower", "brick")
FULL_FRAME_TYPES = {"bands", "curtain", "tint", "scatter", "path", "smoke", "glow"}
CONTAINER_ITEMS = ("star", "coin")

# ----------------------------------------------------------------- characters

LOOK_KEYS = {"skin": "#f2c6a0", "shirt": "#f4e9d8", "overall": "#3f7f9e", "brow": "#5a3a2a",
             "hat": "#f2c230", "mustache": False, "glasses": False}
POSTURES = ("standing", "seated")       # seated: U2's reader
MOUTHS = ("smile", "grin", "o", "flat", "wavy")
POSE_FIELDS = {"arms": [-12.0, -6.0, 12.0, 6.0], "lean": 0.0, "turn": 0.0, "gaze": [0.0, 0.0],
               "mouth": "smile", "brow": 0.0, "shrug": 0.0, "open_hands": 0.0}
CHAR_CHANNELS = {"x": 0.0, "armL_u": -12.0, "armL_f": -6.0, "armR_u": 12.0, "armR_f": 6.0, "lean": 0.0,
                 "turn": 0.0, "gaze_x": 0.0, "gaze_y": 0.0, "mouth": "smile", "brow": 0.0, "shrug": 0.0,
                 "open_hands": 0.0}
CHAR_ANCHORS = ("hand_L", "hand_R", "head", "center", "feet")
SHRUG_POSE = {"arms": [-25.0, -128.0, 25.0, 128.0], "shrug": 1.0, "open_hands": 1.0, "brow": 1.0, "mouth": "wavy"}


def pose_channels(field: str, value: Any) -> dict[str, Any]:
    if field == "arms":
        return dict(zip(("armL_u", "armL_f", "armR_u", "armR_f"), value))
    if field == "gaze":
        return {"gaze_x": value[0], "gaze_y": value[1]}
    return {field: value}


# ---------------------------------------------------------------------- verbs
# actor: "character" | "prop" | None; target: required kind or None.
# keys: allowed parameters (besides do/id/actor/target/start/end/sound/beat).

VERBS: dict[str, dict[str, Any]] = {
    "pose": {"actor": "character", "target": None, "keys": {"set", "ease"}, "needs": {"set"}},
    "shrug": {"actor": "character", "target": None, "keys": {"turn", "gaze", "ease"}, "needs": set()},
    "walk_to": {"actor": "character", "target": None, "keys": {"to", "dx", "ease"}, "needs": {"to"}},
    "push": {"actor": "character", "target": "prop", "keys": {"to", "dx", "offset", "lean", "ease", "grip"},
             "needs": {"to"}},
    "reach": {"actor": "character", "target": "any", "keys": {"hand", "ease"}, "needs": set()},
    "point_at": {"actor": "character", "target": "any", "keys": {"hand", "ease"}, "needs": set()},
    "look_at": {"actor": "character", "target": "any", "keys": {"ease"}, "needs": set()},
    "wave": {"actor": "character", "target": None, "keys": {"hand", "amp", "freq"}, "needs": set()},
    "clap": {"actor": "character", "target": None, "keys": {"amp", "freq"}, "needs": set()},
    "flip": {"actor": "character", "target": "stack", "keys": {"hand", "count", "amp"}, "needs": {"count"}},
    "hold": {"actor": "character", "target": "pole", "keys": {"hand"}, "needs": set()},
    "paint": {"actor": "character", "target": "bands", "keys": {"pole", "step", "dur", "extend", "retract"},
              "needs": {"pole", "step", "dur"}},
    "move": {"actor": None, "target": "prop", "keys": {"to", "by", "ease"}, "needs": set()},
    "attach": {"actor": None, "target": "prop", "keys": {"to", "by"}, "needs": {"to"}},
    "animate": {"actor": None, "target": "prop", "keys": {"channel", "to", "ease"}, "needs": {"channel", "to"}},
    "shake": {"actor": None, "target": "prop", "keys": {"amp", "freq", "decay"}, "needs": set()},
    "tip_over": {"actor": None, "target": "container",
                 "keys": {"side", "wobble_from", "slide", "land", "lid_to", "shake"}, "needs": {"side", "land"}},
    "drop_in": {"actor": None, "target": "container", "keys": {"count", "from"}, "needs": {"count", "from"}},
    "overflow": {"actor": None, "target": "container", "keys": {"count"}, "needs": set()},
    "burst": {"actor": None, "target": "burst", "keys": {"from"}, "needs": {"from"}},
    "stack_add": {"actor": None, "target": "stack", "keys": {"from"}, "needs": {"from"}},
    "layer": {"actor": None, "target": "any_id", "keys": {"behind", "in_front_of"}, "needs": set()},
}
NO_END_VERBS = {"paint", "burst", "layer"}   # their end is computed (or they are instants)
ACTION_KEYS = {"do", "id", "actor", "target", "start", "end", "sound", "beat"}
EASE_NAMES = ("io", "io5", "out", "in", "lin")
SOUND_KEYS = {"kind", "at", "every", "per_item", "gain", "pan", "pitch_step"}
ITEM_VERBS = {"flip", "drop_in", "overflow", "burst", "stack_add"}   # verbs that count items (per_item sound)


def prop_channels(ptype: str, params: dict[str, Any]) -> dict[str, Any]:
    """Every channel a prop of this type has, with its initial value."""
    out = dict(COMMON_CHANNELS)
    out.update({k: v for k, v in PROP_TYPES[ptype]["channels"].items()})
    if ptype == "shape":
        out["color"] = params.get("color", SHAPE_PARAMS["color"])
    if ptype == "bars":
        for i in range(1, int(params["n"]) + 1):
            out[f"h_{i}"] = 0.0
            out[f"reveal_{i}"] = 1.0
    if ptype == "bands":
        for i in range(1, len(params["colors"]) + 1):
            out[f"p_{i}"] = 0.0
    if ptype == "pole":
        out["tip_dx"], out["tip_dy"] = (float(v) for v in params.get("rest", [16.0, -40.0]))
    return out


# --------------------------------------------------------------- geometry

def _shape_bbox(p: dict[str, Any]) -> tuple[float, float, float, float]:
    kind = p.get("shape")
    off = p.get("at") or [0, 0]
    if kind == "rect":
        x, y, w, h = p["rect"]
        box = (x, y, x + w, y + h)
    elif kind == "circle":
        r = p["r"]
        box = (-r, -r, r, r)
    elif kind == "ellipse":
        box = (-p["rx"], -p["ry"], p["rx"], p["ry"])
    elif kind == "gear":
        r = p["r"]
        box = (-r, -r, r, r)
    else:
        xs, ys = [q[0] for q in p["points"]], [q[1] for q in p["points"]]
        box = (min(xs), min(ys), max(xs), max(ys))
    return box[0] + off[0], box[1] + off[1], box[2] + off[0], box[3] + off[1]


def bbox(ptype: str, p: dict[str, Any], ch: dict[str, Any] | None = None) -> tuple[float, float, float, float]:
    """Local bounding box (before position, rotation and scale)."""
    ch = ch or {}
    if ptype == "shape":
        return _shape_bbox(p)
    if ptype == "group":
        boxes = [_shape_bbox(c) for c in p["children"]]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    if ptype == "label":
        w = 0.5 * p.get("size", 40) * max(1, len(str(p["text"])))
        return -w / 2, -p.get("size", 40) * 0.8, w / 2, p.get("size", 40) * 0.2
    if ptype == "path":
        xs, ys = [q[0] for q in p["points"]], [q[1] for q in p["points"]]
        return min(xs), min(ys), max(xs), max(ys)
    if ptype == "glow":
        return -p["r"], -p["r"], p["r"], p["r"]
    if ptype == "door":
        x, y, w, h = p["rect"]
        return x, y, x + w, y + h
    if ptype == "container":
        w, h = p.get("w", 50), p.get("h", 60)
        return -w / 2, -h - 8, w / 2, 0
    if ptype == "stack":
        w = p.get("w", 70)
        n = max(1.0, float(ch.get("count", 0.0)))
        return -w / 2, -n * p.get("sheet", 5.0), w / 2, 0
    if ptype == "bars":
        n, wd, gap = int(p["n"]), p.get("width", 40), p.get("gap", 14)
        hmax = max([float(ch.get(f"h_{i}", 0.0)) for i in range(1, n + 1)] + [1.0]) * p.get("unit", 30)
        return 0, -hmax, n * wd + (n - 1) * gap, 0
    if ptype == "sun":
        r = p.get("r", 95)
        return -r - 30, -r - 30, r + 30, r + 30
    if ptype == "conveyor":
        return p["x"], -8, p["x"] + p["w"], 30
    if ptype == "crane":
        return p["jib_x0"], p["jib_y"] - 92, p["jib_x1"], p["floor"]
    if ptype == "burst":
        a = p["area"]
        return a[0], a[1], a[2], a[3]
    return -10, -10, 10, 10


def anchors(ptype: str, p: dict[str, Any], ch: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Named points in local coordinates; their channels are read by ``ch``."""
    x0, y0, x1, y1 = bbox(ptype, p, ch)
    a = {"center": ((x0 + x1) / 2, (y0 + y1) / 2), "top": ((x0 + x1) / 2, y0), "bottom": ((x0 + x1) / 2, y1),
         "left": (x0, (y0 + y1) / 2), "right": (x1, (y0 + y1) / 2), "origin": (0.0, 0.0)}
    if ptype == "container":
        h = p.get("h", 60)
        a.update({"mouth": (0.0, -h), "inside": (0.0, -h / 2), "bottom": (0.0, 0.0)})
    elif ptype == "stack":
        a["top"] = (0.0, -float(ch.get("count", 0.0)) * p.get("sheet", 5.0))
    elif ptype == "bars":
        n, wd, gap, unit = int(p["n"]), p.get("width", 40), p.get("gap", 14), p.get("unit", 30)
        for i in range(1, n + 1):
            cx = (i - 1) * (wd + gap) + wd / 2
            a[f"slot_{i}"] = (cx, 0.0)
            a[f"top_{i}"] = (cx, -float(ch.get(f"h_{i}", 0.0)) * unit)
    elif ptype == "door":
        x, y, w, h = p["rect"]
        a["opening"] = (x + w / 2, y + h)
        a["sill"] = (x + w / 2, y + h)
    elif ptype == "crane":
        a["hook"] = (float(ch.get("trolley", 0.0)), float(ch.get("hook_y", 0.0)) + 20.0)
    elif ptype == "pole":
        bx, by = float(ch.get("base_x", 0.0)), float(ch.get("base_y", 0.0))
        a["base"] = (bx, by)
        a["tip"] = (bx + float(ch.get("tip_dx", 0.0)), by + float(ch.get("tip_dy", 0.0)))
    elif ptype == "sun":
        r = p.get("r", 95)
        a["top"] = (0.0, -float(r))
    return a


def anchor_needs_world(ptype: str, name: str) -> bool:
    """Crane hook and pole points are already world coordinates (not moved by x/y)."""
    return (ptype == "crane" and name == "hook") or (ptype == "pole" and name in ("base", "tip"))


def place(x: float, y: float, rot: float, scale: float, local: tuple[float, float]) -> tuple[float, float]:
    c, s = math.cos(rot), math.sin(rot)
    return x + (local[0] * c - local[1] * s) * scale, y + (local[0] * s + local[1] * c) * scale
