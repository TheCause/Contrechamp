"""Pre-render checks of a compiled paper scene (one per trap the format can see).

| check          | trap | fails when                                                        |
|----------------|------|-------------------------------------------------------------------|
| limb_overlap   |  —   | two actions drive the same channel at the same time               |
| jump           |  1   | a value jumps when an action takes over (push from elsewhere...)  |
| off_frame      |  5   | the place of an action is outside the current camera frame        |
| contact        |  6   | a hand / hook stays away from what it holds, pushes, hooks        |
| zoom_speed     |  7   | a strong zoom change (ratio >= 1.25) lasts less than 1.2 s        |
| ui_safe_bottom |  8   | the place of an action is in the bottom band of a 9:16 layout     |
| beat_in_phrase |  U2  | a beat is not inside the narration phrase it illustrates          |
| text_policy    |  U2  | an on-screen text is not in text_allowed                          |

Traps 2, 3 and 4 are judged on the image (mute review), not here.
"""

from __future__ import annotations

import math
from typing import Any

from lib.paper_scene import motion as M
from lib.paper_scene.compile import Compiled
from lib.paper_scene.resolve import Issues

ZOOM_STRONG_RATIO = 1.25
ZOOM_MIN_SECONDS = 1.2
PLACE_STEP = 0.1
FRAME_MARGIN = 0.0

# Continuous channels and how much they may move from one instant to the next.
JUMP_TOL = {"x": 2.0, "y": 2.0, "rot": 0.05, "scale": 0.05, "armL_u": 3.0, "armL_f": 3.0, "armR_u": 3.0,
            "armR_f": 3.0, "lean": 1.5, "turn": 0.15, "gaze_x": 0.15, "gaze_y": 0.15, "shrug": 0.15,
            "trolley": 2.0, "hook_y": 2.0, "base_x": 2.0, "base_y": 2.0, "tip_dx": 2.0, "tip_dy": 2.0}
JUMP_HINTS = {
    "push": "the actor is not where the push puts him (prop x + offset): walk him there first, or drop 'offset'",
    "pose": "a pose with no duration snaps: give it a duration (end > start)",
    "walk_to": "a walk with no duration snaps: give it a duration",
}


def check_limb_overlap(c: Compiled, issues: Issues) -> None:
    for key, owners in c.owners.items():
        ow = sorted(owners)
        for a, b in zip(ow, ow[1:]):
            if b[0] < a[1] - 1e-6 and a[2] != b[2]:
                issues.add("limb_overlap", f"[{c.layout['name']}] {key} is driven by '{a[2]}' ({a[3]}, "
                           f"{a[0]:.2f}-{a[1]:.2f}s) and '{b[2]}' ({b[3]}, {b[0]:.2f}-{b[1]:.2f}s) at once",
                           b[2])


def check_jump(c: Compiled, issues: Issues) -> list[dict[str, Any]]:
    measured = []
    for key, chn in c.ch.ch.items():
        name = key.split(".", 1)[1]
        tol = JUMP_TOL.get(name)
        if tol is None:
            continue
        owners = {(o[0]): o for o in c.owners.get(key, [])}
        for i, seg in enumerate(chn["s"]):
            if seg[1] <= 1e-9:
                continue  # nothing was shown before t = 0: a value set there is a start, not a jump
            before = c.ch.value_before(key, i)
            after = c.ch.seg_value(seg, seg[1])
            if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
                continue
            d = abs(after - before)
            own = owners.get(seg[1])
            if own and own[3] == "attach":
                continue  # an attach that starts away from its anchor is a contact defect (checked there)
            if d > tol:
                verb = own[3] if own else "?"
                who = own[2] if own else "?"
                issues.add("jump", f"[{c.layout['name']}] {key} jumps by {d:.1f} at {seg[1]:.2f}s when '{who}' "
                           f"({verb}) takes over: {JUMP_HINTS.get(verb, 'start the action from where it is')}",
                           who)
                measured.append({"channel": key, "t": round(seg[1], 3), "jump": round(d, 2)})
    return measured


