"""Validate and compile a paper scene into its three outputs.

    report = validate(scene)                    # checks only
    result = compile_scene(scene, out_dir)      # checks, then writes the outputs

Outputs (docs/fork/lot4-paper-scene.md, section 7): one HyperFrames project
per layout, ``sfx_events.json`` (sfx_synth input), ``story_beats.json``
(mute_review input), ``report.json`` and ``scene.resolved.json``. A scene
with an error is not written: the report says why.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from lib.paper_scene import checks as K
from lib.paper_scene import library as L
from lib.paper_scene.compile import (Compiled, action_window, camera_keys, compile_action, new_compiled,
                                     refresh_starts)
from lib.paper_scene.resolve import (Issues, Times, _known, _num, check_vocabulary, expand_actions,
                                     layout_view)

ROOT = Path(__file__).resolve().parents[2]
INK = ROOT / "ink-theater"
GSAP = ROOT / ".agents" / "skills" / "music-to-video" / "references" / "motion-primitives" / "assets" / "gsap.min.js"
FONT = INK / "assets" / "patrickhand.ttf"
CHECKS = ("vocabulary", "references", "timing", "limb_overlap", "jump", "off_frame", "contact", "zoom_speed",
          "ui_safe_bottom", "beat_in_phrase", "text_policy")
STATIC = ("vocabulary", "references", "timing")


class Build:
    """Everything computed from one scene (no file written)."""

    def __init__(self, scene: dict[str, Any]) -> None:
        self.scene = scene
        self.issues = Issues()
        self.measured: dict[str, Any] = {}
        self.layouts: dict[str, Compiled] = {}
        self.actions: list[dict[str, Any]] = []
        self.beats: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.duration = 0.0
        self.marks: dict[str, list[float]] = {}
        self.ran: set[str] = set()
        self._run()

    # -------------------------------------------------------------- stages
    def _run(self) -> None:
        sc = self.scene
        if not check_vocabulary(sc, self.issues):
            return
        self._schema()
        if self.issues.of("vocabulary"):
            return
        self._marks()
        times = Times(self.marks, self.issues)
        d = times(sc["duration"], "duration")
        if d is not None and d <= 0:
            self.issues.add("timing", f"duration {d:g} must be > 0", "duration")
        self.duration = d or 0.0
        self.times = times
        self.actions = expand_actions(sc, times, self.issues)
        self._references()
        self._timing()
        self.ran.update(STATIC)
        if any(self.issues.of(k) for k in STATIC):
            return
        order = {a["id"]: i for i, a in enumerate(self.actions)}
        for name, lay in sc["layouts"].items():
            props, chars = layout_view(sc, name)
            c = new_compiled(dict(lay, name=name), props, chars)
            for a in sorted(self.actions, key=lambda a: (a["start"], order[a["id"]])):
                compile_action(c, a, self.duration)
            for job in c.resolvers:        # targets known once every action is in
                job()
            refresh_starts(c)
            jobs = list(c.deferred)
            samplers, contacts = jobs[:c.n_samplers], jobs[c.n_samplers:]
            for job in samplers:          # held props and pole tips, once every arm is known
                job()
            refresh_starts(c)
            for job in contacts:          # contacts are measured on the final motion
                job()
            c.camera = camera_keys(c, sc["camera"][name], times)
            self.layouts[name] = c
        self._beats_and_sound()
        for name, c in self.layouts.items():
            K.check_limb_overlap(c, self.issues)
            self.measured.setdefault("jump", {})[name] = K.check_jump(c, self.issues)
            K.check_off_frame(c, self.issues, self.duration)
            self.measured.setdefault("contact", {})[name] = K.check_contact(c, self.issues)
            self.measured.setdefault("zoom_speed", {})[name] = K.check_zoom_speed(c, self.issues)
            K.check_ui_safe_bottom(c, self.issues, self.duration)
        K.check_beat_in_phrase(self.beats, self.marks, self.issues)
        self.measured["text_policy"] = K.check_text_policy(sc, self.issues)
        self.ran.update(CHECKS)

    def _schema(self) -> None:
        import jsonschema

        from schemas.artifacts import load_schema

        v = jsonschema.Draft202012Validator(load_schema("paper_scene"))
        for e in sorted(v.iter_errors(self.scene), key=lambda e: list(e.absolute_path)):
            where = "/".join(str(p) for p in e.absolute_path) or "(root)"
            self.issues.add("vocabulary", f"schema: {e.message}", where)

    def _marks(self) -> None:
        for name, m in (self.scene.get("marks") or {}).items():
            if not (isinstance(m, list) and len(m) == 2 and all(_num(x) for x in m) and 0 <= m[0] <= m[1]):
                self.issues.add("timing", f"mark {name!r} must be [start, end] in seconds", "marks")
                continue
            self.marks[name] = [float(m[0]), float(m[1])]

    # ---------------------------------------------------------- references
    def _anchor_names(self, pid: str) -> set[str]:
        p = next(q for q in self.scene["props"] if q["id"] == pid)
        params = {k: v for k, v in L.PROP_TYPES[p["type"]]["params"].items() if v is not L.REQ}
        params.update(p.get("params") or {})
        ch = L.prop_channels(p["type"], params)
        names = set(L.anchors(p["type"], params, ch)) | set((p.get("anchors") or {}))
        return names

    def _ref(self, ref: Any, where: str, allow_chars: bool = True, kind: str | None = None) -> None:
        if isinstance(ref, list):
            if not (len(ref) == 2 and all(_num(x) for x in ref)):
                self.issues.add("references", f"point {ref!r} must be [x, y]", where)
            return
        if not isinstance(ref, str):
            self.issues.add("references", f"{ref!r} must be 'id' or 'id.anchor'", where)
            return
        rid, _, anchor = ref.partition(".")
        if rid in self.chars and allow_chars:
            if anchor and anchor not in L.CHAR_ANCHORS:
                self.issues.add("references", f"{ref}: " + _known("character anchor", anchor, L.CHAR_ANCHORS), where)
            return
        if rid not in self.props:
            known = set(self.props) | (set(self.chars) if allow_chars else set())
            self.issues.add("references", _known("prop" if not allow_chars else "prop or character", rid, known),
                            where)
            return
        if kind and self.props[rid] != kind:
            self.issues.add("references", f"{rid} is a {self.props[rid]}, this needs a {kind}", where)
        if anchor:
            names = self._anchor_names(rid)
            if anchor not in names:
                self.issues.add("references", f"{ref}: " + _known(f"anchor of {self.props[rid]}", anchor, names), where)

    def _references(self) -> None:
        sc = self.scene
        self.props = {p["id"]: p["type"] for p in sc["props"]}
        self.chars = {c["id"]: c for c in sc.get("characters") or []}
        ids = set(self.props) | set(self.chars)
        order = sc.get("order") or []
        for p in sc["props"]:
            if p.get("init"):
                params = {k: v for k, v in L.PROP_TYPES[p["type"]]["params"].items() if v is not L.REQ}
                params.update(p.get("params") or {})
                chans = L.prop_channels(p["type"], params)
                for k in p["init"]:
                    if k not in chans or k in ("x", "y"):
                        self.issues.add("references", f"init of {p['id']}: " + _known(
                            "channel", k, [c for c in chans if c not in ("x", "y")]) + " (x, y come from at)",
                            f"props.{p['id']}")
        for x in order:
            if x not in ids:
                self.issues.add("references", "order: " + _known("id", x, ids), "order")
        missing = ids - set(order)
        if missing:
            self.issues.add("references", f"order must list every prop and character; missing: {sorted(missing)}",
                            "order")
        dup = {x for x in order if order.count(x) > 1}
        if dup:
            self.issues.add("references", f"order lists {sorted(dup)} more than once", "order")
        for name in sc["layouts"]:
            if not (sc.get("camera") or {}).get(name):
                self.issues.add("references", f"layout {name!r} has no camera keys", "camera")
        for name, keys in (sc.get("camera") or {}).items():
            if name not in sc["layouts"]:
                self.issues.add("references", _known("layout", name, sc["layouts"]), "camera")
            for j, k in enumerate(keys):
                w = f"camera.{name}[{j}]"
                if ("center" in k) == ("target" in k):
                    self.issues.add("references", "a camera key has center or target (one of them)", w)
                if "target" in k:
                    self._ref(k["target"], w)
                if not (_num(k.get("zoom", 1.0)) and k.get("zoom", 1.0) >= 1.0):
                    self.issues.add("references", "zoom must be >= 1 (zoom 1 shows the whole layout)", w)
        for a in self.actions:
            w, verb = a["_where"], a["do"]
            spec = L.VERBS[verb]
            if spec["actor"] == "character" and a.get("actor") not in self.chars:
                self.issues.add("references", f"{verb}: " + _known("character", a.get("actor"), self.chars), w)
            tk = spec["target"]
            tgt = a.get("target")
            if tk == "prop" and tgt not in self.props:
                self.issues.add("references", f"{verb}: " + _known("prop", tgt, self.props), w)
            elif tk in ("stack", "bands", "pole", "container", "burst"):
                if self.props.get(tgt) != tk:
                    cands = [p for p, t in self.props.items() if t == tk]
                    self.issues.add("references", f"{verb} needs a {tk} prop as target, got {tgt!r} "
                                    f"(known {tk} props: {', '.join(cands) or 'none'})", w)
            elif tk == "any":
                self._ref(tgt, w)
            elif tk == "any_id" and tgt not in ids:
                self.issues.add("references", f"{verb}: " + _known("id", tgt, ids), w)
            for key in ("to", "from", "land", "lid_to"):
                if verb == "animate" and key == "to":
                    if not (_num(a[key]) or (isinstance(a[key], str) and a[key].startswith("#")
                                             and len(a[key]) == 7)):
                        self.issues.add("references", f"animate: to must be a number or a #rrggbb colour, "
                                        f"got {a[key]!r}", w)
                    continue
                if key in a and not _num(a[key]) and not (verb == "move" and key == "to" and isinstance(a[key], list)):
                    self._ref(a[key], w, allow_chars=key != "to" or verb != "attach")
            if verb == "attach":
                rid = str(a.get("to", "")).partition(".")[0]
                if rid not in self.props:
                    self.issues.add("references", "attach: to must be 'prop.anchor'", w)
                if "by" in a and tgt in self.props and a["by"] not in self._anchor_names(tgt):
                    self.issues.add("references", f"attach: " + _known("anchor", a["by"], self._anchor_names(tgt)), w)
            if verb == "paint" and self.props.get(a.get("pole")) != "pole":
                self.issues.add("references", "paint: " + _known("pole", a.get("pole"),
                                [p for p, t in self.props.items() if t == "pole"]), w)
            if verb == "push" and "grip" in a and tgt in self.props and a["grip"] not in self._anchor_names(tgt):
                self.issues.add("references", "push: " + _known("grip anchor", a["grip"], self._anchor_names(tgt)), w)
            if verb == "animate" and tgt in self.props:
                p = next(q for q in self.scene["props"] if q["id"] == tgt)
                params = {k: v for k, v in L.PROP_TYPES[p["type"]]["params"].items() if v is not L.REQ}
                params.update(p.get("params") or {})
                chans = L.prop_channels(p["type"], params)
                if a.get("channel") not in chans:
                    self.issues.add("references", f"animate {tgt}: " + _known("channel", a.get("channel"), chans), w)
            if verb == "layer":
                ref = a.get("behind", a.get("in_front_of"))
                if ref not in ids:
                    self.issues.add("references", "layer: " + _known("id", ref, ids), w)
            if verb in ("flip", "drop_in", "overflow") and "count" in a:
                if not (isinstance(a["count"], int) and a["count"] >= 1):
                    self.issues.add("references", f"{verb}: count must be a positive integer, got {a['count']!r}", w)

    # -------------------------------------------------------------- timing
    def _timing(self) -> None:
        d = self.duration
        for a in self.actions:
            w = a["_where"]
            s, e = a.get("start"), a.get("end", a.get("start"))
            if s is None or e is None:
                continue
            if not (0 <= s <= d + 1e-9) or not (0 <= e <= d + 1e-9):
                self.issues.add("timing", f"{a['do']} at {s:g}-{e:g}s is outside the scene (0-{d:g}s)", w)
            if e < s:
                self.issues.add("timing", f"{a['do']}: end {e:g}s is before start {s:g}s", w)
            if a["do"] == "tip_over" and "wobble_from" in a:
                wf = self.times(a["wobble_from"], w)
                a["wobble_from"] = wf
                if wf is not None and wf > s:
                    self.issues.add("timing", "tip_over: wobble_from must not be after start", w)
            if a["do"] == "paint":
                n = len(next(p for p in self.scene["props"] if p["id"] == a["target"])["params"]["colors"]) \
                    if self.props.get(a["target"]) == "bands" else 0
                end = s + (n - 1) * float(a.get("step", 0)) + float(a.get("dur", 0)) + float(a.get("retract", 0.5))
                if end > d + 1e-9 or s - float(a.get("extend", 0.45)) < 0:
                    self.issues.add("timing", f"paint runs {s - float(a.get('extend', 0.45)):.2f}-{end:.2f}s, "
                                    f"outside the scene (0-{d:g}s)", w)
        for i, b in enumerate(self.scene.get("beats") or []):
            for k in ("start", "end"):
                b_t = self.times(b.get(k), f"beats[{i}]")
                if b_t is not None and not 0 <= b_t <= d + 1e-9:
                    self.issues.add("timing", f"beat {b.get('id')!r} {k} {b_t:g}s is outside the scene", f"beats[{i}]")

    # -------------------------------------------------- beats and sound
    def _beats_and_sound(self) -> None:
        first = next(iter(self.layouts.values()))
        beats = []
        for i, b in enumerate(self.scene.get("beats") or []):
            beats.append({"id": b["id"], "label": b["label"], "start": self.times(b["start"], f"beats[{i}]"),
                          "end": self.times(b["end"], f"beats[{i}]"), "expected": b["expected"],
                          **({"phrase": b["phrase"]} if "phrase" in b else {})})
        for a in self.actions:
            b = a.get("beat")
            if not b:
                continue
            t0, t1 = action_window(a, first)
            beats.append({"id": b["id"], "label": b["label"], "start": b.get("start", t0),
                          "end": b.get("end", t1), "expected": b["expected"]})
        seen = set()
        for b in beats:
            if b["id"] in seen:
                self.issues.add("timing", f"duplicate beat id {b['id']!r}", b["id"])
            seen.add(b["id"])
            if b["start"] is None or b["end"] is None or b["end"] < b["start"] or b["end"] > self.duration + 1e-9:
                self.issues.add("timing", f"beat {b['id']!r} window {b['start']}-{b['end']} is not inside the scene",
                                b["id"])
        self.beats = sorted(beats, key=lambda b: (b["start"] or 0, b["end"] or 0))
        events = []
        from tools.audio.sfx_synth import KIND_PARAMS

        for a in self.actions:
            t0, t1 = action_window(a, first)
            for s in a.get("sound") or []:
                base = {k: v for k, v in s.items() if k in KIND_PARAMS[s["kind"]] or k in ("kind", "gain", "pan")}
                if "per_item" in s:
                    for k, tk in enumerate(first.items.get(a["id"], []), start=1):
                        ev = dict(base, t=tk)
                        if s.get("pitch_step") and "freq" in KIND_PARAMS[s["kind"]]:
                            f0 = s.get("freq", KIND_PARAMS[s["kind"]]["freq"])
                            ev["freq"] = f0 * 2 ** ((k - 1) * s["pitch_step"] / 12)
                        events.append((ev, a))
                elif "every" in s:   # from the window start (+ at), one event per period
                    k, first_t = 0, t0 + float(s.get("at", 0.0))
                    while first_t + k * s["every"] < t1 - 1e-9:
                        events.append((dict(base, t=first_t + k * s["every"]), a))
                        k += 1
                else:   # at: seconds after the start of the action's window
                    events.append((dict(base, t=t0 + float(s.get("at", 0.0))), a))
        for i, e in enumerate((self.scene.get("sound") or {}).get("events") or []):
            t = self.times(e["t"], f"sound.events[{i}]")
            events.append((dict({k: v for k, v in e.items() if k != "t"}, t=t), {"id": f"sound.events[{i}]",
                                                                                   "_where": f"sound.events[{i}]"}))
        out = []
        for ev, a in events:
            if ev["t"] is None or not 0 <= ev["t"] < self.duration:
                self.issues.add("timing", f"sound {ev['kind']} at {ev['t']}s is outside the scene "
                                f"(0-{self.duration:g}s, end excluded)", a.get("_where", a["id"]))
                continue
            out.append({k: (round(v, 4) if isinstance(v, float) else v) for k, v in ev.items()})
        self.events = sorted(out, key=lambda e: (e["t"], e["kind"]))

    # ------------------------------------------------------------- report
    def report(self) -> dict[str, Any]:
        checks = {}
        for name in CHECKS:
            found = self.issues.of(name)
            status = "fail" if found else ("pass" if name in self.ran else "not_checked")
            checks[name] = {"status": status, "issues": [i["message"] for i in found]}
            if name in self.measured:
                checks[name]["measured"] = self.measured[name]
            if status == "not_checked":
                checks[name]["reason"] = "earlier checks failed: the scene could not be compiled"
        status = "fail" if self.issues.items else ("pass" if all(c["status"] == "pass" for c in checks.values())
                                                   else "not_checked")
        return {"status": status, "scene": self.scene.get("id"), "duration": self.duration, "checks": checks,
                "errors": [f"{i['check']}: {i['message']} ({i['where']})" for i in self.issues.items]}

    def sfx_events(self) -> dict[str, Any]:
        snd = self.scene.get("sound") or {}
        return {"duration_seconds": round(self.duration, 4), "peak_dbfs": snd.get("peak_dbfs", -1.0),
                "seed": snd.get("seed", 11), "reverb_mix": snd.get("reverb_mix", 0.18), "events": self.events}

    def story_beats(self) -> dict[str, Any]:
        roles = {c["id"]: c.get("role", "") for c in self.scene.get("characters") or []}
        return {"brief": self.scene.get("brief", ""), "scene": self.scene.get("id"),
                "roles": {k: v for k, v in roles.items() if v},
                "beats": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in b.items() if k != "phrase"}
                          for b in self.beats]}

    def compiled_json(self, name: str) -> dict[str, Any]:
        c = self.layouts[name]
        sc = self.scene
        style = {"background": "#e8e0cf", "grain": True, "grain_seed": 7, "grain_alpha": 0.55, "vignette": True,
                 "fade_in": 0.45}
        style.update(sc.get("style") or {})
        props = {}
        for pid, p in c.props.items():
            params = dict(p["params"])
            props[pid] = {"type": p["type"], "params": params, "extra": c.extras.get(pid, {})}
        chars = {cid: {"look": ch["look"], "posture": ch["posture"], "scale": ch["scale"], "ground": ch["at"][1]}
                 for cid, ch in c.chars.items()}
        return _round({"layout": {k: c.layout[k] for k in ("name", "aspect", "width", "height")},
                       "duration": self.duration, "style": style, "camera": c.camera, "order": sc["order"],
                       "layers": sorted(c.layers, key=lambda x: x[0]), "props": props, "chars": chars,
                       "ch": c.ch.as_json()})


def _round(v: Any) -> Any:
    if isinstance(v, float):
        r = round(v, 4)
        return 0.0 if r == 0 else r
    if isinstance(v, list):
        return [_round(x) for x in v]
    if isinstance(v, tuple):
        return [_round(x) for x in v]
    if isinstance(v, dict):
        return {k: _round(x) for k, x in v.items()}
    return v


def validate(scene: dict[str, Any]) -> dict[str, Any]:
    return Build(scene).report()


# ------------------------------------------------------------------ files

def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def html(compiled: dict[str, Any], uses_font: bool) -> str:
    lay = compiled["layout"]
    W, H = lay["width"], lay["height"]
    bg = compiled["style"]["background"]
    res = "portrait" if H > W else "landscape"
    font = ('@font-face { font-family: "InkHand"; src: url("assets/patrickhand.ttf") format("truetype"); '
            'font-display: block; }\n      ') if uses_font else ""
    data = json.dumps(compiled, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
    d = compiled["duration"]
    return f"""<!doctype html>
