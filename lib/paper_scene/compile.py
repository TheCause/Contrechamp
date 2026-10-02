"""From resolved actions to channels, one layout at a time.

Every verb of the library becomes segments on channels (see motion.py). The
compiler also records what the checks need: who drives which channel when,
the contacts an action promises, and the place where each action happens.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from lib.paper_scene import library as L
from lib.paper_scene import motion as M

SAMPLE_HZ = 30.0


@dataclass
class Compiled:
    layout: dict[str, Any]
    props: dict[str, Any]
    chars: dict[str, Any]
    ch: M.Channels
    camera: list[list[float]] = field(default_factory=list)
    layers: list[list[Any]] = field(default_factory=list)
    owners: dict[str, list[tuple[float, float, str, str]]] = field(default_factory=dict)
    contacts: list[dict[str, Any]] = field(default_factory=list)
    places: list[dict[str, Any]] = field(default_factory=list)
    items: dict[str, list[float]] = field(default_factory=dict)
    extras: dict[str, dict[str, Any]] = field(default_factory=dict)
    ends: dict[str, float] = field(default_factory=dict)
    deferred: list[Callable[[], None]] = field(default_factory=list)
    n_samplers: int = 0
    resolvers: list[Callable[[], None]] = field(default_factory=list)
    relative: list[tuple[str, list[Any]]] = field(default_factory=list)


class CompileError(ValueError):
    pass


# ------------------------------------------------------------------- points

def _split(ref: str) -> tuple[str, str | None]:
    if "." in ref:
        a, b = ref.split(".", 1)
        return a, b
    return ref, None


def prop_state(c: Compiled, pid: str, t: float) -> dict[str, Any]:
    p = c.props[pid]
    return {k: c.ch.value(f"{pid}.{k}", t) for k in L.prop_channels(p["type"], p["params"])}


def prop_point(c: Compiled, pid: str, anchor: str | None, t: float) -> tuple[float, float]:
    p = c.props[pid]
    st = prop_state(c, pid, t)
    anchor = anchor or "center"
    if anchor in p["anchors"]:
        local = tuple(p["anchors"][anchor])
    else:
        local = L.anchors(p["type"], p["params"], st)[anchor]
        if L.anchor_needs_world(p["type"], anchor):
            return local
    return L.place(st["x"], st["y"], st["rot"], st["scale"], local)


def char_state(c: Compiled, cid: str, t: float) -> dict[str, Any]:
    return {k: c.ch.value(f"{cid}.{k}", t) for k in L.CHAR_CHANNELS}


def char_point(c: Compiled, cid: str, anchor: str | None, t: float) -> tuple[float, float]:
    ch = c.chars[cid]
    st = char_state(c, cid, t)
    seated = ch["posture"] == "seated"
    s, x, g = ch["scale"], st["x"], ch["at"][1]
    anchor = anchor or "center"
    if anchor in ("hand_L", "hand_R"):
        side = -1 if anchor == "hand_L" else 1
        k = "armL" if side < 0 else "armR"
        loc = M.hand_local(side, st[f"{k}_u"], st[f"{k}_f"], st["shrug"], st["lean"], seated)
        return M.to_world(x, g, s, loc)
    drop = M.SEAT_DROP if seated else 0.0
    if anchor == "head":
        return M.to_world(x, g, s, M._lean((0.0, -132.0 + st["shrug"] * 3 + drop), st["lean"]))
    if anchor == "feet":
        return x, g
    return M.to_world(x, g, s, M._lean((0.0, -70.0 + drop), st["lean"]))


def ref_point(c: Compiled, ref: Any, t: float) -> tuple[float, float]:
    if isinstance(ref, list):
        return float(ref[0]), float(ref[1])
    rid, anchor = _split(ref)
    if rid in c.chars:
        return char_point(c, rid, anchor, t)
    return prop_point(c, rid, anchor, t)


# ----------------------------------------------------------------- building

def new_compiled(layout: dict[str, Any], props: dict[str, Any], chars: dict[str, Any]) -> Compiled:
    c = Compiled(layout=layout, props=props, chars=chars, ch=M.Channels())
    for pid, p in props.items():
        for k, v in L.prop_channels(p["type"], p["params"]).items():
            c.ch.define(f"{pid}.{k}", v)
        c.ch.ch[f"{pid}.x"]["v"], c.ch.ch[f"{pid}.y"]["v"] = float(p["at"][0]), float(p["at"][1])
        for k, v in p.get("init", {}).items():
            c.ch.ch[f"{pid}.{k}"]["v"] = float(v) if isinstance(v, (int, float)) else v
    for cid, ch in chars.items():
        for k, v in L.CHAR_CHANNELS.items():
            c.ch.define(f"{cid}.{k}", v)
        c.ch.ch[f"{cid}.x"]["v"] = float(ch["at"][0])
        for field_, v in ch["pose"].items():
            for k, vv in L.pose_channels(field_, v).items():
                c.ch.ch[f"{cid}.{k}"]["v"] = vv
    return c


def _seg(c: Compiled, key: str, seg: list[Any], act: dict[str, Any]) -> None:
    c.ch.add(key, seg)
    c.owners.setdefault(key, []).append((seg[1], seg[2], act["id"], act["do"]))


def _ease(c: Compiled, key: str, t0: float, t1: float, v1: Any, ease: str, act: dict[str, Any]) -> None:
    """Ease from wherever the channel is at t0 (re-read once every action is in: see refresh_starts)."""
    v0 = c.ch.value(key, t0)
    seg = ["e", t0, t1, v0, v1, ease]
    _seg(c, key, seg, act)
    c.relative.append((key, seg))


def _from_here(c: Compiled, key: str, seg: list[Any], act: dict[str, Any]) -> None:
    """A segment whose base (index 3) is wherever the channel is at its start
    (oscillations, damped shakes): re-read like the eases' starts."""
    seg[3] = c.ch.value(key, seg[1])
    _seg(c, key, seg, act)
    c.relative.append((key, seg))