def _sample_times(t0: float, t1: float) -> list[float]:
    n = max(1, int(math.ceil((t1 - t0) / PLACE_STEP)))
    return [t0 + (t1 - t0) * i / n for i in range(n + 1)]


def check_off_frame(c: Compiled, issues: Issues, duration: float) -> None:
    W, H = c.layout["width"], c.layout["height"]
    for pl in c.places:
        for t in _sample_times(pl["t0"], min(pl["t1"], duration)):
            x, y = pl["fn"](t)
            x0, y0, x1, y1 = M.view_rect(c.camera, t, W, H)
            out = max(x0 + FRAME_MARGIN - x, x - (x1 - FRAME_MARGIN), y0 + FRAME_MARGIN - y, y - (y1 - FRAME_MARGIN))
            if out > 0:
                issues.add("off_frame", f"[{c.layout['name']}] {pl['what']} is {out:.0f} px outside the camera "
                           f"frame at {t:.2f}s ('{pl['action']}', {pl['verb']}); frame x {x0:.0f}..{x1:.0f}, "
                           f"y {y0:.0f}..{y1:.0f}, point ({x:.0f}, {y:.0f})", pl["action"])
                break


POINT_TOL_DEG = 10.0
POINT_MIN_FROM_VERTICAL = 30.0   # U2 renders: a nearly vertical pointing arm was read as a hand on the head


def check_pointing(c: Compiled, issues: Issues, duration: float) -> list[dict[str, Any]]:
    """A pointing arm must aim at its target (shoulder -> hand vs shoulder -> target) when
    it arrives and for the POINT_HOLD seconds a viewer needs to read it."""
    from lib.paper_scene.compile import POINT_HOLD, char_point, ref_point, shoulder_world

    measured = []
    for p in c.pointings:
        if p["target"].partition(".")[0] == p["actor"]:
            issues.add("pointing", f"[{c.layout['name']}] '{p['actor']}' points at {p['target']}, his own body: a "
                       "point designates something else", p["action"])
            continue
        if p["t"] + POINT_HOLD > duration + 1e-9:
            issues.add("pointing", f"[{c.layout['name']}] '{p['actor']}' points at {p['target']} until {p['t']:.2f}s: "
                       f"the point must be held {POINT_HOLD:g} s inside the scene (it ends at {duration:g}s)",
                       p["action"])
        worst, worst_t, first = 0.0, p["t"], None
        t, end = p["t"], min(p["t"] + POINT_HOLD, duration)
        while t <= end + 1e-9:
            sx, sy = shoulder_world(c, p["actor"], p["side"], t)
            hx, hy = char_point(c, p["actor"], "hand_L" if p["side"] < 0 else "hand_R", t)
            tx, ty = ref_point(c, p["target"], t)
            a = math.degrees(math.atan2(hy - sy, hx - sx) - math.atan2(ty - sy, tx - sx))
            a = abs((a + 180) % 360 - 180)
            first = a if first is None else first
            if a > worst:
                worst, worst_t = a, t
            t += 0.05
        sx, sy = shoulder_world(c, p["actor"], p["side"], p["t"])
        tx, ty = ref_point(c, p["target"], p["t"])
        from_up = abs(math.degrees(math.atan2(tx - sx, -(ty - sy))))      # 0 = straight up
        # (no "arrival error": the arm is solved onto the target, it would be 0 by construction)
        measured.append({"action": p["action"], "t": round(p["t"], 3), "worst_deg_while_held": round(worst, 2),
                         "from_vertical_deg": round(from_up, 1)})
        if from_up < POINT_MIN_FROM_VERTICAL:
            issues.add("pointing", f"[{c.layout['name']}] '{p['actor']}' points almost straight up at {p['target']} "
                       f"({from_up:.0f} deg from vertical, '{p['action']}'): an arm that high reads as a raised hand or "
                       f"a hand on the head; move him away from under the target or aim lower "
                       f"(>= {POINT_MIN_FROM_VERTICAL:g} deg)", p["action"])
        if worst > POINT_TOL_DEG:
            issues.add("pointing", f"[{c.layout['name']}] '{p['actor']}' points {worst:.0f} deg off {p['target']} at "
                       f"{worst_t:.2f}s ('{p['action']}'): hold the point {POINT_HOLD:g}s on its target, without "
                       "walking, leaning or re-posing that arm", p["action"])
    return measured


