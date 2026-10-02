"""Reading a paper scene: vocabulary, references, times, repeats, layouts.

Produces a flat, resolved scene (absolute times, repeats expanded, one set of
positions per layout) or a list of issues. Nothing here draws or moves.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any

from lib.paper_scene import library as L
from lib.paper_scene.expr import ExprError, evaluate, is_arithmetic

ID = re.compile(r"^[A-Za-z0-9_-]+$")
TIME_REF = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)(?:\.(start|end))?\s*(?:([+-])\s*(\d+(?:\.\d+)?))?$")
TOP_KEYS = {"format", "id", "title", "brief", "duration", "marks", "insert", "layouts", "style", "text_allowed",
            "order", "props", "characters", "camera", "actions", "beats", "sound"}
LAYOUT_KEYS = {"aspect", "width", "height", "ui_safe_bottom"}
ASPECTS = {"9:16": (9, 16), "16:9": (16, 9)}
STYLE_KEYS = {"background": "#e8e0cf", "grain": True, "grain_seed": 7, "grain_alpha": 0.55, "vignette": True,
              "fade_in": 0.45}
PROP_KEYS = {"id", "type", "at", "params", "anchors", "layouts", "init"}
CHAR_KEYS = {"id", "role", "look", "posture", "scale", "at", "pose", "layouts"}
BEAT_KEYS = {"id", "label", "start", "end", "expected", "phrase"}
ACTION_BEAT_KEYS = {"id", "label", "expected", "start", "end"}
SOUND_TOP_KEYS = {"peak_dbfs", "seed", "reverb_mix", "events"}
CAMERA_KEYS = {"t", "center", "target", "follow", "zoom"}


class Issues:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, check: str, message: str, where: str = "") -> None:
        item = {"check": check, "where": where, "message": message}
        if item not in self.items:      # an action checked in several layouts reports once
            self.items.append(item)

    def of(self, check: str) -> list[dict[str, str]]:
        return [i for i in self.items if i["check"] == check]


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _known(what: str, name: Any, known) -> str:
    return f"unknown {what} {name!r} (known: {', '.join(sorted(known))})"


# ------------------------------------------------------------------ times

class Times:
    def __init__(self, marks: dict[str, list[float]], issues: Issues) -> None:
        self.marks, self.issues = marks, issues

    def __call__(self, v: Any, where: str) -> float | None:
        if _num(v):
            return float(v)
        if isinstance(v, str):
            m = TIME_REF.match(v.strip())
            if m and m.group(1) in self.marks:
                a, b = self.marks[m.group(1)]
                t = b if m.group(2) == "end" else a
                if m.group(3):
                    t += float(m.group(4)) * (1 if m.group(3) == "+" else -1)
                return t
            name = m.group(1) if m else v
            self.issues.add("timing", f"time {v!r}: " + (_known("mark", name, self.marks) if self.marks
                                                          else "no marks declared; use seconds"), where)
            return None
        self.issues.add("timing", f"time {v!r} must be seconds or a mark reference like 'p2.end+0.3'", where)
        return None


# ------------------------------------------------------------- vocabulary

def _check_params(ptype: str, params: dict[str, Any], issues: Issues, where: str, partial: bool = False) -> None:
    spec = L.PROP_TYPES[ptype]["params"]
    for k in params:
        if k not in spec:
            issues.add("vocabulary", f"{ptype}: " + _known("parameter", k, spec), where)
    if partial:
        return
    for k, d in spec.items():
        if d is L.REQ and k not in params:
            issues.add("vocabulary", f"{ptype}: missing parameter {k!r}", where)
    if ptype == "shape":
        _check_shape(params, issues, where)
    if ptype == "group":
        ch = params.get("children")
        if not isinstance(ch, list) or not ch:
            issues.add("vocabulary", "group: children must be a non-empty list of shapes", where)
        else:
            for i, c in enumerate(ch):
                if not isinstance(c, dict):
                    issues.add("vocabulary", "group child must be an object", f"{where}.children[{i}]")
                    continue
                for k in c:
                    if k not in L.SHAPE_PARAMS:
                        issues.add("vocabulary", "shape: " + _known("parameter", k, L.SHAPE_PARAMS),
                                   f"{where}.children[{i}]")
                _check_shape(c, issues, f"{where}.children[{i}]")
    if ptype == "scatter" and params.get("item") not in L.SCATTER_ITEMS:
        issues.add("vocabulary", "scatter: " + _known("item", params.get("item"), L.SCATTER_ITEMS), where)
    if ptype == "container" and params.get("item", "star") not in L.CONTAINER_ITEMS:
        issues.add("vocabulary", "container: " + _known("item", params.get("item"), L.CONTAINER_ITEMS), where)
    if ptype == "bars":
        n = params.get("n")
        if not isinstance(n, int) or n < 1:
            issues.add("vocabulary", "bars: n must be a positive integer", where)
        labels = params.get("labels")
        if labels is not None and (not isinstance(labels, list) or len(labels) != n):
            issues.add("vocabulary", "bars: labels must list one text per bar", where)
    if ptype == "bands" and (not isinstance(params.get("colors"), list) or not params.get("colors")):
        issues.add("vocabulary", "bands: colors must be a non-empty list", where)


def _check_shape(p: dict[str, Any], issues: Issues, where: str) -> None:
    kind = p.get("shape")
    if kind not in L.SHAPE_KINDS:
        issues.add("vocabulary", "shape: " + _known("shape", kind, L.SHAPE_KINDS), where)
        return
    need = {"rect": ["rect"], "circle": ["r"], "ellipse": ["rx", "ry"], "poly": ["points"], "gear": ["r"]}[kind]
    for k in need:
        if p.get(k) is None:
            issues.add("vocabulary", f"shape {kind}: missing parameter {k!r}", where)


def check_vocabulary(scene: Any, issues: Issues) -> bool:
    if not isinstance(scene, dict):
        issues.add("vocabulary", "a scene is a JSON object")
        return False
    for k in scene:
        if k not in TOP_KEYS:
            issues.add("vocabulary", _known("key", k, TOP_KEYS))
    if scene.get("format") != "paper_scene/1":
        issues.add("vocabulary", "format must be 'paper_scene/1'")
    for k in ("id", "brief", "duration", "layouts", "props", "order", "camera", "actions"):
        if k not in scene:
            issues.add("vocabulary", f"missing {k!r}")
    layouts = scene.get("layouts") or {}
    if not isinstance(layouts, dict) or not layouts:
        issues.add("vocabulary", "layouts must name at least one layout")
        layouts = {}
    for name, lay in layouts.items():
        if not isinstance(lay, dict):
            issues.add("vocabulary", "a layout is an object", f"layouts.{name}")
            continue
        for k in lay:
            if k not in LAYOUT_KEYS:
                issues.add("vocabulary", _known("layout key", k, LAYOUT_KEYS), f"layouts.{name}")
        if lay.get("aspect") not in ASPECTS:
            issues.add("vocabulary", _known("aspect", lay.get("aspect"), ASPECTS), f"layouts.{name}")
        elif _num(lay.get("width")) and _num(lay.get("height")):
            a = ASPECTS[lay["aspect"]]
            if abs(lay["width"] * a[1] - lay["height"] * a[0]) > 1e-6 * lay["width"] * a[1]:
                issues.add("vocabulary", f"{lay['width']}x{lay['height']} is not {lay['aspect']}", f"layouts.{name}")
        if not (_num(lay.get("width")) and _num(lay.get("height"))):
            issues.add("vocabulary", "width and height are required numbers", f"layouts.{name}")
    for k in (scene.get("style") or {}):
        if k not in STYLE_KEYS:
            issues.add("vocabulary", _known("style key", k, STYLE_KEYS), "style")
    ids: dict[str, str] = {}
    for i, p in enumerate(scene.get("props") or []):
        where = f"props[{i}]"
        if not isinstance(p, dict):
            issues.add("vocabulary", "a prop is an object", where)
            continue
        where = f"props.{p.get('id', i)}"
        for k in p:
            if k not in PROP_KEYS:
                issues.add("vocabulary", _known("prop key", k, PROP_KEYS), where)
        pid = p.get("id")
        if not isinstance(pid, str) or not ID.match(pid):
            issues.add("vocabulary", f"id {pid!r}: letters, digits, '_' or '-' only", where)
        elif pid in ids:
            issues.add("vocabulary", f"duplicate id {pid!r}", where)
        else:
            ids[pid] = "prop"
        if p.get("type") not in L.PROP_TYPES:
            issues.add("vocabulary", _known("prop type", p.get("type"), L.PROP_TYPES), where)
            continue
        _check_params(p["type"], p.get("params") or {}, issues, where)
        for lname, ov in (p.get("layouts") or {}).items():
            if lname not in layouts:
                issues.add("references", _known("layout", lname, layouts), where)
            for k in ov:
                if k not in ("at", "params", "anchors", "init"):
                    issues.add("vocabulary", _known("layout override", k, ("at", "params", "anchors", "init")), where)
            for an in (ov.get("anchors") or {}):
                if an not in (p.get("anchors") or {}):
                    issues.add("vocabulary", f"layout override moves anchor {an!r}, which the prop does not declare "
                               "(declare it once, then move it per layout)", where)
            _check_params(p["type"], ov.get("params") or {}, issues, f"{where}.layouts.{lname}", partial=True)
            # one sound, one beat list, one story: a layout changes geometry, never what happens
            geo = L.GEOMETRY_PARAMS[p["type"]]
            for k in (ov.get("params") or {}):
                if k in L.PROP_TYPES[p["type"]]["params"] and k not in geo:
                    issues.add("vocabulary", f"layout override of {p['type']} parameter {k!r}: a layout changes "
                               f"geometry only ({', '.join(sorted(geo)) or 'nothing for this type'}), not what is "
                               "shown, counted or drawn", f"{where}.layouts.{lname}")
            for k in (ov.get("init") or {}):
                if k not in L.GEOMETRY_INIT:
                    issues.add("vocabulary", f"layout override of init {k!r}: a layout changes geometry only "
                               f"({', '.join(sorted(L.GEOMETRY_INIT))}), not a state, a count, an opacity or a "
                               "drawing", f"{where}.layouts.{lname}")
    for i, c in enumerate(scene.get("characters") or []):
        where = f"characters[{i}]"
        if not isinstance(c, dict):
            issues.add("vocabulary", "a character is an object", where)
            continue
        where = f"characters.{c.get('id', i)}"
        for k in c:
            if k not in CHAR_KEYS:
                issues.add("vocabulary", _known("character key", k, CHAR_KEYS), where)
        cid = c.get("id")
        if not isinstance(cid, str) or not ID.match(cid):
            issues.add("vocabulary", f"id {cid!r}: letters, digits, '_' or '-' only", where)
        elif cid in ids:
            issues.add("vocabulary", f"duplicate id {cid!r}", where)
        else:
            ids[cid] = "character"
        for k in (c.get("look") or {}):
            if k not in L.LOOK_KEYS:
                issues.add("vocabulary", _known("look key", k, L.LOOK_KEYS), where)
        if c.get("posture", "standing") not in L.POSTURES:
            issues.add("vocabulary", _known("posture", c.get("posture"), L.POSTURES), where)
        _check_pose(c.get("pose") or {}, issues, where)
        if not (isinstance(c.get("at"), list) and len(c["at"]) == 2):
            issues.add("vocabulary", "at must be [x, ground_y]", where)
    for i, a in enumerate(scene.get("actions") or []):
        _check_action_vocab(a, issues, f"actions[{i}]", in_repeat=False)
    for i, b in enumerate(scene.get("beats") or []):
        for k in b:
            if k not in BEAT_KEYS:
                issues.add("vocabulary", _known("beat key", k, BEAT_KEYS), f"beats[{i}]")
        for k in ("id", "label", "start", "end", "expected"):
            if k not in b:
                issues.add("vocabulary", f"beat lacks {k!r}", f"beats[{i}]")
    snd = scene.get("sound") or {}
    for k in snd:
        if k not in SOUND_TOP_KEYS:
            issues.add("vocabulary", _known("sound key", k, SOUND_TOP_KEYS), "sound")
    for i, e in enumerate(snd.get("events") or []):
        _check_sound(e, issues, f"sound.events[{i}]", free=True)
    for name, keys in (scene.get("camera") or {}).items():
        for j, k in enumerate(keys if isinstance(keys, list) else []):
            for kk in k:
                if kk not in CAMERA_KEYS:
                    issues.add("vocabulary", _known("camera key", kk, CAMERA_KEYS), f"camera.{name}[{j}]")
    return not issues.of("vocabulary")


def _check_pose(pose: Any, issues: Issues, where: str, in_repeat: bool = False) -> None:
    """In a repeat, a number may still be an expression of $n (checked again once expanded)."""
    def num(x: Any) -> bool:
        return _num(x) or (in_repeat and isinstance(x, str) and is_arithmetic(x))

    if not isinstance(pose, dict):
        issues.add("vocabulary", "a pose is an object of pose fields", where)
        return
    for k, v in pose.items():
        if k not in L.POSE_FIELDS:
            issues.add("vocabulary", _known("pose field", k, L.POSE_FIELDS), where)
        elif k == "mouth" and v not in L.MOUTHS:
            issues.add("vocabulary", _known("mouth", v, L.MOUTHS), where)
        elif k == "arms" and not (isinstance(v, list) and len(v) == 4 and all(num(x) for x in v)):
            issues.add("vocabulary", "arms must be [upper_L, fore_L, upper_R, fore_R] in degrees", where)
        elif k == "gaze" and not (isinstance(v, list) and len(v) == 2 and all(num(x) for x in v)):
            issues.add("vocabulary", "gaze must be [x, y]", where)
        elif k not in ("mouth", "arms", "gaze") and not num(v):
            issues.add("vocabulary", f"{k} must be a number", where)


def _check_sound(e: Any, issues: Issues, where: str, free: bool = False, verb: str | None = None) -> None:
    from tools.audio.sfx_synth import KIND_PARAMS

    if not isinstance(e, dict):
        issues.add("vocabulary", "a sound is an object", where)
        return
    kind = e.get("kind")
    if kind not in KIND_PARAMS:
        issues.add("vocabulary", _known("sound kind", kind, KIND_PARAMS), where)
        return
    allowed = ({"t", "kind", "gain", "pan"} if free else L.SOUND_KEYS) | set(KIND_PARAMS[kind])
    for k in e:
        if k not in allowed:
            issues.add("vocabulary", f"sound {kind}: " + _known("parameter", k, allowed), where)
    if free:
        if "t" not in e:
            issues.add("vocabulary", "a free sound event needs t", where)
        return
    if "per_item" in e and ("at" in e or "every" in e):
        issues.add("vocabulary", "per_item places its own events: no at / every with it", where)
    if "per_item" in e and verb not in L.ITEM_VERBS:
        issues.add("vocabulary", f"per_item needs a verb that counts items ({', '.join(sorted(L.ITEM_VERBS))})",
                   where)
    if "every" in e and not ((_num(e["every"]) and e["every"] > 0)
                             or (isinstance(e["every"], str) and is_arithmetic(e["every"]))):
        issues.add("vocabulary", "every must be a period in seconds > 0", where)


def _check_action_vocab(a: Any, issues: Issues, where: str, in_repeat: bool) -> None:
    if not isinstance(a, dict):
        issues.add("vocabulary", "an action is an object", where)
        return
    if "repeat" in a:
        if in_repeat:
            issues.add("vocabulary", "repeat blocks do not nest", where)
            return
        r = a["repeat"]
        if isinstance(r, dict) and "name" in r and not (isinstance(r["name"], str) and ID.match(r["name"])):
            issues.add("vocabulary", f"repeat name {r['name']!r}: letters, digits, '_' or '-' only", where)
        for k in a:
            if k not in ("repeat", "actions"):
                issues.add("vocabulary", _known("repeat key", k, ("repeat", "actions")), where)
        if not isinstance(r, dict) or not isinstance(r.get("spans"), list) or not r["spans"]:
            issues.add("vocabulary", "repeat needs {name, spans: [[start, end], ...]}", where)
        for i, sub in enumerate(a.get("actions") or []):
            _check_action_vocab(sub, issues, f"{where}.actions[{i}]", in_repeat=True)
        return
    verb = a.get("do")
    if verb not in L.VERBS:
        issues.add("vocabulary", _known("action", verb, L.VERBS), where)
        return
    spec = L.VERBS[verb]
    allowed = L.ACTION_KEYS | spec["keys"]
    for k in a:
        if k not in allowed:
            issues.add("vocabulary", f"{verb}: " + _known("parameter", k, allowed), where)
    for k in spec["needs"]:
        if k not in a:
            issues.add("vocabulary", f"{verb}: missing parameter {k!r}", where)
    if "start" not in a:
        issues.add("vocabulary", f"{verb}: missing start", where)
    if verb not in L.NO_END_VERBS and "end" not in a:
        issues.add("vocabulary", f"{verb}: missing end", where)
    if verb in L.NO_END_VERBS and "end" in a:
        issues.add("vocabulary", f"{verb}: its end is computed, do not give one", where)
    if spec["actor"] and "actor" not in a:
        issues.add("vocabulary", f"{verb}: missing actor", where)
    if not spec["actor"] and "actor" in a:
        issues.add("vocabulary", f"{verb}: acts on a target, not an actor", where)
    if spec["target"] and "target" not in a:
        issues.add("vocabulary", f"{verb}: missing target", where)
    if "ease" in a and a["ease"] not in L.EASE_NAMES:
        issues.add("vocabulary", _known("ease", a["ease"], L.EASE_NAMES), where)
    if verb == "pose":
        if not isinstance(a.get("set"), dict) or not a.get("set"):
            issues.add("vocabulary", "pose: set must be a non-empty object of pose fields", where)
        else:
            _check_pose(a["set"], issues, where, in_repeat)
    hands = ("L", "R", "both") if verb == "wave" else ("L", "R")
    if "hand" in a and a["hand"] not in hands:
        issues.add("vocabulary", _known("hand", a["hand"], hands), where)
    if verb == "layer" and len([k for k in ("behind", "in_front_of") if k in a]) != 1:
        issues.add("vocabulary", "layer: give behind or in_front_of", where)
    if verb == "tip_over" and a.get("side") not in (-1, 1):
        issues.add("vocabulary", "tip_over: side must be -1 (left) or 1 (right)", where)
    lays = a.get("layouts")
    if lays is not None:
        if not isinstance(lays, dict):
            issues.add("vocabulary", "layouts must be an object {layout: {target, to, from, dx, grip}}", where)
        else:
            for lname, ov in lays.items():
                if not isinstance(ov, dict):
                    issues.add("vocabulary", "a layout override is an object", where)
                    continue
                for k in ov:
                    if k not in L.LAYOUT_ACTION_KEYS:
                        issues.add("vocabulary", _known("layout override", k, L.LAYOUT_ACTION_KEYS)
                                   + ": a layout changes where an action aims, not when nor how much", where)
    for i, s in enumerate(a.get("sound") or []):
        _check_sound(s, issues, f"{where}.sound[{i}]", verb=verb)
    b = a.get("beat")
    if b is not None:
        if not isinstance(b, dict):
            issues.add("vocabulary", "beat must be {id, label, expected}", where)
        else:
            for k in b:
                if k not in ACTION_BEAT_KEYS:
                    issues.add("vocabulary", _known("beat key", k, ACTION_BEAT_KEYS), where)
            for k in ("id", "label", "expected"):
                if k not in b:
                    issues.add("vocabulary", f"beat lacks {k!r}", where)


# --------------------------------------------------------------- expansion

def _subst(v: Any, n: int) -> Any:
    """``$n`` in a repeat: arithmetic strings ("2*$n+1") become numbers, others text ("h_$n")."""
    if isinstance(v, str):
        if is_arithmetic(v):
            return evaluate(v, n)     # raises ExprError on anything beyond the arithmetic
        return v.replace("$n", str(n))
    if isinstance(v, list):
        return [_subst(x, n) for x in v]
    if isinstance(v, dict):
        return {k: _subst(x, n) for k, x in v.items()}
    return v


def expand_actions(scene: dict[str, Any], times: Times, issues: Issues) -> list[dict[str, Any]]:
    """Flat list of actions with absolute times; repeats unrolled."""
    out: list[dict[str, Any]] = []
    for i, a in enumerate(scene.get("actions") or []):
        if "repeat" in a:
            r = a["repeat"]
            name = r.get("name", f"repeat{i}")
            for n, span in enumerate(r["spans"], start=1):
                where = f"actions[{i}].repeat.spans[{n - 1}]"
                if not (isinstance(span, list) and len(span) == 2):
                    issues.add("timing", "a span is [start, end]", where)
                    continue
                s0, s1 = times(span[0], where), times(span[1], where)
                if s0 is None or s1 is None:
                    continue
                if s1 <= s0:
                    issues.add("timing", f"span {s0:g}..{s1:g} is empty", where)
                    continue
                for j, sub in enumerate(a.get("actions") or []):
                    w = f"actions[{i}].actions[{j}] (n={n})"
                    try:
                        act = _subst(copy.deepcopy(sub), n)
                    except ExprError as e:
                        issues.add("vocabulary", f"expression: {e} (only numbers, $n, + - * /, (), min, max)", w)
                        continue
                    if not isinstance(act.get("id", ""), str):
                        act["id"] = str(act["id"])
                    act["id"] = f"{act.get('id', f'{name}_a{j}')}_{n}" if "$n" not in str(sub.get("id", "")) \
                        else act["id"]
                    act["_where"] = w
                    act["_n"] = n
                    for key in ("start", "end"):
                        if key in act:
                            f = act[key]
                            if not _num(f) or not 0 <= f <= 1:
                                issues.add("timing", f"inside a repeat, {key} is a fraction 0..1 of the span "
                                                     f"(got {f!r})", w)
                                act[key] = None
                            else:
                                act[key] = s0 + f * (s1 - s0)
                    if isinstance(act.get("beat"), dict):
                        for key in ("start", "end"):
                            if key in act["beat"]:
                                f = act["beat"][key]
                                act["beat"][key] = s0 + f * (s1 - s0) if _num(f) else None
                    out.append(act)
            continue
        act = copy.deepcopy(a)
        act.setdefault("id", f"a{i}")
        act["_where"] = f"actions[{i}] ({act['id']})"
        for key in ("start", "end"):
            if key in act:
                act[key] = times(act[key], act["_where"])
        if isinstance(act.get("beat"), dict):
            for key in ("start", "end"):
                if key in act["beat"]:
                    act["beat"][key] = times(act["beat"][key], act["_where"])
        out.append(act)
    seen: set[str] = set()
    for act in out:
        if act["id"] in seen:
            issues.add("vocabulary", f"duplicate action id {act['id']!r}", act["_where"])
        seen.add(act["id"])
    return out


def layout_view(scene: dict[str, Any], name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Props and characters with this layout's overrides applied."""
    props, chars = {}, {}
    for p in scene.get("props") or []:
        q = {"id": p["id"], "type": p["type"], "at": list(p.get("at") or [0, 0]),
             "params": dict(p.get("params") or {}), "anchors": dict(p.get("anchors") or {}),
             "init": dict(p.get("init") or {})}
        ov = (p.get("layouts") or {}).get(name) or {}
        if "at" in ov:
            q["at"] = list(ov["at"])
        q["params"].update(ov.get("params") or {})
        q["anchors"].update(ov.get("anchors") or {})
        q["init"].update(ov.get("init") or {})
        spec = L.PROP_TYPES[p["type"]]["params"]
        full = {k: v for k, v in spec.items() if v is not L.REQ}
        full.update(q["params"])
        q["params"] = full
        props[p["id"]] = q
    for c in scene.get("characters") or []:
        q = {"id": c["id"], "role": c.get("role", ""), "look": {**L.LOOK_KEYS, **(c.get("look") or {})},
             "posture": c.get("posture", "standing"), "scale": float(c.get("scale", 1.0)), "at": list(c["at"]),
             "pose": dict(c.get("pose") or {})}
        ov = (c.get("layouts") or {}).get(name) or {}
        if "at" in ov:
            q["at"] = list(ov["at"])
        if "scale" in ov:
            q["scale"] = float(ov["scale"])
        chars[c["id"]] = q
    return props, chars