def refresh_starts(c: Compiled) -> None:
    """Set the start of every relative ease to the value its channel really has
    there, in time order (some channels are only known after the deferred passes:
    a held pole follows a hand that a later action moves)."""
    rel = {id(seg) for _, seg in c.relative}
    for key in sorted({k for k, _ in c.relative}):
        segs = c.ch.segments(key)
        for i, seg in enumerate(segs):
            if id(seg) in rel:
                seg[3] = c.ch.value_before(key, i)


def _sampled(c: Compiled, key: str, t0: float, t1: float, fn: Callable[[float], Any], act: dict[str, Any],
             hz: float = SAMPLE_HZ) -> None:
    n = max(1, int(math.ceil((t1 - t0) * hz)))
    pts = [[t0 + (t1 - t0) * i / n, fn(t0 + (t1 - t0) * i / n)] for i in range(n + 1)]
    _seg(c, key, ["s", t0, t1, pts], act)


def _place(c: Compiled, act: dict[str, Any], t0: float, t1: float, fn: Callable[[float], tuple[float, float]],
           what: str) -> None:
    c.places.append({"action": act["id"], "verb": act["do"], "t0": t0, "t1": t1, "fn": fn, "what": what,
                     "where": act.get("_where", act["id"])})


def _contact(c: Compiled, act: dict[str, Any], t: float, effector: tuple[float, float],
             target: tuple[float, float], tol: float, what: str) -> None:
    c.contacts.append({"action": act["id"], "verb": act["do"], "t": t, "effector": effector, "target": target,
                       "distance": math.hypot(effector[0] - target[0], effector[1] - target[1]), "tol": tol,
                       "what": what, "where": act.get("_where", act["id"])})


CONTACT_TOL = 8.0   # px: a hand or hook farther than this from what it holds does not touch it


def _arm(hand: str) -> tuple[str, str, int]:
    return ("armL_u", "armL_f", -1) if hand == "L" else ("armR_u", "armR_f", 1)


def _ik_to(c: Compiled, cid: str, hand: str, point: tuple[float, float], t: float,
           lean: float | None = None) -> tuple[float, float, float]:
    ch = c.chars[cid]
    st = char_state(c, cid, t)
    ku, kf, side = _arm(hand)
    ln = st["lean"] if lean is None else lean
    local = M.to_local(st["x"], ch["at"][1], ch["scale"], ln, point)
    up, fore, miss = M.ik(side, local, st["shrug"], ch["posture"] == "seated", near=(st[ku], st[kf]))
    return up, fore, miss * ch["scale"]


# -------------------------------------------------------------------- verbs

def compile_action(c: Compiled, a: dict[str, Any], scene_duration: float) -> None:
    verb = a["do"]
    fn = VERB_COMPILERS[verb]
    fn(c, a, scene_duration)


def _v_pose(c: Compiled, a: dict[str, Any], _d: float, values: dict[str, Any] | None = None) -> None:
    cid, t0, t1 = a["actor"], a["start"], a["end"]
    for field_, v in (values or a["set"]).items():
        for k, vv in L.pose_channels(field_, v).items():
            _ease(c, f"{cid}.{k}", t0, t1, vv, a.get("ease", "io"), a)
    _place(c, a, t0, t1, lambda t: char_point(c, cid, "center", t), f"{cid} (pose)")


def _v_shrug(c: Compiled, a: dict[str, Any], d: float) -> None:
    vals = dict(L.SHRUG_POSE)
    for k in ("turn", "gaze"):
        if k in a:
            vals[k] = a[k]
    _v_pose(c, a, d, vals)


def _target_x(c: Compiled, to: Any, t: float) -> float:
    return float(to) if isinstance(to, (int, float)) else ref_point(c, to, t)[0]


def _v_walk_to(c: Compiled, a: dict[str, Any], _d: float) -> None:
    cid, t0, t1 = a["actor"], a["start"], a["end"]
    x1 = _target_x(c, a["to"], t0) + float(a.get("dx", 0.0))
    _ease(c, f"{cid}.x", t0, t1, x1, a.get("ease", "io"), a)
    _place(c, a, t0, t1, lambda t: char_point(c, cid, "center", t), f"{cid} (walking)")