MIN_ITEM_PX, MIN_THICK_PX = 24.0, 6.0
SPILL_DRAWN = 6        # coins the player draws spilling over a rim (ink-theater/paper-scene.js)
SAMPLE_DT = 0.1        # 10 Hz: what happens between camera keys is checked too


def _times(t0: float, t1: float, dt: float = SAMPLE_DT) -> list[float]:
    n = max(1, int(math.ceil((t1 - t0) / dt)))
    return [t0 + (t1 - t0) * i / n for i in range(n + 1)]


def check_counts(c: Compiled, issues: Issues, duration: float) -> list[dict[str, Any]]:
    """What is counted (and heard, one sound per item) must be drawn: a jar shows at most
    `capacity` items, a pile at most `max` sheets, a spill at most SPILL_DRAWN coins."""
    measured = []
    for pid, p in c.props.items():
        if p["type"] not in ("container", "stack"):
            continue
        cap = p["params"]["capacity"] if p["type"] == "container" else p["params"]["max"]
        most = max(c.ch.value(f"{pid}.count", t) for t in _times(0.0, duration))
        measured.append({"prop": pid, "max_count": round(most, 2), "drawn": cap})
        if most > cap + 1e-6:
            what = "capacity" if p["type"] == "container" else "max"
            issues.add("counts", f"[{c.layout['name']}] '{pid}' counts up to {most:g} items but draws at most "
                       f"{cap} ({what}): raise {what}, or count fewer", pid)
        if p["type"] == "container":
            spill = max(c.ch.value(f"{pid}.spill", t) for t in _times(0.0, duration))
            if spill > SPILL_DRAWN + 1e-6:
                issues.add("counts", f"[{c.layout['name']}] '{pid}' overflows {spill:g} items but at most "
                           f"{SPILL_DRAWN} are drawn spilling: overflow count <= {SPILL_DRAWN}", pid)
    return measured


def _item_size(c: Compiled, pid: str, t: float) -> tuple[float, float, str]:
    p = c.props[pid]
    z = M.camera_at(c.camera, t, c.layout["width"], c.layout["height"])[2]
    sc = c.ch.value(f"{pid}.scale", t) * z
    if p["type"] == "stack":
        return p["params"]["w"] * sc, p["params"]["sheet"] * sc, "sheet"
    if p["type"] == "burst":          # a spray is read as a whole: its largest particle
        r = 2 * max(p["params"]["sizes"]) * z
        return r, r, "star"
    if p["params"].get("item") == "coin":
        return 0.36 * p["params"]["w"] * sc, 0.2 * p["params"]["w"] * sc, "coin"
    return 16 * sc, 16 * sc, "star"


