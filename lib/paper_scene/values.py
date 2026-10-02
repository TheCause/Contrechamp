"""Values of a paper scene: names, colours, numbers, shapes of lists.

A scene is untrusted input (an agent wrote it, a server may pass it on). Every
key that becomes a file name or a DOM/JS identifier is a plain name; every
colour is ``#rrggbb`` or ``#rgb`` (expanded here); every number is finite; every
parameter has the type the library expects. ``check_values`` returns a
normalized copy of the scene and adds one issue per bad value.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Callable

from lib.paper_scene import library as L

NAME = re.compile(r"^[A-Za-z0-9_-]+$")
COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def expand(c: str) -> str:
    c = c.lower()
    return "#" + "".join(ch * 2 for ch in c[1:]) if len(c) == 4 else c


def non_finite(obj: Any, path: str = "") -> list[str]:
    """Paths of every NaN or infinity in the scene."""
    out: list[str] = []
    if isinstance(obj, float) and not math.isfinite(obj):
        out.append(path or "(root)")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out += non_finite(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += non_finite(v, f"{path}[{i}]")
    return out


# --------------------------------------------------------------- value kinds
# Each checker returns (ok, normalized value).

def _num(v): return is_num(v), v
def _pos(v): return is_num(v) and v > 0, v
def _nonneg(v): return is_num(v) and v >= 0, v
def _int1(v): return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= MAX_COUNT, v


# Sanity bounds: far above both real cases, low enough that no value can make the
# compiler loop for minutes (a count of 1e9 pages, a sound every nanosecond).
MAX_COUNT = 2000
MIN_PERIOD = 0.01
MAX_PITCH_STEP = 24.0
MAX_DURATION = 300.0
def _bool(v): return isinstance(v, bool), v
def _str(v): return isinstance(v, str), v
def _text(v): return isinstance(v, str) and len(v) <= 200, v


def _color(v):
    if isinstance(v, str) and COLOR.match(v):
        return True, expand(v)
    return False, v


def _nullable(f):
    return lambda v: (True, v) if v is None else f(v)


def _list_of(f, n=None, min_len=1):
    def chk(v):
        if not isinstance(v, list) or (n is not None and len(v) != n) or len(v) < min_len:
            return False, v
        out = []
        for x in v:
            ok, y = f(x)
            if not ok:
                return False, v
            out.append(y)
        return True, out
    return chk


_point = _list_of(_num, 2)
_rect4 = _list_of(_num, 4)


def _rectwh(v):
    ok, v = _rect4(v)
    return ok and v[2] > 0 and v[3] > 0, v


def _points(v):
    return _list_of(_point, min_len=2)(v)


def _avoid(v):
    if not isinstance(v, list):
        return False, v
    return all(isinstance(a, list) and len(a) in (3, 4) and all(is_num(x) for x in a) for a in v), v


def _glows(v):
    if not isinstance(v, list):
        return False, v
    out = []
    for g in v:
        if not (isinstance(g, list) and len(g) == 5 and all(is_num(g[i]) for i in (0, 1, 2, 4))):
            return False, v
        ok, c = _color(g[3])
        if not ok:
            return False, v
        out.append([g[0], g[1], g[2], c, g[4]])
    return True, out


def _pairs(v):
    return _list_of(_list_of(_num, 2), 2)(v)


def _labels(v):
    return (True, v) if v is None else _list_of(_text)(v)


def _hat(v):
    return (True, v) if v is False else _color(v)


def _glasses(v):
    return (True, v) if isinstance(v, bool) else _color(v)


SHAPE_KINDS_V: dict[str, Callable] = {
    "shape": _str, "rect": _rectwh, "r": _pos, "rx": _pos, "ry": _pos, "points": _points, "teeth": _int1,
    "r_in": _pos, "color": _color, "depth": _nonneg, "amp": _nonneg, "step": _pos, "alpha": _nonneg,
    "edge": _bool, "shadow": _bool, "key": _str, "rolls": _nullable(_pos), "spin": _num, "at": _point,
}
PARAM_KINDS: dict[str, dict[str, Callable]] = {
    "shape": SHAPE_KINDS_V,
    "group": {"children": lambda v: (isinstance(v, list) and len(v) > 0, v), "spin": _num},
    "label": {"text": _text, "size": _pos, "color": _color},
    "path": {"points": _points, "closed": _bool, "color": _color, "width": _pos, "dashed": _bool,
             "line_alpha": _nonneg, "fill_color": _nullable(_color), "fill_alpha": _nonneg},
    "glow": {"r": _pos, "color": _color},
    "door": {"rect": _rectwh, "color": _color, "slat_color": _color, "slats": _int1,
             "inside_color": _nullable(_color), "box_color": _nullable(_color)},
    "container": {"item": _str, "w": _pos, "h": _pos, "capacity": _int1, "glass": _color, "lid": _nullable(_color),
                  "glow": _bool, "item_color": _nullable(_color)},
    "stack": {"w": _pos, "sheet": _pos, "color": _color, "line": _color, "max": _int1},
    "bars": {"n": _int1, "width": _pos, "gap": _nonneg, "unit": _pos, "color": _color, "dashed": _bool,
             "labels": _labels, "label_size": _pos, "label_color": _color},
    "bands": {"colors": _list_of(_color), "top0": _num, "band_h": _pos, "overlap": _nonneg, "strokes": _int1},
    "pole": {"rest": _point, "bands": _nullable(_str), "head_color": _color},
    "sun": {"r": _pos, "rolls": _bool},
    "conveyor": {"x": _num, "w": _pos, "legs": _list_of(_num, min_len=0), "floor": _num},
    "crane": {"mast_x": _num, "jib_y": _num, "jib_x0": _num, "jib_x1": _num, "floor": _num, "color": _color},
    "burst": {"count": _int1, "area": _rect4, "avoid": _avoid, "sizes": _list_of(_pos), "seed": _int1,
              "spacing": _nonneg, "column": _pairs, "hold": _nonneg, "spread": _list_of(_pos, 2), "sparks": _int1},
    "curtain": {"top": _color, "bottom": _color},
    "tint": {"color": _color, "alpha": _nonneg, "glows": _glows},
    "smoke": {"rise": _num, "r0": _pos, "r1": _pos, "color": _color},
    "scatter": {"area": _rect4, "count": _int1, "item": _str, "seed": _int1, "colors": _nullable(_list_of(_color)),
                "size": _pos},
}
assert all(set(PARAM_KINDS[t]) == set(L.PROP_TYPES[t]["params"]) for t in L.PROP_TYPES), "PARAM_KINDS drift"
LOOK_KINDS = {"skin": _color, "shirt": _color, "overall": _color, "brow": _color, "hat": _hat,
              "mustache": _bool, "glasses": _glasses}
STYLE_KINDS = {"background": _color, "grain": _bool, "grain_seed": _int1, "grain_alpha": _nonneg,
               "vignette": _bool, "fade_in": _nonneg}


def _params(kinds: dict[str, Callable], params: Any, issues, where: str) -> Any:
    if not isinstance(params, dict):
        issues.add("vocabulary", "params must be an object", where)
        return params
    out = dict(params)
    for k, v in params.items():
        f = kinds.get(k)
        if f is None:
            continue  # unknown names are reported by the vocabulary check
        ok, nv = f(v)
        if not ok:
            hint = " (a colour is #rrggbb or #rgb)" if isinstance(v, str) else ""
            issues.add("vocabulary", f"{k}={v!r} is not a valid value{hint}", where)
        else:
            out[k] = nv
    return out


def _name(issues, what: str, key: Any, where: str) -> None:
    if not isinstance(key, str) or not NAME.match(key):
        issues.add("vocabulary", f"{what} {key!r}: letters, digits, '_' or '-' only (it becomes a file or "
                   "identifier name)", where)


def check_values(scene: dict[str, Any], issues) -> dict[str, Any]:
    """Normalized copy (colours expanded); issues added for every bad value."""
    s = copy.deepcopy(scene)
    for path in non_finite(s):
        issues.add("vocabulary", f"{path}: not a finite number", path)
    if is_num(s.get("duration")) and s["duration"] > MAX_DURATION:
        issues.add("vocabulary", f"duration {s['duration']:g}s: at most {MAX_DURATION:g}s", "duration")
    for name, lay in (s.get("layouts") or {}).items() if isinstance(s.get("layouts"), dict) else []:
        if isinstance(lay, dict) and any(is_num(lay.get(k)) and lay[k] > 8192 for k in ("width", "height")):
            issues.add("vocabulary", "width and height: at most 8192 px", f"layouts.{name}")
    if not isinstance(s.get("id"), str) or not NAME.match(s.get("id", "")):
        _name(issues, "scene id", s.get("id"), "id")
    for name in list((s.get("layouts") or {}) if isinstance(s.get("layouts"), dict) else []):
        _name(issues, "layout name", name, "layouts")
    for name in list(s.get("camera") or {}) if isinstance(s.get("camera"), dict) else []:
        _name(issues, "layout name", name, "camera")
    for name in list(s.get("marks") or {}) if isinstance(s.get("marks"), dict) else []:
        _name(issues, "mark name", name, "marks")
    style = s.get("style")
    if style is not None:
        if not isinstance(style, dict):
            issues.add("vocabulary", "style must be an object", "style")
        else:
            s["style"] = _params(STYLE_KINDS, style, issues, "style")
    if "text_allowed" in s and not _list_of(_text, min_len=0)(s["text_allowed"])[0]:
        issues.add("vocabulary", "text_allowed must be a list of texts", "text_allowed")
    for p in s.get("props") or []:
        if not isinstance(p, dict) or p.get("type") not in PARAM_KINDS:
            continue
        where = f"props.{p.get('id')}"
        kinds = PARAM_KINDS[p["type"]]
        if "at" in p and not _point(p["at"])[0]:
            issues.add("vocabulary", "at must be [x, y]", where)
        if "params" in p:
            p["params"] = _params(kinds, p["params"], issues, where)
            if p["type"] == "group" and isinstance(p["params"].get("children"), list):
                p["params"]["children"] = [_params(SHAPE_KINDS_V, c, issues, f"{where}.children[{i}]")
                                           for i, c in enumerate(p["params"]["children"])]
        for a, pt in (p.get("anchors") or {}).items() if isinstance(p.get("anchors"), dict) else []:
            _name(issues, "anchor name", a, where)
            if not _point(pt)[0]:
                issues.add("vocabulary", f"anchor {a!r} must be [dx, dy]", where)
        init = p.get("init")
        if init is not None:
            if not isinstance(init, dict):
                issues.add("vocabulary", "init must be an object", where)
            else:
                for k, v in init.items():
                    _name(issues, "channel", k, where)
                    ok = is_num(v) or _color(v)[0]
                    if not ok:
                        issues.add("vocabulary", f"init {k}={v!r} must be a number or a colour", where)
                    elif isinstance(v, str):
                        init[k] = expand(v)
        lays = p.get("layouts")
        if lays is not None:
            if not isinstance(lays, dict):
                issues.add("vocabulary", "layouts must be an object", where)
            else:
                for ln, ov in lays.items():
                    _name(issues, "layout name", ln, where)
                    if not isinstance(ov, dict):
                        issues.add("vocabulary", "a layout override is an object", where)
                        continue
                    if "at" in ov and not _point(ov["at"])[0]:
                        issues.add("vocabulary", "at must be [x, y]", f"{where}.layouts.{ln}")
                    if "params" in ov:
                        ov["params"] = _params(kinds, ov["params"], issues, f"{where}.layouts.{ln}")
    for c in s.get("characters") or []:
        if not isinstance(c, dict):
            continue
        where = f"characters.{c.get('id')}"
        if "at" in c and not _point(c["at"])[0]:
            issues.add("vocabulary", "at must be [x, ground_y]", where)
        if "scale" in c and not _pos(c["scale"])[0]:
            issues.add("vocabulary", "scale must be a number > 0", where)
        if "role" in c and not _text(c["role"])[0]:
            issues.add("vocabulary", "role must be a short text", where)
        if "look" in c:
            if not isinstance(c["look"], dict):
                issues.add("vocabulary", "look must be an object", where)
            else:
                c["look"] = _params(LOOK_KINDS, c["look"], issues, where)
        if "pose" in c and not isinstance(c["pose"], dict):
            issues.add("vocabulary", "pose must be an object", where)
        lays = c.get("layouts")
        if lays is not None:
            if not isinstance(lays, dict):
                issues.add("vocabulary", "layouts must be an object", where)
            else:
                for ln, ov in lays.items():
                    _name(issues, "layout name", ln, where)
                    if not isinstance(ov, dict) or any(k not in ("at", "scale") for k in ov):
                        issues.add("vocabulary", "a character layout override has only at / scale", where)
                        continue
                    if "at" in ov and not _point(ov["at"])[0]:
                        issues.add("vocabulary", "at must be [x, ground_y]", where)
                    if "scale" in ov and not _pos(ov["scale"])[0]:
                        issues.add("vocabulary", "scale must be a number > 0", where)
    snd = s.get("sound")
    for i, e in enumerate((snd or {}).get("events") or [] if isinstance(snd, dict) else []):
        if not isinstance(e, dict):
            continue
        for k, v in e.items():
            if k in ("kind", "t"):
                continue
            ok = _list_of(_pos)(v)[0] if k == "freqs" else is_num(v)
            if not ok:
                issues.add("vocabulary", f"sound {k}={v!r} must be a number", f"sound.events[{i}]")
    cam = s.get("camera")
    if isinstance(cam, dict):
        for name, keys in cam.items():
            if not isinstance(keys, list) or not keys:
                issues.add("vocabulary", "camera keys must be a non-empty list", f"camera.{name}")
                continue
            for j, k in enumerate(keys):
                w = f"camera.{name}[{j}]"
                if not isinstance(k, dict):
                    issues.add("vocabulary", "a camera key is an object", w)
                    continue
                if "center" in k and not _point(k["center"])[0]:
                    issues.add("vocabulary", "center must be [x, y]", w)
                if "zoom" in k and not (is_num(k["zoom"]) and k["zoom"] >= 1):
                    issues.add("vocabulary", "zoom must be a number >= 1", w)
                if "target" in k and not isinstance(k["target"], str):
                    issues.add("vocabulary", "target must be 'id' or 'id.anchor'", w)
    return s


# ------------------------------------------------------------- action values

REF_OR_NUM = lambda v: (isinstance(v, str) or is_num(v), v)          # noqa: E731
REF_OR_POINT = lambda v: (isinstance(v, str) or _point(v)[0], v)     # noqa: E731
ACTION_KINDS: dict[str, Callable] = {
    "dx": _num, "offset": _num, "lean": _num, "grip": _str, "freq": _pos, "step": _pos, "dur": _pos,
    "extend": _nonneg, "retract": _nonneg, "slide": _num, "channel": _str, "behind": _str, "in_front_of": _str,
    "pole": _str, "turn": _num, "gaze": _list_of(_num, 2), "count": _int1,
    "land": REF_OR_NUM, "lid_to": REF_OR_POINT, "from": REF_OR_POINT, "target": _str, "actor": _str,
    "id": lambda v: (isinstance(v, str) and bool(NAME.match(v)), v),
}


def check_action_values(a: dict[str, Any], issues) -> None:
    w, verb = a.get("_where", a.get("id")), a.get("do")
    for k, f in ACTION_KINDS.items():
        if k in a and not f(a[k])[0]:
            issues.add("vocabulary", f"{verb}: {k}={a[k]!r} is not a valid value", w)
    if "amp" in a:   # a pair (upper, fore) for wave / clap, one number for shake / flip
        ok = _list_of(_num, 2)(a["amp"])[0] if verb in ("wave", "clap") else is_num(a["amp"])
        if not ok:
            issues.add("vocabulary", f"{verb}: amp={a['amp']!r} is not a valid value", w)
    to = a.get("to")
    if "to" in a:
        if verb == "animate":
            ok = is_num(to) or _color(to)[0]
            if ok and isinstance(to, str):
                a["to"] = expand(to)
            elif not ok:
                issues.add("vocabulary", f"animate: to must be a number or a colour, got {to!r}", w)
        elif verb == "move":
            if not REF_OR_POINT(to)[0]:
                issues.add("vocabulary", f"move: to must be [x, y] or 'id.anchor', got {to!r}", w)
        elif verb == "attach":
            if not isinstance(to, str):
                issues.add("vocabulary", "attach: to must be 'prop.anchor'", w)
        elif not REF_OR_NUM(to)[0]:
            issues.add("vocabulary", f"{verb}: to must be a number or 'id.anchor', got {to!r}", w)
    if "by" in a:
        ok = _point(a["by"])[0] if verb == "move" else isinstance(a["by"], str)
        if not ok:
            issues.add("vocabulary", f"{verb}: by={a['by']!r} is not valid", w)
    if verb == "tip_over" and "shake" in a:
        sh = a["shake"]
        if not (isinstance(sh, dict) and set(sh) <= {"amp", "freq", "decay"} and all(is_num(x) for x in sh.values())):
            issues.add("vocabulary", "tip_over: shake must be {amp, freq, decay}", w)
    if verb == "tip_over" and "wobble_from" in a and not (is_num(a["wobble_from"]) or isinstance(a["wobble_from"], str)):
        issues.add("vocabulary", "tip_over: wobble_from must be a time", w)
    b = a.get("beat")
    if isinstance(b, dict) and "id" in b and not (isinstance(b["id"], str) and NAME.match(b["id"])):
        issues.add("vocabulary", f"beat id {b['id']!r}: letters, digits, '_' or '-' only", w)
    for i, snd in enumerate(a.get("sound") or []):
        if not isinstance(snd, dict):
            continue
        for k, v in snd.items():
            if k in ("kind", "per_item"):
                continue
            ok = _list_of(_pos)(v)[0] if k == "freqs" else is_num(v)
            if not ok:
                issues.add("vocabulary", f"sound {k}={v!r} must be a number", f"{w}.sound[{i}]")
            elif k == "every" and v < MIN_PERIOD:
                issues.add("vocabulary", f"sound every={v!r}: at least {MIN_PERIOD} s", f"{w}.sound[{i}]")
            elif k == "pitch_step" and abs(v) > MAX_PITCH_STEP:
                issues.add("vocabulary", f"sound pitch_step={v!r}: at most {MAX_PITCH_STEP:g} semitones",
                           f"{w}.sound[{i}]")