def _v_push(c: Compiled, a: dict[str, Any], _d: float) -> None:
    cid, pid, t0, t1 = a["actor"], a["target"], a["start"], a["end"]
    ch = c.chars[cid]
    px0 = c.ch.value(f"{pid}.x", t0)
    ax0 = c.ch.value(f"{cid}.x", t0)
    offset = float(a["offset"]) if "offset" in a else ax0 - px0
    side = -1 if offset < 0 else 1            # actor on the left (-1) or right of the prop
    px1 = _target_x(c, a["to"], t0) + float(a.get("dx", 0.0))
    _ease(c, f"{pid}.x", t0, t1, px1, a.get("ease", "io"), a)
    _seg(c, f"{cid}.x", ["f", t0, t1, f"{pid}.x", 1.0, offset], a)
    # hands on the prop: its near side (or a named grip), reached over a short ease
    ta = t0 + min(0.3, (t1 - t0) / 3)
    lean = float(a.get("lean", 8.0)) * (-side)
    _ease(c, f"{cid}.lean", t0, ta, lean, "io", a)
    grip = a.get("grip") or ("left" if side < 0 else "right")
    gx, gy = prop_point(c, pid, grip, t0)
    shoulder_y = ch["at"][1] - 90.0 * ch["scale"]
    if grip in ("left", "right"):
        p = c.props[pid]
        st = prop_state(c, pid, t0)
        x0b, y0b, x1b, y1b = L.bbox(p["type"], p["params"], st)
        top, bottom = st["y"] + y0b * st["scale"], st["y"] + y1b * st["scale"]
        gy = min(max(shoulder_y + 10 * ch["scale"], top + 6), bottom - 6)
    dxg, dyg = gx - px0, gy
    for hand in ("L", "R"):
        ku, kf, _ = _arm(hand)
        grip_t = lambda t, dxg=dxg, dyg=dyg: (c.ch.value(f"{pid}.x", t) + dxg, dyg)
        # IK with the actor where he will be once the hands are on the prop
        st_x = px0 + offset
        local = M.to_local(st_x, ch["at"][1], ch["scale"], lean, grip_t(t0))
        st = char_state(c, cid, t0)
        up, fore, _miss = M.ik(-1 if hand == "L" else 1, local, st["shrug"], ch["posture"] == "seated",
                               near=(st[ku], st[kf]))
        _ease(c, f"{cid}.{ku}", t0, ta, up, "io", a)
        _ease(c, f"{cid}.{kf}", t0, ta, fore, "io", a)
        for tc in (ta, t1):
            c.deferred.append(lambda tc=tc, hand=hand, grip_t=grip_t: _contact(
                c, a, tc, char_point(c, cid, f"hand_{hand}", tc), grip_t(tc), CONTACT_TOL,
                f"{cid}'s {hand} hand on {pid}"))
    _place(c, a, t0, t1, lambda t: char_point(c, cid, "center", t), f"{cid} (pushing)")
    _place(c, a, t0, t1, lambda t: prop_point(c, pid, "center", t), f"{pid} (pushed)")


def _target_ease(c: Compiled, a: dict[str, Any], keys: list[str], solve: Callable[[], list[float]]) -> None:
    """Ease channels to values that depend on where the target is at the end.

    The values are solved once every action is compiled (an action starting at
    the same time may move the target), before the held props are sampled."""
    cid, t0, t1 = a["actor"], a["start"], a["end"]
    segs = []
    for k in keys:
        cur = c.ch.value(f"{cid}.{k}", t1)
        _ease(c, f"{cid}.{k}", t0, t1, cur, a.get("ease", "io"), a)
        segs.append(c.ch.segments(f"{cid}.{k}")[[s[1] for s in c.ch.segments(f"{cid}.{k}")].index(t0)])

    def resolve() -> None:
        for seg, v in zip(segs, solve()):
            seg[4] = v
        for k in keys:
            c.ch._starts.pop(f"{cid}.{k}", None)
    c.resolvers.append(resolve)


def _v_reach(c: Compiled, a: dict[str, Any], _d: float) -> None:
    cid, t0, t1, hand = a["actor"], a["start"], a["end"], a.get("hand", "R")
    ku, kf, _ = _arm(hand)

    def solve() -> list[float]:
        up, fore, _miss = _ik_to(c, cid, hand, ref_point(c, a["target"], t1), t1)
        return [up, fore]
    _target_ease(c, a, [ku, kf], solve)
    c.deferred.append(lambda: _contact(c, a, t1, char_point(c, cid, f"hand_{hand}", t1),
                                       ref_point(c, a["target"], t1), CONTACT_TOL,
                                       f"{cid}'s {hand} hand on {a['target']}"))
    _place(c, a, t0, t1, lambda t: ref_point(c, a["target"], t), f"{a['target']} (reached)")


def _v_point_at(c: Compiled, a: dict[str, Any], _d: float) -> None:
    cid, t0, t1, hand = a["actor"], a["start"], a["end"], a.get("hand", "R")
    ch = c.chars[cid]
    ku, kf, side = _arm(hand)

    def solve() -> list[float]:
        st = char_state(c, cid, t1)
        tx, ty = ref_point(c, a["target"], t1)
        lx, ly = M.to_local(st["x"], ch["at"][1], ch["scale"], st["lean"], (tx, ty))
        sx, sy = M.shoulder(side, st["shrug"], ch["posture"] == "seated")
        ang = math.degrees(math.atan2(lx - sx, ly - sy))
        cur = c.ch.value(f"{cid}.{ku}", t0)
        ang = cur + ((ang - cur + 180) % 360 - 180)
        return [ang, ang]
    _target_ease(c, a, [ku, kf], solve)
    _place(c, a, t0, t1, lambda t: ref_point(c, a["target"], t), f"{a['target']} (pointed at)")