def check_min_size(c: Compiled, issues: Issues) -> list[dict[str, Any]]:
    """Objects that carry a count (sheets of a pile, coins in a jar, the stars of a burst) must
    be big enough on screen to be counted — largest side >= 24 px, thickness >= 6 px — and
    visible, all along the action that counts them (sampled at 10 Hz)."""
    measured = []
    for a in c.actions:
        verb = a.get("do")
        if verb not in ("stack_add", "flip", "drop_in", "overflow", "burst"):
            continue
        pid = a["target"]
        t0 = a["start"]
        t1 = c.ends.get(a["id"], a.get("end", t0))
        worst = None
        for t in _times(t0, t1):
            if c.props[pid]["type"] != "burst" and c.ch.value(f"{pid}.opacity", t) <= 0:
                issues.add("min_size", f"[{c.layout['name']}] '{pid}' is counted by '{a['id']}' at {t:.2f}s while "
                           "invisible (opacity 0): what is counted must be seen", a["id"])
                worst = None
                break
            big, thick, what = _item_size(c, pid, t)
            if worst is None or min(big / MIN_ITEM_PX, thick / MIN_THICK_PX) < worst[0]:
                worst = (min(big / MIN_ITEM_PX, thick / MIN_THICK_PX), t, big, thick, what)
        if worst is None:
            continue
        _, t, big, thick, what = worst
        measured.append({"action": a["id"], "prop": pid, "item": what, "worst_at": round(t, 2),
                         "px": round(big, 1), "thick_px": round(thick, 1)})
        if big < MIN_ITEM_PX or thick < MIN_THICK_PX:
            issues.add("min_size", f"[{c.layout['name']}] each {what} of '{pid}' is {big:.0f} x {thick:.0f} px on screen "
                       f"at {t:.2f}s ('{a['id']}'): a counted object needs >= {MIN_ITEM_PX:g} px and >= "
                       f"{MIN_THICK_PX:g} px thick all along its action (enlarge it, or frame it closer)", a["id"])
    return measured


DRAWN_TYPES = {"path", "bars", "stack", "container", "label"}


def _spill_points(c: Compiled, pid: str, t: float) -> list[tuple[float, float]]:
    """Where the spilled coins are (the same formula as the player)."""
    p = c.props[pid]
    sp = c.ch.value(f"{pid}.spill", t)
    if sp <= 0:
        return []
    w, h = p["params"]["w"], p["params"]["h"]
    x, y, rot = (c.ch.value(f"{pid}.{k}", t) for k in ("x", "y", "rot"))
    mx, my = x + h * math.sin(rot), y - h * math.cos(rot)
    pts = []
    for k in range(SPILL_DRAWN):
        u = max(0.0, min(1.0, sp - k))
        if u <= 0:
            continue
        side = 1 if k % 2 else -1
        ex, ey = mx + side * (w * 0.75 + 6 * k), y - 4
        pts.append((M.lerp(mx + side * w * 0.3, ex, u), M.lerp(my - 6, ey, M.EASES["in"](u))))
    return pts


def drawn_box(c: Compiled, pid: str, t: float) -> tuple[float, float, float, float] | None:
    """World box of what a prop shows at t (None when nothing of it is visible)."""
    from lib.paper_scene import library as L

    p = c.props[pid]
    st = {k: c.ch.value(f"{pid}.{k}", t) for k in L.prop_channels(p["type"], p["params"])}
    if st["opacity"] <= 0:
        return None
    pr = p["params"]
    if p["type"] == "path":
        if st["draw"] <= 0 and st["fill"] <= 0:
            return None
        box = L.bbox("path", pr, st)
    elif p["type"] == "bars":
        shown = [i for i in range(1, int(pr["n"]) + 1) if st[f"h_{i}"] > 0 and st[f"reveal_{i}"] > 0]
        if not shown:
            return None
        wd, gap, unit = pr["width"], pr["gap"], pr["unit"]
        x0 = min((i - 1) * (wd + gap) for i in shown)
        x1 = max((i - 1) * (wd + gap) + wd for i in shown)
        y0 = -max(st[f"h_{i}"] for i in shown) * unit
        y1 = pr["label_size"] * 1.4 if pr.get("labels") else 0.0
        box = (x0, y0, x1, y1)
    elif p["type"] == "stack":
        if st["count"] <= 0:
            return None
        box = L.bbox("stack", pr, st)
    else:
        box = L.bbox(p["type"], pr, st)
    pts = [L.place(st["x"], st["y"], st["rot"], st["scale"], q)
           for q in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3]))]
    if p["type"] == "container":   # spilled coins are part of what the jar shows
        r = 0.18 * pr["w"]
        for (sx, sy) in _spill_points(c, pid, t):
            pts += [(sx - r, sy - r), (sx + r, sy + r)]
    return min(q[0] for q in pts), min(q[1] for q in pts), max(q[0] for q in pts), max(q[1] for q in pts)


