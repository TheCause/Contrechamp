"""Motion rules of a compiled paper scene: channels, camera, puppet kinematics.

These rules are written twice: here (the compiler and the pre-render checks
read them) and in ``ink-theater/paper-scene.js`` (the render reads them). A
test runs the JavaScript evaluator on a compiled scene and compares it with
this one, so the checks look at the motion that is actually rendered.

A channel is ``{"v": initial, "s": [segment, ...]}``; segments are sorted by
start time and never overlap. Before the first segment the channel holds its
initial value; after a segment ends it holds that segment's last value. Kinds:

- ``["e", t0, t1, v0, v1, ease]``  eased from v0 to v1 (numbers interpolate,
  ``#rrggbb`` colours mix, other strings switch half-way);
- ``["o", t0, t1, base, amp, freq]``  base + amp * sin(2 pi freq (t - t0));
- ``["d", t0, t1, base, amp, freq, decay]``  base + amp * exp(-decay u) * sin(2 pi freq u);
- ``["f", t0, t1, src, k, off]``  k * value(src at t) + off (src = "id.channel");
- ``["s", t0, t1, [[t, v], ...]]``  sampled, linear between samples.
"""

from __future__ import annotations

import bisect
import math
from typing import Any, Callable

EASES: dict[str, Callable[[float], float]] = {
    "io": lambda x: x * x * (3 - 2 * x),
    "io5": lambda x: x * x * x * (x * (6 * x - 15) + 10),
    "out": lambda x: 1 - (1 - x) ** 3,
    "in": lambda x: x * x * x,
    "lin": lambda x: x,
}


def clamp(x: float, a: float = 0.0, b: float = 1.0) -> float:
    return max(a, min(b, x))


def lerp(a: float, b: float, x: float) -> float:
    return a + (b - a) * x


def prog(t: float, t0: float, t1: float) -> float:
    if t1 <= t0:
        return 1.0 if t >= t1 else 0.0
    return clamp((t - t0) / (t1 - t0))


def is_color(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 7 and v.startswith("#")


def _rgb(h: str) -> list[int]:
    return [int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)]


def mix(h1: str, h2: str, x: float) -> str:
    a, b = _rgb(h1), _rgb(h2)
    # floor(v + 0.5) is JavaScript's Math.round (Python's round() rounds half to even)
    return "#" + "".join(f"{int(math.floor(clamp(lerp(p, q, x), 0, 255) + 0.5)):02x}" for p, q in zip(a, b))


def interp(v0: Any, v1: Any, x: float) -> Any:
    if isinstance(v1, (int, float)) and isinstance(v0, (int, float)):
        return v0 + (v1 - v0) * x
    if is_color(v0) and is_color(v1):
        return mix(v0, v1, x)
    return v1 if x > 0.5 else v0


# ------------------------------------------------------------------ channels

class Channels:
    """All channels of one compiled layout, by ``"id.channel"``."""

    def __init__(self) -> None:
        self.ch: dict[str, dict[str, Any]] = {}
        self._starts: dict[str, list[float]] = {}

    def define(self, key: str, initial: Any) -> None:
        self.ch.setdefault(key, {"v": initial, "s": []})

    def has(self, key: str) -> bool:
        return key in self.ch

    def segments(self, key: str) -> list[list[Any]]:
        return self.ch[key]["s"]

    def add(self, key: str, seg: list[Any]) -> None:
        """Insert a segment (kept sorted). Overlaps are found by the checks."""
        segs = self.ch[key]["s"]
        i = bisect.bisect_right([s[1] for s in segs], seg[1])
        segs.insert(i, seg)
        self._starts.pop(key, None)

    def value(self, key: str, t: float) -> Any:
        c = self.ch[key]
        segs = c["s"]
        if not segs or t < segs[0][1]:
            return c["v"]
        starts = self._starts.get(key)
        if starts is None:
            starts = [s[1] for s in segs]
            self._starts[key] = starts
        i = bisect.bisect_right(starts, t) - 1
        seg = segs[i]
        return self.seg_value(seg, min(t, seg[2]))

    def value_before(self, key: str, seg_index: int) -> Any:
        """Value the channel would have at a segment's start without it."""
        c = self.ch[key]
        segs = c["s"]
        if seg_index == 0:
            return c["v"]
        prev = segs[seg_index - 1]
        return self.seg_value(prev, min(segs[seg_index][1], prev[2]))

    def seg_value(self, seg: list[Any], t: float) -> Any:
        kind = seg[0]
        if kind == "e":
            _, t0, t1, v0, v1, ease = seg
            x = 1.0 if t1 <= t0 else clamp((t - t0) / (t1 - t0))
            return interp(v0, v1, EASES[ease](x))
        if kind == "o":
            _, t0, _t1, base, amp, freq = seg
            return base + amp * math.sin(2 * math.pi * freq * (t - t0))
        if kind == "d":
            _, t0, _t1, base, amp, freq, decay = seg
            u = max(0.0, t - t0)
            return base + amp * math.exp(-decay * u) * math.sin(2 * math.pi * freq * u)
        if kind == "f":
            _, _t0, _t1, src, k, off = seg
            return k * self.value(src, t) + off
        if kind == "s":
            pts = seg[3]
            if t <= pts[0][0]:
                return pts[0][1]
            if t >= pts[-1][0]:
                return pts[-1][1]
            j = bisect.bisect_right([p[0] for p in pts], t)
            (ta, va), (tb, vb) = pts[j - 1], pts[j]
            return interp(va, vb, (t - ta) / (tb - ta) if tb > ta else 1.0)
        raise ValueError(f"unknown segment kind {kind!r}")

    def as_json(self) -> dict[str, Any]:
        return self.ch