def _v_look_at(c: Compiled, a: dict[str, Any], _d: float) -> None:
    cid, t0, t1 = a["actor"], a["start"], a["end"]

    def solve() -> list[float]:
        hx, hy = char_point(c, cid, "head", t1)
        tx, ty = ref_point(c, a["target"], t1)
        dx, dy = tx - hx, ty - hy
        m = max(abs(dx), abs(dy), 1e-6)
        return [M.clamp(dx / 400.0, -1, 1), dx / m, dy / m]
    _target_ease(c, a, ["turn", "gaze_x", "gaze_y"], solve)
    _place(c, a, t0, t1, lambda t: char_point(c, cid, "head", t), f"{cid} (looking)")


def _v_wave(c: Compiled, a: dict[str, Any], _d: float) -> None:
    """One arm, or both mirrored ("both": the left arm gets +amp, the right -amp)."""
    cid, t0, t1, hand = a["actor"], a["start"], a["end"], a.get("hand", "L")
    amp = a.get("amp", [6.0, 30.0])
    freq = float(a.get("freq", 1.6))
    arms = [(_arm("L"), 1.0), (_arm("R"), -1.0)] if hand == "both" else [(_arm(hand), 1.0)]
    for (ku, kf, _), sign in arms:
        for k, am in ((ku, amp[0]), (kf, amp[1])):
            _from_here(c, f"{cid}.{k}", ["o", t0, t1, 0.0, sign * float(am), freq], a)
    anchor = "head" if hand == "both" else f"hand_{hand}"
    _place(c, a, t0, t1, lambda t: char_point(c, cid, anchor, t), f"{cid} waving")


def _v_clap(c: Compiled, a: dict[str, Any], _d: float) -> None:
    cid, t0, t1 = a["actor"], a["start"], a["end"]
    amp = a.get("amp", [8.0, 22.0])
    freq = float(a.get("freq", 3.0))
    for k, am in (("armL_u", amp[0]), ("armL_f", amp[1]), ("armR_u", -amp[0]), ("armR_f", -amp[1])):
        _from_here(c, f"{cid}.{k}", ["o", t0, t1, 0.0, float(am), freq], a)
    _place(c, a, t0, t1, lambda t: char_point(c, cid, "center", t), f"{cid} (clapping)")


def _v_flip(c: Compiled, a: dict[str, Any], _d: float) -> None:
    cid, sid, t0, t1, hand = a["actor"], a["target"], a["start"], a["end"], a.get("hand", "R")
    n = int(a["count"])
    ku, kf, _ = _arm(hand)
    freq = n / (t1 - t0)
    _seg(c, f"{sid}.flipped", ["e", t0, t1, 0.0, float(n), "lin"], a)
    _from_here(c, f"{cid}.{kf}", ["o", t0, t1, 0.0, float(a.get("amp", 25.0)), freq], a)
    _from_here(c, f"{cid}.gaze_x", ["o", t0, t1, 0.0, 0.6, freq], a)
    c.items[a["id"]] = [t0 + (k - 0.5) * (t1 - t0) / n for k in range(1, n + 1)]
    c.deferred.append(lambda: _contact(c, a, t0, char_point(c, cid, f"hand_{hand}", t0),
                                       prop_point(c, sid, "top", t0), 2 * CONTACT_TOL,
                                       f"{cid}'s {hand} hand on the pages of {sid}"))
    _place(c, a, t0, t1, lambda t: prop_point(c, sid, "top", t), f"{sid} (pages flipped)")


def _v_hold(c: Compiled, a: dict[str, Any], _d: float) -> None:
    cid, pid, t0, t1, hand = a["actor"], a["target"], a["start"], a["end"], a.get("hand", "R")
    # placeholders now (overlap checks), real samples once every arm is known
    for k in ("base_x", "base_y"):
        c.owners.setdefault(f"{pid}.{k}", []).append((t0, t1, a["id"], a["do"]))

    def later() -> None:
        _sampled_no_owner(c, f"{pid}.base_x", t0, t1, lambda t: char_point(c, cid, f"hand_{hand}", t)[0])
        _sampled_no_owner(c, f"{pid}.base_y", t0, t1, lambda t: char_point(c, cid, f"hand_{hand}", t)[1] + 14)
    c.deferred.insert(0, later)
    c.n_samplers += 1
    _place(c, a, t0, t1, lambda t: char_point(c, cid, f"hand_{hand}", t), f"{cid} holding {pid}")


def _sampled_no_owner(c: Compiled, key: str, t0: float, t1: float, fn: Callable[[float], Any]) -> None:
    n = max(1, int(math.ceil((t1 - t0) * SAMPLE_HZ)))
    pts = [[t0 + (t1 - t0) * i / n, fn(t0 + (t1 - t0) * i / n)] for i in range(n + 1)]
    c.ch.add(key, ["s", t0, t1, pts])


def _band_rect(p: dict[str, Any], i: int) -> tuple[float, float]:
    y1 = p["top0"] - p["band_h"] * i
    return y1 - p["band_h"] - p["overlap"], y1 + p["overlap"]