def _state(box, view) -> str:
    x0, y0, x1, y1 = view
    if x0 - 0.5 <= box[0] and box[2] <= x1 + 0.5 and y0 - 0.5 <= box[1] and box[3] <= y1 + 0.5:
        return "in"
    if box[0] < x1 and box[2] > x0 and box[1] < y1 and box[3] > y0:
        return "cut"
    return "out"


def check_drawn(c: Compiled, issues: Issues, duration: float) -> None:
    """What the format DRAWS and an action makes happen (a line being drawn, strips growing, a
    pile, a jar and its spilled coins, a label) must not be cut by the frame, sampled at 10 Hz
    from its first action to the end (camera moves included). Allowed: entirely out of the
    shot while nothing happens to it, and the frames where a camera move carries it across the
    edge from inside to outside (or back) without coming back the same way."""
    W, H = c.layout["width"], c.layout["height"]
    keys = [k[0] for k in c.camera]
    for pid, p in c.props.items():
        if p["type"] not in DRAWN_TYPES:
            continue
        segs = [o for key, owners in c.owners.items() if key.startswith(pid + ".") for o in owners]
        if not segs:
            continue                       # never touched by an action: decor, cropping allowed
        first = min(o[0] for o in segs)
        seq = []
        for t in _times(first, duration):
            box = drawn_box(c, pid, t)
            acting = any(o[0] - 1e-9 <= t <= o[1] + 1e-9 for o in segs)
            seq.append((t, None if box is None else _state(box, M.view_rect(c.camera, t, W, H)), acting, box))
        i = 0
        while i < len(seq):
            t, st, acting, box = seq[i]
            if st == "out" and acting:
                issues.add("off_frame", f"[{c.layout['name']}] '{pid}' is drawn outside the frame at {t:.2f}s "
                           "while an action changes it", pid)
                break
            if st != "cut":
                i += 1
                continue
            j = i
            while j < len(seq) and seq[j][1] == "cut":
                j += 1
            before = seq[i - 1][1] if i > 0 else None
            after = seq[j][1] if j < len(seq) else None
            crossing = {before, after} == {"in", "out"}
            key_inside = any(seq[i][0] - 1e-9 <= k <= seq[j - 1][0] + 1e-9 for k in keys)
            if not crossing or key_inside or any(s[2] for s in seq[i:j]):
                x0, y0, x1, y1 = M.view_rect(c.camera, t, W, H)
                issues.add("off_frame", f"[{c.layout['name']}] '{pid}' is cut by the frame at {t:.2f}s: it spans x "
                           f"{box[0]:.0f}..{box[2]:.0f}, y {box[1]:.0f}..{box[3]:.0f}; frame x {x0:.0f}..{x1:.0f}, "
                           f"y {y0:.0f}..{y1:.0f}", pid)
                break
            i = j


STATIC_SKIP = {"pole", "burst"}   # placed by what holds or launches them, not by their own position


def key_times(c: Compiled, duration: float) -> list[float]:
    return sorted({min(max(k[0], 0.0), duration) for k in c.camera} | {0.0, duration})


def char_box(c: Compiled, cid: str, t: float) -> tuple[float, float, float, float]:
    """The character's body on screen: feet to hat, shoulders' width (scaled)."""
    ch = c.chars[cid]
    x = c.ch.value(f"{cid}.x", t)
    s, g = ch["scale"], ch["at"][1]
    top = 172.0 - (M.SEAT_DROP if ch["posture"] == "seated" else 0.0)
    return x - 32 * s, g - top * s, x + 32 * s, g