# ------------------------------------------------------------------- camera

def camera_at(keys: list[list[float]], t: float, width: float, height: float) -> tuple[float, float, float]:
    """Centre (world px) and zoom at t; the view never leaves the layout."""
    if t >= keys[-1][0]:
        cx, cy, z = keys[-1][1:4]
    elif t <= keys[0][0]:
        cx, cy, z = keys[0][1:4]
    else:
        cx, cy, z = keys[-1][1:4]
        for a, b in zip(keys, keys[1:]):
            if a[0] <= t <= b[0]:
                u = EASES[a[4] if len(a) > 4 else "io"](prog(t, a[0], b[0]))
                cx, cy, z = (lerp(p, q, u) for p, q in zip(a[1:4], b[1:4]))
                break
    hw, hh = width / (2 * z), height / (2 * z)
    return clamp(cx, hw, width - hw), clamp(cy, hh, height - hh), z


def view_rect(keys, t, width, height) -> tuple[float, float, float, float]:
    cx, cy, z = camera_at(keys, t, width, height)
    hw, hh = width / (2 * z), height / (2 * z)
    return cx - hw, cy - hh, cx + hw, cy + hh


def to_screen(keys, t, width, height, x, y) -> tuple[float, float]:
    cx, cy, z = camera_at(keys, t, width, height)
    return (x - cx) * z + width / 2, (y - cy) * z + height / 2


# --------------------------------------------------------------- kinematics
# The paper worker of ink-theater/paper-cut.js (workerPose): canonical units,
# y down, origin between the feet; angles in degrees, 0 = hanging down,
# positive = towards screen right. Upper arm 25, forearm 24.

UPPER, FORE = 25.0, 24.0
SEAT_DROP = 22.0  # a seated worker's pelvis is at seat height: upper body this much lower


def vec(a_deg: float, length: float) -> tuple[float, float]:
    a = math.radians(a_deg)
    return length * math.sin(a), length * math.cos(a)


def shoulder(side: int, shrug: float, seated: bool) -> tuple[float, float]:
    return side * 26.0, -94.0 - shrug * 13.0 + (SEAT_DROP if seated else 0.0)


def _lean(pt: tuple[float, float], lean_deg: float) -> tuple[float, float]:
    a = math.radians(lean_deg)
    return pt[0] * math.cos(a) - pt[1] * math.sin(a), pt[0] * math.sin(a) + pt[1] * math.cos(a)


def hand_local(side: int, up: float, fore: float, shrug: float, lean: float, seated: bool) -> tuple[float, float]:
    s = shoulder(side, shrug, seated)
    e = (s[0] + vec(up, UPPER)[0], s[1] + vec(up, UPPER)[1])
    h = (e[0] + vec(fore, FORE)[0], e[1] + vec(fore, FORE)[1])
    return _lean(h, lean)


def to_world(x: float, ground: float, scale: float, local: tuple[float, float]) -> tuple[float, float]:
    return x + local[0] * scale, ground + local[1] * scale


def to_local(x: float, ground: float, scale: float, lean: float, world: tuple[float, float]) -> tuple[float, float]:
    lx, ly = (world[0] - x) / scale, (world[1] - ground) / scale
    return _lean((lx, ly), -lean)


def ik(side: int, target_local: tuple[float, float], shrug: float, seated: bool,
       near: tuple[float, float] | None = None) -> tuple[float, float, float]:
    """(upper, fore, miss): arm angles towards a point in the unleaned body
    frame; ``miss`` = how far (canonical units) the hand stays from it."""
    sx, sy = shoulder(side, shrug, seated)
    dx, dy = target_local[0] - sx, target_local[1] - sy
    dist = math.hypot(dx, dy)
    theta = math.degrees(math.atan2(dx, dy))
    if dist >= UPPER + FORE - 1e-9:
        up = fore = theta
    else:
        dist_c = max(dist, abs(UPPER - FORE) + 1e-6)
        alpha = math.degrees(math.acos(clamp((UPPER ** 2 + dist_c ** 2 - FORE ** 2) / (2 * UPPER * dist_c), -1, 1)))
        best = None
        for up in (theta - alpha, theta + alpha):
            ex, ey = sx + vec(up, UPPER)[0], sy + vec(up, UPPER)[1]
            fore = math.degrees(math.atan2(target_local[0] - ex, target_local[1] - ey))
            key = (ey, side * ex)  # elbow low, then outward
            if best is None or key > best[0]:
                best = (key, up, fore)
        _, up, fore = best
    if near is not None:  # same direction, closest to the current angles (no spin)
        up = near[0] + ((up - near[0] + 180) % 360 - 180)
        fore = near[1] + ((fore - near[1] + 180) % 360 - 180)
    hx = sx + vec(up, UPPER)[0] + vec(fore, FORE)[0]
    hy = sy + vec(up, UPPER)[1] + vec(fore, FORE)[1]
    return up, fore, math.hypot(hx - target_local[0], hy - target_local[1])