def roller_tip(p: dict[str, Any], width: float, s0: float, step: float, dur: float, extend: float,
               retract: float, rest: Callable[[float], tuple[float, float]], t: float) -> tuple[float, float]:
    """Where the roller head is while the bands are painted (alternate directions)."""
    n = len(p["colors"])
    bp = [M.EASES["io"](M.prog(t, s0 + i * step, s0 + i * step + dur)) for i in range(n)]
    dirs = [1 if i % 2 == 0 else -1 for i in range(n)]
    for i in range(n):
        s = s0 + i * step
        y0, y1 = _band_rect(p, i)
        cy = (y0 + y1) / 2 if i < n - 1 else 150.0
        front = -30 + bp[i] * (width + 60) if dirs[i] > 0 else width + 30 - bp[i] * (width + 60)
        front = M.clamp(front, 110, width - 110)
        if t < s:
            start_x = 110.0 if dirs[i] > 0 else width - 110.0
            if i == 0:
                u = M.EASES["io"](M.prog(t, s0 - extend, s))
                r = rest(t)
                return M.lerp(r[0], start_x, u), M.lerp(r[1], cy, u)
            prev_end = s - step + dur
            u = M.EASES["io"](M.prog(t, prev_end, s))
            py0, py1 = _band_rect(p, i - 1)
            return start_x, M.lerp((py0 + py1) / 2, cy, u)
        if t <= s + dur:
            return front, cy
    end = s0 + (n - 1) * step + dur
    u = M.EASES["io"](M.prog(t, end, end + retract))
    x = 110.0 if dirs[-1] < 0 else width - 110.0
    r = rest(t)
    return M.lerp(x, r[0], u), M.lerp(150.0, r[1], u)


def _v_paint(c: Compiled, a: dict[str, Any], _d: float) -> None:
    cid, bid, pole = a["actor"], a["target"], a["pole"]
    bp, ch = c.props[bid]["params"], c.chars[cid]
    s0, step, dur = a["start"], float(a["step"]), float(a["dur"])
    extend, retract = float(a.get("extend", 0.45)), float(a.get("retract", 0.5))
    n = len(bp["colors"])
    t_from, t_to = s0 - extend, s0 + (n - 1) * step + dur + retract
    c.ends[a["id"]] = t_to
    a["_t_from"] = t_from
    for i in range(n):
        _seg(c, f"{bid}.p_{i + 1}", ["e", s0 + i * step, s0 + i * step + dur, 0.0, 1.0, "io"], a)
    c.props[pole]["params"]["bands"] = bid
    width = c.layout["width"]
    rest = lambda t: (c.ch.value(f"{cid}.x", t) + 50 * ch["scale"] / 1.1,
                      ch["at"][1] - 150 * ch["scale"] / 1.1)
    tip = lambda t: roller_tip(bp, width, s0, step, dur, extend, retract, rest, t)
    start_vals = char_state(c, cid, t_from)
    blend = 0.3

    def body(t: float) -> dict[str, Any]:
        x = c.ch.value(f"{cid}.x", t)
        bx, by = x + 34 * ch["scale"] / 1.1, ch["at"][1] - 110 * ch["scale"] / 1.1
        tx, ty = tip(t)
        ang = math.degrees(math.atan2(tx - bx, ty - by))
        ang = (ang + 180) % 360 - 180
        turn = M.clamp((tx - x) / 400.0, -1, 1)
        v = {"armL_u": M.lerp(-12, ang, 0.35) - 10, "armL_f": ang - 4, "armR_u": M.lerp(12, ang, 0.55),
             "armR_f": ang, "turn": turn, "gaze_x": turn, "gaze_y": -1.0, "lean": -4 * turn}
        u = M.EASES["io"](M.prog(t, t_from, t_from + blend))
        return {k: M.lerp(start_vals[k], vv, u) for k, vv in v.items()}

    for k in ("armL_u", "armL_f", "armR_u", "armR_f", "turn", "gaze_x", "gaze_y", "lean"):
        _sampled(c, f"{cid}.{k}", t_from, t_to, lambda t, k=k: body(t)[k], a)
    _seg(c, f"{cid}.mouth", ["e", t_from, t_from + blend, c.ch.value(f"{cid}.mouth", t_from), "grin", "io"], a)
    for k in ("tip_dx", "tip_dy"):
        c.owners.setdefault(f"{pole}.{k}", []).append((t_from, t_to, a["id"], a["do"]))

    def later() -> None:
        # the roller leaves from, and comes back to, where the pole really rests
        rdx, rdy = c.ch.value(f"{pole}.tip_dx", t_from), c.ch.value(f"{pole}.tip_dy", t_from)
        prest = lambda t: (c.ch.value(f"{pole}.base_x", t) + rdx, c.ch.value(f"{pole}.base_y", t) + rdy)
        ptip = lambda t: roller_tip(bp, width, s0, step, dur, extend, retract, prest, t)
        _sampled_no_owner(c, f"{pole}.tip_dx", t_from, t_to, lambda t: ptip(t)[0] - c.ch.value(f"{pole}.base_x", t))
        _sampled_no_owner(c, f"{pole}.tip_dy", t_from, t_to, lambda t: ptip(t)[1] - c.ch.value(f"{pole}.base_y", t))
    c.deferred.insert(c.n_samplers, later)   # after the holds, before every contact measure
    c.n_samplers += 1
    _place(c, a, t_from, t_to, tip, f"{pole}'s roller")