def check_static(c: Compiled, issues: Issues, duration: float) -> None:
    """Without any action: every character's body wholly inside the camera frame all along
    (10 Hz, camera moves included), every prop inside its layout (a position inherited from
    another layout lands outside)."""
    from lib.paper_scene import library as L
    from lib.paper_scene.compile import prop_point

    W, H = c.layout["width"], c.layout["height"]
    for cid in c.chars:
        for t in _times(0.0, duration):
            box = char_box(c, cid, t)
            if _state(box, M.view_rect(c.camera, t, W, H)) != "in":
                x0, y0, x1, y1 = M.view_rect(c.camera, t, W, H)
                issues.add("off_frame", f"[{c.layout['name']}] character '{cid}' is not wholly in the frame at "
                           f"{t:.2f}s: body x {box[0]:.0f}..{box[2]:.0f}, y {box[1]:.0f}..{box[3]:.0f}; frame x "
                           f"{x0:.0f}..{x1:.0f}, y {y0:.0f}..{y1:.0f}", cid)
                break
    for pid, p in c.props.items():
        if p["type"] in L.FULL_FRAME_TYPES or p["type"] in STATIC_SKIP:
            continue
        x, y = prop_point(c, pid, "center", 0.0)
        if not (0 <= x <= W and 0 <= y <= H):
            issues.add("off_frame", f"[{c.layout['name']}] prop '{pid}' lies outside the {W}x{H} layout "
                       f"(centre at {x:.0f}, {y:.0f}): give it a position for this layout", pid)


def check_ui_safe_bottom(c: Compiled, issues: Issues, duration: float) -> None:
    lay = c.layout
    if lay["aspect"] != "9:16":
        return
    if "ui_safe_bottom" not in lay:
        issues.add("ui_safe_bottom", f"[{lay['name']}] a 9:16 layout must declare ui_safe_bottom "
                   "(0.2 for Reels / Shorts / TikTok, 0 when no app interface covers the frame)", lay["name"])
        return
    f = float(lay["ui_safe_bottom"])
    if f <= 0:
        return
    W, H = lay["width"], lay["height"]
    limit = H * (1 - f)
    from lib.paper_scene.compile import char_point

    for cid in c.chars:     # a character standing there, even without an action (10 Hz)
        for t in _times(0.0, duration):
            sx, sy = M.to_screen(c.camera, t, W, H, *char_point(c, cid, "center", t))
            if sy > limit and 0 <= sx <= W and sy <= H:
                issues.add("ui_safe_bottom", f"[{lay['name']}] character '{cid}' stands {sy - limit:.0f} px inside "
                           f"the bottom {f:.0%} of the frame at {t:.2f}s (camera key)", cid)
                break
    for pl in c.places:
        for t in _sample_times(pl["t0"], min(pl["t1"], duration)):
            x, y = pl["fn"](t)
            sx, sy = M.to_screen(c.camera, t, W, H, x, y)
            if sy > limit and 0 <= sx <= W and sy <= H:
                issues.add("ui_safe_bottom", f"[{lay['name']}] {pl['what']} at {t:.2f}s is {sy - limit:.0f} px "
                           f"inside the bottom {f:.0%} of the frame (screen y {sy:.0f} > {limit:.0f}); "
                           f"('{pl['action']}', {pl['verb']})", pl["action"])
                break


def check_contact(c: Compiled, issues: Issues) -> list[dict[str, Any]]:
    measured = []
    for k in c.contacts:
        measured.append({"action": k["action"], "t": round(k["t"], 3), "distance": round(k["distance"], 1),
                         "tol": k["tol"]})
        if k["distance"] > k["tol"]:
            issues.add("contact", f"[{c.layout['name']}] contact not shown: {k['what']} stays "
                       f"{k['distance']:.0f} px away at {k['t']:.2f}s ('{k['action']}', {k['verb']}; "
                       f"tolerance {k['tol']:.0f} px)", k["action"])
    return measured