<html lang="en" data-resolution="{res}">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <script src="gsap.min.js"></script>
    <script src="ink-theater.js"></script>
    <script src="paper-cut.js"></script>
    <script src="paper-scene.js"></script>
    <style>
      {font}* {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ width: {W}px; height: {H}px; overflow: hidden; background: {bg}; }}
      #root {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; }}
      .stage {{ position: absolute; inset: 0; background: {bg}; isolation: isolate; }}
      .stage svg {{ position: absolute; inset: 0; display: block; width: {W}px; height: {H}px; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{d:g}" data-width="{W}" data-height="{H}">
      <section id="scene" class="clip" data-start="0" data-duration="{d:g}" data-track-index="1">
        <div class="stage" id="stage">
          <svg id="world-svg" data-paper-cut viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
            <defs></defs>
            <g id="world"></g>
          </svg>
          <svg id="over-svg" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg"></svg>
        </div>
      </section>
    </div>
    <script>
      /* Compiled by the paper_scene tool (scene "{compiled_id(compiled)}", layout "{lay['name']}"). Do not edit:
         edit the scene description and compile again. */
      window.__PAPER_SCENE__ = {data};
      window.__timelines = window.__timelines || {{}};
      window.__timelines["main"] = PaperScene.mount(window.__PAPER_SCENE__);
    </script>
  </body>
</html>
"""


def compiled_id(compiled: dict[str, Any]) -> str:
    return str(compiled.get("scene", "")) or "scene"


def write_layout(out: Path, compiled: dict[str, Any], uses_font: bool) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    for name in ("ink-theater.js", "paper-cut.js", "paper-scene.js"):
        shutil.copyfile(INK / name, out / name)
    link = out / "gsap.min.js"
    if link.is_symlink() or link.exists():
        link.unlink()
    os.symlink(os.path.relpath(GSAP, out), link)
    if uses_font:
        (out / "assets").mkdir(exist_ok=True)
        shutil.copyfile(FONT, out / "assets" / "patrickhand.ttf")
        shutil.copyfile(INK / "assets" / "OFL.txt", out / "assets" / "OFL.txt")
    (out / "index.html").write_text(html(compiled, uses_font), encoding="utf-8")
    return out


def compile_scene(scene: dict[str, Any], out_dir: str | Path) -> dict[str, Any]:
    b = Build(scene)
    rep = b.report()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(_dump(rep), encoding="utf-8")
    if rep["status"] != "pass":
        return {"report": rep, "written": [str(out / "report.json")]}
    written = [str(out / "report.json")]
    uses_font = any(p["type"] == "label" or (p["type"] == "bars" and (p.get("params") or {}).get("labels"))
                    for p in scene["props"])
    for name in b.layouts:
        cj = b.compiled_json(name)
        cj["scene"] = scene.get("id")
        if scene.get("insert"):
            cj["insert"] = scene["insert"]
        d = write_layout(out / name, cj, uses_font)
        written.append(str(d / "index.html"))
    (out / "sfx_events.json").write_text(_dump(b.sfx_events()), encoding="utf-8")
    (out / "story_beats.json").write_text(_dump(b.story_beats()), encoding="utf-8")
    resolved = {"scene": scene.get("id"), "duration": b.duration, "marks": b.marks,
                "insert": scene.get("insert"),
                "actions": [{k: v for k, v in a.items() if not k.startswith("_") and k != "sound"}
                            for a in b.actions]}
    (out / "scene.resolved.json").write_text(_dump(_round(resolved)), encoding="utf-8")
    written += [str(out / n) for n in ("sfx_events.json", "story_beats.json", "scene.resolved.json")]
    return {"report": rep, "written": written, "layouts": list(b.layouts), "build": b}


def load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def scene_from(arg: Any) -> dict[str, Any]:
    if isinstance(arg, dict):
        return arg
    return load(arg)