def _v_move(c: Compiled, a: dict[str, Any], _d: float) -> None:
    pid, t0, t1 = a["target"], a["start"], a["end"]
    x0, y0 = c.ch.value(f"{pid}.x", t0), c.ch.value(f"{pid}.y", t0)
    if "to" in a:
        if isinstance(a["to"], list):
            x1, y1 = float(a["to"][0]), float(a["to"][1])
        else:
            cx, cy = prop_point(c, pid, "center", t0)
            tx, ty = ref_point(c, a["to"], t0)
            x1, y1 = x0 + tx - cx, y0 + ty - cy
    else:
        by = a.get("by", [0, 0])
        x1, y1 = x0 + float(by[0]), y0 + float(by[1])
    _ease(c, f"{pid}.x", t0, t1, x1, a.get("ease", "io"), a)
    _ease(c, f"{pid}.y", t0, t1, y1, a.get("ease", "io"), a)
    _place(c, a, t0, t1, lambda t: prop_point(c, pid, "center", t), f"{pid} (moving)")


def _v_attach(c: Compiled, a: dict[str, Any], _d: float) -> None:
    pid, t0, t1 = a["target"], a["start"], a["end"]
    qid, anchor = _split(a["to"])
    by = a.get("by", "center")
    p = c.props[pid]
    st = prop_state(c, pid, t0)
    blx, bly = (p["anchors"][by] if by in p["anchors"] else L.anchors(p["type"], p["params"], st)[by])
    q = c.props[qid]
    qst = prop_state(c, qid, t0)
    if L.anchor_needs_world(q["type"], anchor or ""):
        if q["type"] == "crane":
            srcx, offx, srcy, offy = f"{qid}.trolley", 0.0, f"{qid}.hook_y", 20.0
        else:
            srcx, offx, srcy, offy = f"{qid}.base_x", 0.0, f"{qid}.base_y", 0.0
    else:
        loc = (q["anchors"][anchor] if anchor in q["anchors"] else
               L.anchors(q["type"], q["params"], qst)[anchor or "center"])
        srcx, offx, srcy, offy = f"{qid}.x", loc[0] * qst["scale"], f"{qid}.y", loc[1] * qst["scale"]
    _seg(c, f"{pid}.x", ["f", t0, t1, srcx, 1.0, offx - blx * st["scale"]], a)
    _seg(c, f"{pid}.y", ["f", t0, t1, srcy, 1.0, offy - bly * st["scale"]], a)
    c.deferred.append(lambda: _contact(c, a, t0, ref_point(c, a["to"], t0),
                                       _before_point(c, pid, by, t0), CONTACT_TOL,
                                       f"{a['to']} takes {pid} by its {by}"))
    _place(c, a, t0, t1, lambda t: prop_point(c, pid, "center", t), f"{pid} (carried)")


def _before_point(c: Compiled, pid: str, anchor: str, t: float) -> tuple[float, float]:
    """Where a prop's anchor was just before t (before an attach took over)."""
    return prop_point(c, pid, anchor, t - 1e-3)


def _v_animate(c: Compiled, a: dict[str, Any], _d: float) -> None:
    pid, t0, t1, chn = a["target"], a["start"], a["end"], a["channel"]
    to = a["to"]
    _ease(c, f"{pid}.{chn}", t0, t1, float(to) if isinstance(to, (int, float)) else to, a.get("ease", "io"), a)
    p = c.props[pid]
    if p["type"] in L.FULL_FRAME_TYPES or p["type"] == "bands":
        return
    anchor = {"crane": "hook", "door": "opening", "pole": "tip"}.get(p["type"], "center")
    if p["type"] == "bars" and chn.startswith(("h_", "reveal_")):
        anchor = "top_" + chn.split("_", 1)[1]
    _place(c, a, t0, t1, lambda t: prop_point(c, pid, anchor, t), f"{pid}.{anchor}")


def _v_shake(c: Compiled, a: dict[str, Any], _d: float) -> None:
    pid, t0, t1 = a["target"], a["start"], a["end"]
    _from_here(c, f"{pid}.x", ["d", t0, t1, 0.0, float(a.get("amp", 5.0)), float(a.get("freq", 6.0)),
                               float(a.get("decay", 9.0))], a)
    _place(c, a, t0, t1, lambda t: prop_point(c, pid, "center", t), f"{pid} (shaking)")