def zoom_runs(keys: list[list[float]]) -> list[list[list[float]]]:
    """Monotonic zoom moves (lists of keys); a hold or a change of direction ends a move."""
    runs: list[list[list[float]]] = []
    cur: list[list[float]] = []
    sign = 0
    for a, b in zip(keys, keys[1:]):
        d = b[3] - a[3]
        s = 0 if abs(d) < 1e-9 else (1 if d > 0 else -1)
        if s == 0 or s != sign:
            if len(cur) > 1:
                runs.append(cur)
            cur = [a] if s else []
        if s:
            cur.append(b)
        sign = s
    if len(cur) > 1:
        runs.append(cur)
    return runs


def check_zoom_speed(c: Compiled, issues: Issues) -> list[dict[str, Any]]:
    """Inside one zoom move, any two keys closer than 1.2 s must differ by less than x1.25
    (a fast part is not excused by a slow end of the same move)."""
    measured = []
    for run in zoom_runs(c.camera):
        z0, z1 = run[0][3], run[-1][3]
        measured.append({"t0": run[0][0], "t1": run[-1][0], "from": z0, "to": z1,
                         "ratio": round(max(z0, z1) / min(z0, z1), 3)})
    # Sliding window: every set of consecutive camera moves that fits in less than 1.2 s,
    # whatever holds or reversals cut it into pieces, adds up its scale changes (|d ln zoom|).
    # A move longer than 1.2 s never fits whole: it is the slow change the rule asks for.
    keys = c.camera
    limit = math.log(ZOOM_STRONG_RATIO)
    worst = None
    for i in range(len(keys) - 1):
        total = 0.0
        for j in range(i, len(keys) - 1):
            if keys[j + 1][0] - keys[i][0] >= ZOOM_MIN_SECONDS - 1e-9:
                break
            total += abs(math.log(keys[j + 1][3] / keys[j][3]))
            if total >= limit - 1e-12 and (worst is None or total > worst[0]):
                worst = (total, keys[i], keys[j + 1])
    if worst:
        total, a, b = worst
        issues.add("zoom_speed", f"[{c.layout['name']}] zoom changes by x{math.exp(total):.2f} in {b[0] - a[0]:.2f}s "
                   f"({a[0]:g}-{b[0]:g}s, {a[3]:g} -> {b[3]:g}) reads as a cut: give a strong zoom change at least "
                   f"{ZOOM_MIN_SECONDS:g}s", c.layout["name"])
    return measured


def check_beat_in_phrase(beats: list[dict[str, Any]], marks: dict[str, list[float]], issues: Issues) -> None:
    for b in beats:
        ph = b.get("phrase")
        if not ph:
            continue
        if ph not in marks:
            issues.add("beat_in_phrase", f"beat '{b['id']}': unknown phrase {ph!r} (known: "
                       f"{', '.join(sorted(marks))})", b["id"])
            continue
        a, z = marks[ph]
        if b["start"] < a - 1e-6 or b["end"] > z + 1e-6:
            issues.add("beat_in_phrase", f"beat '{b['id']}' ({b['start']:.2f}-{b['end']:.2f}s) is not inside "
                       f"the phrase it illustrates, {ph} ({a:.2f}-{z:.2f}s)", b["id"])


def check_text_policy(scene: dict[str, Any], issues: Issues) -> list[str]:
    texts = []
    for p in scene.get("props") or []:
        params = p.get("params") or {}
        if p.get("type") == "label":
            texts.append(str(params.get("text")))
        if p.get("type") == "bars" and params.get("labels"):
            texts.extend(str(x) for x in params["labels"])
        for ov in (p.get("layouts") or {}).values():
            op = ov.get("params") or {}
            if p.get("type") == "label" and "text" in op:
                texts.append(str(op["text"]))
            if p.get("type") == "bars" and op.get("labels"):
                texts.extend(str(x) for x in op["labels"])
    allowed = scene.get("text_allowed")
    if texts and allowed is None:
        issues.add("text_policy", "the scene shows text: list every allowed text in text_allowed "
                   f"(found: {sorted(set(texts))})", "text_allowed")
        return texts
    for t in texts:
        if t not in (allowed or []):
            issues.add("text_policy", f"on-screen text {t!r} is not in text_allowed {allowed}", "text_allowed")
    return texts