def _v_tip_over(c: Compiled, a: dict[str, Any], _d: float) -> None:
    jid, tip, crash = a["target"], a["start"], a["end"]
    side = int(a["side"])
    bump = float(a.get("wobble_from", tip))
    a["_t_from"] = bump          # the action's window starts with the wobble
    x0, y0 = c.ch.value(f"{jid}.x", bump), c.ch.value(f"{jid}.y", bump)
    land = a["land"]
    land_y = float(land) if isinstance(land, (int, float)) else ref_point(c, land, crash)[1]
    slide = float(a.get("slide", 85.0))
    sh = a.get("shake") or {"amp": 0.0, "freq": 6.0, "decay": 9.0}
    h = c.props[jid]["params"].get("h", 60)
    settle = crash + 1.0

    def shake(t: float) -> float:
        u = max(0.0, t - bump)
        return sh["amp"] * math.exp(-sh["decay"] * u) * math.sin(2 * math.pi * sh["freq"] * u)

    def wobble(t: float) -> float:
        k = M.prog(t, bump, tip)
        return 0.16 * math.sin(2 * math.pi * 3.2 * (t - bump)) * (0.4 + k) * side

    rot_tip, x_tip = wobble(tip), x0 + shake(tip)

    def state(t: float) -> tuple[float, float, float]:
        if t < tip:
            return x0 + shake(t), y0, wobble(t)
        if t < crash:
            u = M.prog(t, tip, crash)
            return (M.lerp(x_tip, x0 + slide * side, u), M.lerp(y0, land_y, M.EASES["in"](u)),
                    M.lerp(rot_tip, 1.57 * side, M.EASES["in"](u)))
        b = t - crash
        return (x0 + slide * side, land_y - 6 * abs(math.exp(-8 * b) * math.sin(2 * math.pi * 2.5 * b)),
                side * (1.57 - 0.08 * math.exp(-7 * b) * math.sin(2 * math.pi * 3 * b)))

    for i, k in enumerate(("x", "y", "rot")):
        _sampled(c, f"{jid}.{k}", bump, settle, lambda t, i=i: state(t)[i], a)
    _seg(c, f"{jid}.empty", ["e", crash, crash + 0.35, 0.0, 1.0, "io"], a)
    mouth = lambda: L.place(*state(crash)[:2], state(crash)[2], 1.0, (0.0, -h))
    lid_to = a.get("lid_to")

    def lid(t: float) -> tuple[float, float, float]:
        mx, my = mouth()
        tx, ty = ref_point(c, lid_to, crash) if lid_to else (mx + 40 * side, land_y)
        b = t - crash
        if b < 0.6:
            u = b / 0.6
            return M.lerp(mx, tx, u), M.lerp(my, ty, u) - 230 * 4 * u * (1 - u), 1.57 * side * (1 - u) + 4 * math.pi * u
        return tx, ty, 0.12 * math.exp(-5 * (b - 0.6)) * math.sin(2 * math.pi * 3 * (b - 0.6))

    for i, k in enumerate(("lid_x", "lid_y", "lid_rot")):
        _sampled(c, f"{jid}.{k}", crash, settle, lambda t, i=i: lid(t)[i], a)
    _seg(c, f"{jid}.lid_free", ["e", crash, crash, 0.0, 1.0, "lin"], a)
    _place(c, a, bump, crash, lambda t: L.place(*state(t)[:2], state(t)[2], 1.0, (0.0, -h)), f"{jid}'s mouth")


def _v_drop_in(c: Compiled, a: dict[str, Any], _d: float) -> None:
    jid, t0, t1, n = a["target"], a["start"], a["end"], int(a["count"])
    c0 = c.ch.value(f"{jid}.count", t0)
    _seg(c, f"{jid}.count", ["e", t0, t1, c0, c0 + n, "lin"], a)
    times = [t0 + k * (t1 - t0) / n for k in range(1, n + 1)]
    fall = min(0.35, (t1 - t0) / n * 1.5)
    drops = c.extras.setdefault(jid, {}).setdefault("drops", [])
    for tk in times:
        sx, sy = ref_point(c, a["from"], tk - fall)
        ex, ey = prop_point(c, jid, "inside", tk)
        drops.append([round(tk - fall, 4), round(tk, 4), sx, sy, ex, ey])
    c.items[a["id"]] = times
    _place(c, a, t0, t1, lambda t: prop_point(c, jid, "mouth", t), f"{jid} (filling)")


def _v_overflow(c: Compiled, a: dict[str, Any], _d: float) -> None:
    jid, t0, t1 = a["target"], a["start"], a["end"]
    n = int(a.get("count", 3))
    _seg(c, f"{jid}.spill", ["e", t0, t1, 0.0, float(n), "lin"], a)
    c.items[a["id"]] = [t0 + (k - 0.5) * (t1 - t0) / n for k in range(1, n + 1)]
    _place(c, a, t0, t1, lambda t: prop_point(c, jid, "mouth", t), f"{jid} (overflowing)")


def _v_stack_add(c: Compiled, a: dict[str, Any], _d: float) -> None:
    sid, t0, t1 = a["target"], a["start"], a["end"]
    c0 = c.ch.value(f"{sid}.count", t0)
    _seg(c, f"{sid}.count", ["e", t1, t1, c0, c0 + 1.0, "lin"], a)
    sx, sy = ref_point(c, a["from"], t0)
    n = max(2, int(math.ceil((t1 - t0) * SAMPLE_HZ)))
    pts = []
    for i in range(n + 1):
        t = t0 + (t1 - t0) * i / n
        ex, ey = prop_point(c, sid, "top", t)
        u = M.EASES["io"](i / n)
        pts.append([round(t, 4), round(M.lerp(sx, ex, u), 2), round(M.lerp(sy, ey - c.props[sid]["params"]["sheet"],
                                                                           u), 2)])
    c.extras.setdefault(sid, {}).setdefault("flights", []).append(pts)
    c.items[a["id"]] = [t1]
    _place(c, a, t0, t1, lambda t: prop_point(c, sid, "top", t), f"{sid} (sheet added)")


def burst_particles(p: dict[str, Any], start: float, origin: tuple[float, float]) -> list[dict[str, Any]]:
    """Seeded particles: each leaves the source in a column, holds, then flies to its place."""
    rng = random.Random(p["seed"])
    x0, y0, x1, y1 = p["area"]
    parts: list[dict[str, Any]] = []
    tries = 0
    while len(parts) < p["count"] and tries < 5000:
        tries += 1
        tx, ty = rng.uniform(x0, x1), rng.uniform(y0, y1)
        bad = False
        for av in p["avoid"]:
            if len(av) == 4 and av[0] < tx < av[2] and av[1] < ty < av[3]:
                bad = True
            if len(av) == 3 and math.hypot(tx - av[0], ty - av[1]) < av[2]:
                bad = True
        if bad or any(math.hypot(tx - q["tx"], ty - q["ty"]) < p["spacing"] for q in parts):
            continue
        r = rng.choice(p["sizes"])
        k = len(parts)
        t0 = start + k * 0.009
        ts = start + 0.78 + k * 0.012
        d = rng.uniform(*p["spread"])
        (gxa, gxb), (gya, gyb) = p["column"]
        q = dict(tx=tx, ty=ty, r=r, t0=t0, ts=ts, d=d, land=ts + d, gx=rng.uniform(gxa, gxb),
                 gy=rng.uniform(gya, gyb), bend=rng.uniform(-220, 220), lift=rng.uniform(120, 300),
                 spin=rng.uniform(-9, 9), ph=rng.uniform(0, 6.28), w=rng.uniform(2.0, 4.5))
        parts.append(q)
    ox, oy = origin
    for q in parts:
        gx, gy = ox + q["gx"], oy - q["gy"]

        def pos(t: float, q=q, gx=gx, gy=gy) -> tuple[float, float]:
            if t < q["ts"]:
                e = M.EASES["out"](M.prog(t, q["t0"], q["t0"] + 0.5))
                return (M.lerp(ox, gx, e) + 6 * math.sin(3 * t + q["ph"]) * e,
                        M.lerp(oy, gy, e) - 10 * e * math.sin(2.2 * (t - q["t0"]) + q["ph"]))
            sx0 = gx + 6 * math.sin(3 * q["ts"] + q["ph"])
            sy0 = gy - 10 * math.sin(2.2 * (q["ts"] - q["t0"]) + q["ph"])
            e = M.EASES["io"](M.prog(t, q["ts"], q["land"]))
            cx, cy = (sx0 + q["tx"]) / 2 + q["bend"], min(sy0, q["ty"]) - q["lift"]
            return ((1 - e) ** 2 * sx0 + 2 * (1 - e) * e * cx + e * e * q["tx"],
                    (1 - e) ** 2 * sy0 + 2 * (1 - e) * e * cy + e * e * q["ty"])

        n = int(math.ceil((q["land"] - q["t0"]) * 60))
        q["path"] = [[round(q["t0"] + (q["land"] - q["t0"]) * i / n, 4)] +
                     [round(v, 2) for v in pos(q["t0"] + (q["land"] - q["t0"]) * i / n)] for i in range(n + 1)]
    return parts


def _v_burst(c: Compiled, a: dict[str, Any], _d: float) -> None:
    bid, t0 = a["target"], a["start"]
    origin = ref_point(c, a["from"], t0)
    parts = burst_particles(c.props[bid]["params"], t0, origin)
    ex = c.extras.setdefault(bid, {})
    ex["origin"] = [round(origin[0], 2), round(origin[1], 2)]
    ex["start"] = t0
    ex["particles"] = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in q.items()
                        if k in ("r", "t0", "ts", "land", "spin", "ph", "w", "path", "tx", "ty")} for q in parts]
    end = max([q["land"] for q in parts] + [t0 + 0.9])
    c.ends[a["id"]] = end
    c.items[a["id"]] = sorted(q["land"] for q in parts)
    c.owners.setdefault(f"{bid}.burst", []).append((t0, end, a["id"], a["do"]))
    _place(c, a, t0, t0 + 0.5, lambda t: origin, f"{bid}'s source")


def _v_layer(c: Compiled, a: dict[str, Any], _d: float) -> None:
    rel = "behind" if "behind" in a else "in_front_of"
    c.layers.append([a["start"], a["target"], rel, a[rel]])


VERB_COMPILERS: dict[str, Callable[[Compiled, dict[str, Any], float], None]] = {
    "pose": _v_pose, "shrug": _v_shrug, "walk_to": _v_walk_to, "push": _v_push, "reach": _v_reach,
    "point_at": _v_point_at, "look_at": _v_look_at, "wave": _v_wave, "clap": _v_clap, "flip": _v_flip,
    "hold": _v_hold, "paint": _v_paint, "move": _v_move, "attach": _v_attach, "animate": _v_animate,
    "shake": _v_shake, "tip_over": _v_tip_over, "drop_in": _v_drop_in, "overflow": _v_overflow,
    "burst": _v_burst, "stack_add": _v_stack_add, "layer": _v_layer,
}
assert set(VERB_COMPILERS) == set(L.VERBS)


def action_window(a: dict[str, Any], c: Compiled) -> tuple[float, float]:
    """The interval an action occupies (computed ends included)."""
    t0 = a.get("_t_from", a["start"])
    t1 = c.ends.get(a["id"], a.get("end", a["start"]))
    return t0, t1


def camera_keys(c: Compiled, keys: list[dict[str, Any]], times) -> list[list[float]]:
    out = []
    for i, k in enumerate(keys):
        t = times(k.get("t"), f"camera.{c.layout['name']}[{i}]")
        if t is None:
            continue
        if "target" in k:
            cx, cy = ref_point(c, k["target"], t)
        else:
            cx, cy = float(k["center"][0]), float(k["center"][1])
        out.append([t, cx, cy, float(k.get("zoom", 1.0))])
    out.sort(key=lambda k: k[0])
    return out
