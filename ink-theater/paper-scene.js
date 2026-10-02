/*
 * Paper Scene — the player of a compiled paper scene (lib/paper_scene).
 *
 * Browser global `PaperScene`. Load AFTER ink-theater.js and paper-cut.js.
 * `PaperScene.mount(scene)` builds the set once and returns a paused GSAP
 * timeline driven by `PaperCut.drive`: every frame is a pure function of t.
 *
 * The compiler (Python) writes every motion as channels; this file evaluates
 * them with the SAME rules (lib/paper_scene/motion.py) and draws the library's
 * prop types. A test runs `PaperScene.Channels` on a compiled scene and
 * compares it with the Python values. No Math.random, no clock.
 */
(function (root) {
  "use strict";
  var IT = root.InkTheater, PC = root.PaperCut;
  if (!IT || !PC) throw new Error("paper-scene.js needs ink-theater.js and paper-cut.js loaded first");
  var el = IT.el, D2R = Math.PI / 180;

  // ---------------------------------------------------------------- evaluator
  var EASES = {
    io: function (x) { return x * x * (3 - 2 * x); },
    io5: function (x) { return x * x * x * (x * (6 * x - 15) + 10); },
    out: function (x) { return 1 - Math.pow(1 - x, 3); },
    "in": function (x) { return x * x * x; },
    lin: function (x) { return x; }
  };
  function clamp(x, a, b) { a = a == null ? 0 : a; b = b == null ? 1 : b; return Math.max(a, Math.min(b, x)); }
  function lerp(a, b, x) { return a + (b - a) * x; }
  function prog(t, t0, t1) { if (t1 <= t0) return t >= t1 ? 1 : 0; return clamp((t - t0) / (t1 - t0)); }
  function isColor(v) { return typeof v === "string" && v.length === 7 && v.charAt(0) === "#"; }
  function interp(v0, v1, x) {
    if (typeof v0 === "number" && typeof v1 === "number") return v0 + (v1 - v0) * x;
    if (isColor(v0) && isColor(v1)) return PC.mix(v0, v1, x);
    return x > 0.5 ? v1 : v0;
  }
  function upper(arr, t) { var lo = 0, hi = arr.length; while (lo < hi) { var m = (lo + hi) >> 1; if (t < arr[m]) hi = m; else lo = m + 1; } return lo; }

  function Channels(ch) {
    this.ch = ch; this.starts = {};
    for (var k in ch) this.starts[k] = ch[k].s.map(function (s) { return s[1]; });
  }
  Channels.prototype.value = function (key, t) {
    var c = this.ch[key];
    if (!c) throw new Error("paper-scene: no channel " + key);
    var s = c.s;
    if (!s.length || t < s[0][1]) return c.v;
    var i = upper(this.starts[key], t) - 1, seg = s[i];
    return this.segValue(seg, Math.min(t, seg[2]));
  };
  Channels.prototype.segValue = function (seg, t) {
    var k = seg[0], x, u;
    if (k === "e") { x = seg[2] <= seg[1] ? 1 : clamp((t - seg[1]) / (seg[2] - seg[1])); return interp(seg[3], seg[4], EASES[seg[5]](x)); }
    if (k === "o") return seg[3] + seg[4] * Math.sin(2 * Math.PI * seg[5] * (t - seg[1]));
    if (k === "d") { u = Math.max(0, t - seg[1]); return seg[3] + seg[4] * Math.exp(-seg[6] * u) * Math.sin(2 * Math.PI * seg[5] * u); }
    if (k === "f") return seg[4] * this.value(seg[3], t) + seg[5];
    if (k === "s") {
      var p = seg[3];
      if (t <= p[0][0]) return p[0][1];
      if (t >= p[p.length - 1][0]) return p[p.length - 1][1];
      var lo = 0, hi = p.length;
      while (lo < hi) { var m = (lo + hi) >> 1; if (t < p[m][0]) hi = m; else lo = m + 1; }
      var a = p[lo - 1], b = p[lo];
      return interp(a[1], b[1], b[0] > a[0] ? (t - a[0]) / (b[0] - a[0]) : 1);
    }
    throw new Error("paper-scene: unknown segment " + k);
  };

  function cameraAt(keys, t, W, H) {
    var c = keys[keys.length - 1].slice(1);
    if (t <= keys[0][0]) c = keys[0].slice(1);
    else if (t < keys[keys.length - 1][0]) {
      for (var i = 0; i < keys.length - 1; i++) if (keys[i][0] <= t && t <= keys[i + 1][0]) {
        var u = EASES[keys[i][4] || "io"](prog(t, keys[i][0], keys[i + 1][0]));
        c = [1, 2, 3].map(function (j) { return lerp(keys[i][j], keys[i + 1][j], u); }); break;
      }
    }
    var z = c[2], hw = W / (2 * z), hh = H / (2 * z);
    return [clamp(c[0], hw, W - hw), clamp(c[1], hh, H - hh), z];
  }

  // ------------------------------------------------------------------ helpers
  function f2(x) { return (Math.round(x * 100) / 100).toString(); }
  function T(x, y, rotDeg, s) { return "translate(" + f2(x) + "," + f2(y) + ")" + (rotDeg ? " rotate(" + f2(rotDeg) + ")" : "") + (s != null && s !== 1 ? " scale(" + (Math.round(s * 10000) / 10000) + ")" : ""); }
  function g(parent, attrs) { var n = el("g", attrs || {}); parent.appendChild(n); return n; }
  function line(parent, pts, color, w, op, extra) {
    var a = { d: "M" + pts.map(function (q) { return f2(q[0]) + " " + f2(q[1]); }).join("L"), fill: "none", stroke: color, "stroke-width": w, "stroke-opacity": op == null ? 1 : op, "stroke-linecap": "round", "stroke-linejoin": "round" };
    for (var k in (extra || {})) a[k] = extra[k];
    var p = el("path", a); parent.appendChild(p); return p;
  }
  function sparkle(parent) {
    var s = g(parent, { stroke: "rgb(255,247,204)", "stroke-linecap": "round", fill: "none" });
    var a = el("path", { "stroke-width": 2.2 }), b = el("path", { "stroke-width": 1.4 });
    s.appendChild(a); s.appendChild(b);
    return { g: s, set: function (x, y, size, alpha) {
      if (alpha <= 0) { s.style.display = "none"; return; }
      s.style.display = ""; s.setAttribute("stroke-opacity", f2(alpha));
      var k = size * 0.45;
      a.setAttribute("d", "M" + f2(x - size) + " " + f2(y) + "L" + f2(x + size) + " " + f2(y) + "M" + f2(x) + " " + f2(y - size) + "L" + f2(x) + " " + f2(y + size));
      b.setAttribute("d", "M" + f2(x - k) + " " + f2(y - k) + "L" + f2(x + k) + " " + f2(y + k) + "M" + f2(x - k) + " " + f2(y + k) + "L" + f2(x + k) + " " + f2(y - k));
    } };
  }
  var AMP = { rect: 1.0, poly: 1.5, circle: 1.2, ellipse: 1.2, gear: 0.8 };
  function shapePoints(key, p) {
    var amp = p.amp != null ? p.amp : AMP[p.shape];
    if (p.shape === "rect") return PC.tornRect(key, p.rect[0], p.rect[1], p.rect[2], p.rect[3], amp);
    if (p.shape === "circle") return PC.tornCircle(key, p.r, amp);
    if (p.shape === "ellipse") return PC.tornEllipse(key, p.rx, p.ry, amp);
    if (p.shape === "gear") {
      var n = p.teeth || 24, ri = p.r_in != null ? p.r_in : p.r * 0.77, pts = [];
      for (var i = 0; i < n; i++) { var a = i * 2 * Math.PI / n, r = (Math.floor(i / 2) % 2 === 0) ? p.r : ri; pts.push([r * Math.cos(a), r * Math.sin(a)]); }
      return PC.tornPoly(key, pts, amp, p.step || 6);
    }
    return PC.tornPoly(key, p.points, amp, p.step || 13);
  }
  // One paper piece from shape params; returns {g, fill} (fill may be null for outlines).
  function shapePiece(parent, key, p) {
    var holder = g(parent, p.at ? { transform: T(p.at[0], p.at[1]) } : {});
    var pts = shapePoints(key, p);
    var piece = PC.paper(holder, pts, p.color || "#d8694b", { depth: p.depth == null ? 1 : p.depth, alpha: p.alpha == null ? 1 : p.alpha, edge: p.edge !== false, shadow: p.shadow !== false });
    return { g: holder, piece: piece, x0: p.at ? p.at[0] : 0, y0: p.at ? p.at[1] : 0 };
  }

  // ------------------------------------------------------------------ drawers
  // Each returns {node, update(t, S)} ; S = {v(key), id, ...}.
  var DRAW = {};

  DRAW.shape = function (parent, id, P, S) {
    var root_ = g(parent), inner = g(root_), sp = shapePiece(inner, P.params.key || id, P.params);
    var x0 = S.v(id + ".x", 0);
    return function (t) {
      var x = S.v(id + ".x", t), rot = S.v(id + ".rot", t) + (P.params.spin || 0) * t, r = P.params.rolls;
      if (r) rot += (x - x0) / r;
      root_.setAttribute("transform", T(x, S.v(id + ".y", t), rot / D2R, S.v(id + ".scale", t)));
      root_.setAttribute("opacity", f2(S.v(id + ".opacity", t)));
      var c = S.v(id + ".color", t); sp.piece.fill.setAttribute("fill", c); if (P.params.edge !== false) sp.piece.fill.setAttribute("stroke", PC.shade(c, 0.35));
    };
  };

  DRAW.group = function (parent, id, P, S) {
    var root_ = g(parent), x0 = S.v(id + ".x", 0), kids = [];
    P.params.children.forEach(function (c, i) {
      var sp = shapePiece(root_, c.key || (id + "_" + i), c);
      kids.push([sp, c]);
    });
    return function (t) {
      var x = S.v(id + ".x", t);
      root_.setAttribute("transform", T(x, S.v(id + ".y", t), (S.v(id + ".rot", t) + (P.params.spin || 0) * t) / D2R, S.v(id + ".scale", t)));
      root_.setAttribute("opacity", f2(S.v(id + ".opacity", t)));
      kids.forEach(function (k) { if (k[1].rolls) k[0].g.setAttribute("transform", T(k[0].x0, k[0].y0, ((x - x0) / k[1].rolls) / D2R)); });
    };
  };

  DRAW.label = function (parent, id, P, S) {
    var root_ = g(parent), p = P.params;
    var tx = el("text", { x: 0, y: 0, "font-family": "InkHand, 'Patrick Hand', sans-serif", "font-size": p.size, fill: p.color, "text-anchor": "middle" });
    tx.textContent = p.text; root_.appendChild(tx);
    return function (t) {
      root_.setAttribute("transform", T(S.v(id + ".x", t), S.v(id + ".y", t), S.v(id + ".rot", t) / D2R, S.v(id + ".scale", t)));
      root_.setAttribute("opacity", f2(S.v(id + ".opacity", t)));
    };
  };

  DRAW.path = function (parent, id, P, S) {
    var root_ = g(parent), p = P.params, pts = p.points.slice();
    if (p.closed) pts.push(pts[0]);
    var fill = null;
    if (p.fill_color) { fill = el("path", { d: PC.polyD(p.points), fill: p.fill_color, "fill-opacity": 0 }); root_.appendChild(fill); }
    var stroke = line(root_, [[0, 0], [0, 0]], p.color, p.width, p.line_alpha, p.dashed ? { "stroke-dasharray": "14 10" } : {});
    var lens = [0];
    for (var i = 1; i < pts.length; i++) lens.push(lens[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    var L = lens[lens.length - 1];
    return function (t) {
      root_.setAttribute("transform", T(S.v(id + ".x", t), S.v(id + ".y", t), S.v(id + ".rot", t) / D2R, S.v(id + ".scale", t)));
      root_.setAttribute("opacity", f2(S.v(id + ".opacity", t)));
      var d = clamp(S.v(id + ".draw", t)) * L, out = [pts[0]];
      for (var j = 1; j < pts.length; j++) {
        if (lens[j] <= d) { out.push(pts[j]); continue; }
        var u = (d - lens[j - 1]) / ((lens[j] - lens[j - 1]) || 1);
        out.push([lerp(pts[j - 1][0], pts[j][0], u), lerp(pts[j - 1][1], pts[j][1], u)]); break;
      }
      stroke.setAttribute("d", "M" + out.map(function (q) { return f2(q[0]) + " " + f2(q[1]); }).join("L"));
      stroke.style.display = d > 0 ? "" : "none";
      if (fill) fill.setAttribute("fill-opacity", f2(clamp(S.v(id + ".fill", t)) * p.fill_alpha));
    };
  };

  DRAW.glow = function (parent, id, P, S) {
    var root_ = g(parent), c = PC.glow(root_, 0, 0, P.params.r, P.params.color, 0.5);
    return function (t) {
      root_.setAttribute("transform", T(S.v(id + ".x", t), S.v(id + ".y", t), 0, S.v(id + ".scale", t)));
      c.setAttribute("opacity", f2(S.v(id + ".alpha", t) * S.v(id + ".opacity", t)));
    };
  };

  DRAW.door = function (parent, id, P, S) {
    var p = P.params, r = p.rect, root_ = g(parent);
    if (p.inside_color) PC.paper(root_, PC.tornRect(id + "in", r[0], r[1], r[2], r[3], 1.0), p.inside_color, { shadow: false });
    var clip = el("clipPath", { id: "clip-" + id }), cr = el("rect", { x: r[0], y: r[1], width: r[2], height: r[3] });
    clip.appendChild(cr); S.defs.appendChild(clip);
    var panel = g(g(root_, { "clip-path": "url(#clip-" + id + ")" }));
    PC.paper(panel, PC.tornRect(id + "panel", r[0] + 2, r[1], r[2] - 4, r[3], 1.0), p.color, { depth: 0.6 });
    var n = p.slats, step = r[3] / (n + 0.5);
    for (var k = 0; k < n; k++) line(panel, [[r[0] + 4, r[1] + step * (k + 0.7)], [r[0] + r[2] - 4, r[1] + step * (k + 0.7)]], p.slat_color, 3);
    if (p.box_color) PC.paper(root_, PC.tornRect(id + "box", r[0] - 4, r[1] - 12, r[2] + 8, 18, 1), p.box_color, { depth: 0.8 });
    return function (t) {
      root_.setAttribute("transform", T(S.v(id + ".x", t), S.v(id + ".y", t)));
      var open = clamp(S.v(id + ".open", t)), h = (1 - open) * r[3];
      cr.setAttribute("height", f2(Math.max(0, h)));
      panel.setAttribute("transform", T(0, -open * r[3]));
      panel.style.display = h > 2 ? "" : "none";
    };
  };

  var JAR_STARS = [[-12, -14, 7, 0.2], [8, -22, 8, 1.1], [-3, -36, 6, 2.0], [12, -44, 6, 0.7], [-13, -46, 7, 1.6], [2, -10, 6, 2.6]];
  // a coin is 0.36 x 0.2 of the jar width (9 x 5 radii for the default 50): min_size measures the same
  function coinPiece(parent, key, w) { return PC.paper(parent, PC.tornEllipse(key, 0.18 * w, 0.1 * w, 0.4), "#e6b43c", { depth: 0.4, edge: true }); }
  DRAW.container = function (parent, id, P, S) {
    var p = P.params, w = p.w, h = p.h, root_ = g(parent), body = g(root_), items = [], glowEl = null;
    if (p.glow) glowEl = PC.glow(body, 0, -h / 2, h * 1.15, "#fff3b0", 0.3);
    var sx = w / 50, sy = h / 60;
    for (var i = 0; i < p.capacity; i++) {
      var ig = g(body);
      if (p.item === "coin") { coinPiece(ig, id + "c" + i, w); items.push([ig, [0, -6 - i * 7 * Math.min(1, 50 / (p.capacity * 7 + 1) * 1.0)], 0]); }
      else { var s = JAR_STARS[i % JAR_STARS.length]; PC.paper(ig, PC.starPoly(id + "s" + i, s[2]), p.item_color || "#fff0a8", { depth: 0.2, edge: false }); items.push([ig, [s[0] * sx, s[1] * sy], s[3]]); }
    }
    if (p.item === "coin") items.forEach(function (it, i) { var row = Math.floor(i / 3), col = i % 3; it[1] = [(col - 1) * w * 0.28 + (row % 2 ? w * 0.08 : 0), -7 - row * 8]; });
    var jb = [[-w * 0.48, -h], [w * 0.48, -h], [w * 0.52, -h * 0.13], [w * 0.4, 0], [-w * 0.4, 0], [-w * 0.52, -h * 0.13]];
    PC.paper(body, PC.tornPoly(id + "body", jb, 0.8), p.glass, { depth: 0.4, alpha: 0.42 });
    body.appendChild(el("path", { d: PC.polyD(PC.tornPoly(id + "body", jb, 0.8)), fill: "none", stroke: "rgb(64,102,128)", "stroke-opacity": 0.55, "stroke-width": 2.5 }));
    PC.paper(body, PC.tornRect(id + "neck", -w * 0.4, -h - 6, w * 0.8, 8, 0.6), "#b8d4e2", { depth: 0.2, alpha: 0.7 });
    line(body, [[-w * 0.3, -h * 0.83], [-w * 0.34, -h * 0.27]], "#ffffff", 4, 0.55);
    var lidOn = null, lidFree = null;
    if (p.lid) {
      lidOn = g(body, { transform: T(0, -h) }); PC.paper(lidOn, PC.tornRect(id + "lid", -w * 0.54, -12, w * 1.08, 14, 0.8), p.lid, { depth: 0.6 });
      lidFree = g(parent); PC.paper(lidFree, PC.tornRect(id + "lidf", -w * 0.54, -7, w * 1.08, 14, 0.8), p.lid, { depth: 0.6 });
    }
    var fx = g(parent), drops = (P.extra.drops || []).map(function (d, i) { var cg = g(fx); coinPiece(cg, id + "d" + i, w); return [cg, d]; });
    var spills = [];
    for (var k = 0; k < 6; k++) { var spg = g(fx); coinPiece(spg, id + "sp" + k, w); spills.push(spg); }
    return function (t) {
      var x = S.v(id + ".x", t), y = S.v(id + ".y", t), rot = S.v(id + ".rot", t);
      root_.setAttribute("transform", T(x, y, rot / D2R, S.v(id + ".scale", t)));
      root_.setAttribute("opacity", f2(S.v(id + ".opacity", t)));
      var n = Math.floor(S.v(id + ".count", t) + 1e-6), empty = clamp(S.v(id + ".empty", t));
      if (glowEl) glowEl.setAttribute("opacity", f2(0.35 * (1 - empty) * (0.75 + 0.25 * Math.sin(t * 6))));
      items.forEach(function (it, i) {
        it[0].style.display = (i < n && empty < 1) ? "" : "none";
        it[0].setAttribute("opacity", f2(1 - empty));
        it[0].setAttribute("transform", T(it[1][0], it[1][1], p.item === "coin" ? 0 : (it[2] + t * 0.5) / D2R));
      });
      if (lidOn) {
        var free = S.v(id + ".lid_free", t) >= 0.5;
        lidOn.style.display = free ? "none" : ""; lidFree.style.display = free ? "" : "none";
        if (free) lidFree.setAttribute("transform", T(S.v(id + ".lid_x", t), S.v(id + ".lid_y", t), S.v(id + ".lid_rot", t) / D2R));
      }
      drops.forEach(function (d) {
        var a = d[1], on = t >= a[0] && t < a[1];
        d[0].style.display = on ? "" : "none";
        if (on) { var u = EASES["in"](prog(t, a[0], a[1])); d[0].setAttribute("transform", T(lerp(a[2], a[4], u), lerp(a[3], a[5], u))); }
      });
      var sp = S.v(id + ".spill", t), mx = x + (-h) * Math.sin(-rot), my = y - h * Math.cos(rot);
      spills.forEach(function (s, k) {
        var u = clamp(sp - k);
        s.style.display = (u > 0 && u < 1) || (u >= 1 && k < sp) ? "" : "none";
        var side = k % 2 ? 1 : -1, ex = mx + side * (w * 0.75 + 6 * k), ey = y - 4;
        s.setAttribute("transform", T(lerp(mx + side * w * 0.3, ex, u), lerp(my - 6, ey, EASES["in"](u)), side * 40 * u));
      });
    };
  };

  // A page being flipped: hinged on the edge AWAY from the one who flips (side = +1 when he
  // stands right of the pile), its free edge rises on HIS side and never crosses to a third
  // party's side (U2: a page pointing at the courier made him read as the one flipping).
  function flipPage(w, sheet, top, u, side) {
    var th = 50 * Math.PI / 180 * Math.sin(Math.PI * clamp(u)), hx = -side * w / 2;
    var ex = hx + side * w * Math.cos(th), ey = top - w * Math.sin(th);
    return { hinge: [hx, top], edge: [ex, ey],
      pts: [[hx, top], [ex, ey], [ex - side * 2, ey - sheet * 0.6], [hx, top - sheet * 0.6]] };
  }

  DRAW.stack = function (parent, id, P, S) {
    var p = P.params, root_ = g(parent), sheets = [], r = PC.rngFor(id + "stack");
    for (var i = 0; i < p.max; i++) {
      var sg = g(root_, { transform: T((r() - 0.5) * 6, -(i + 1) * p.sheet, (r() - 0.5) * 3) });
      PC.paper(sg, PC.tornRect(id + "sh" + i, -p.w / 2, 0, p.w, p.sheet + 1, 0.4), p.color, { depth: 0.3 });
      line(sg, [[-p.w / 2 + 6, p.sheet * 0.5], [p.w / 2 - 6, p.sheet * 0.5]], p.line, 1, 0.6);
      sheets.push(sg);
    }
    var page = g(root_), pageP = PC.paper(page, [[0, 0], [1, 0], [1, 1], [0, 1]], p.color, { depth: 0.5 });
    var fly = g(parent), flights = (P.extra.flights || []).map(function (f, i) {
      var fg = g(fly); PC.paper(fg, PC.tornRect(id + "fl" + i, -p.w / 2, -p.sheet, p.w, p.sheet + 1, 0.4), p.color, { depth: 0.6 }); return [fg, f];
    });
    return function (t) {
      root_.setAttribute("transform", T(S.v(id + ".x", t), S.v(id + ".y", t), S.v(id + ".rot", t) / D2R, S.v(id + ".scale", t)));
      root_.setAttribute("opacity", f2(S.v(id + ".opacity", t)));
      var n = Math.min(p.max, Math.floor(S.v(id + ".count", t) + 1e-6));
      sheets.forEach(function (s, i) { s.style.display = i < n ? "" : "none"; });
      var fl = S.v(id + ".flipped", t), u = fl - Math.floor(fl), top = -n * p.sheet, side = 1;
      (P.extra.flip_side || []).forEach(function (f) { if (t >= f[0] && t <= f[1]) side = f[2]; });
      if (n > 0 && fl > 0 && u > 0.02 && u < 0.98) {
        PC.reshape(pageP, PC.polyD(flipPage(p.w, p.sheet, top, u, side).pts)); page.style.display = "";
      } else page.style.display = "none";
      flights.forEach(function (f) {
        var pts = f[1], on = t >= pts[0][0] && t < pts[pts.length - 1][0];
        f[0].style.display = on ? "" : "none";
        if (!on) return;
        var j = 1; while (j < pts.length - 1 && pts[j][0] <= t) j++;
        var a = pts[j - 1], b = pts[j], q = b[0] > a[0] ? (t - a[0]) / (b[0] - a[0]) : 1;
        f[0].setAttribute("transform", T(lerp(a[1], b[1], q), lerp(a[2], b[2], q), Math.sin(t * 9) * 4));
      });
    };
  };

  DRAW.bars = function (parent, id, P, S) {
    var p = P.params, root_ = g(parent), bars = [];
    for (var i = 1; i <= p.n; i++) {
      var bx = (i - 1) * (p.width + p.gap), bg = g(root_), piece = null, outline = null;
      if (p.dashed) {
        outline = el("rect", { x: bx, y: 0, width: p.width, height: 0, fill: p.color, "fill-opacity": p.fill_alpha, stroke: PC.shade(p.color, -0.35), "stroke-width": 3, "stroke-dasharray": "10 7" });
        bg.appendChild(outline);
      } else piece = PC.paper(bg, PC.tornRect(id + "b" + i, bx, -1, p.width, 1, 0.8), p.color, { depth: 0.8 });
      var pin = el("circle", { cx: bx + p.width / 2, cy: 0, r: 5, fill: "#c4553b" }); if (!p.dashed) bg.appendChild(pin);
      var lab = null;
      if (p.labels) { lab = el("text", { x: bx + p.width / 2, y: p.label_size * 1.15, "font-family": "InkHand, 'Patrick Hand', sans-serif", "font-size": p.label_size, fill: p.label_color, "text-anchor": "middle" }); lab.textContent = p.labels[i - 1]; bg.appendChild(lab); }
      bars.push({ g: bg, x: bx, piece: piece, outline: outline, pin: pin, lab: lab, last: null });
    }
    return function (t) {
      root_.setAttribute("transform", T(S.v(id + ".x", t), S.v(id + ".y", t), S.v(id + ".rot", t) / D2R, S.v(id + ".scale", t)));
      root_.setAttribute("opacity", f2(S.v(id + ".opacity", t)));
      bars.forEach(function (b, k) {
        var i = k + 1, hh = Math.max(0, S.v(id + ".h_" + i, t)) * p.unit, rv = clamp(S.v(id + ".reveal_" + i, t));
        b.g.style.display = (hh > 0.5 && rv > 0) ? "" : "none";
        b.g.setAttribute("opacity", f2(rv));
        var key = Math.round(hh * 10) / 10;
        if (b.piece && key !== b.last) { PC.reshape(b.piece, PC.tornRect(id + "b" + i, b.x, -key, p.width, key, 0.8)); }
        if (b.outline) { b.outline.setAttribute("y", f2(-hh)); b.outline.setAttribute("height", f2(hh)); }
        b.last = key;
        b.pin.setAttribute("cy", f2(-hh + 7));
      });
    };
  };

  DRAW.bands = function (parent, id, P, S) {
    var p = P.params, W = S.W, n = p.colors.length, bands = [];
    function rect(i) { var y1 = p.top0 - p.band_h * i; return [y1 - p.band_h - p.overlap, y1 + p.overlap]; }
    p.colors.forEach(function (c, i) {
      var yr = rect(i), y0 = i === n - 1 ? -20 : yr[0], y1 = yr[1];
      var clip = el("clipPath", { id: "clip-" + id + i }), cr = el("rect", { x: -30, y: y0 - 30, width: 0, height: y1 - y0 + 60 });
      clip.appendChild(cr); S.defs.appendChild(clip);
      var bg = g(parent, { "clip-path": "url(#clip-" + id + i + ")" });
      PC.paper(bg, PC.tornPoly(id + "band" + i, [[-20, y0], [W + 20, y0], [W + 20, y1], [-20, y1]], 5.0, 22), c, { depth: 1.2 });
      var r = PC.rngFor(id + "strokes" + i);
      for (var j = 0; j < p.strokes; j++) {
        var yy = y0 + 20 + r() * (y1 - y0 - 40);
        bg.appendChild(el("path", { d: "M-10 " + f2(yy) + "C300 " + f2(yy + r() * 12 - 6) + " " + f2(W * 0.65) + " " + f2(yy + r() * 12 - 6) + " " + (W + 10) + " " + f2(yy), fill: "none", stroke: PC.shade(c, r() < 0.5 ? -0.06 : 0.08), "stroke-opacity": 0.55, "stroke-width": f2(6 + r() * 10) }));
      }
      bands.push(cr);
    });
    return function (t) {
      bands.forEach(function (cr, i) {
        var reach = clamp(S.v(id + ".p_" + (i + 1), t)) * (W + 60);
        cr.setAttribute("width", f2(reach));
        cr.setAttribute("x", f2(i % 2 === 0 ? -30 : W + 30 - reach));
      });
    };
  };

  DRAW.pole = function (parent, id, P, S) {
    var p = P.params, root_ = g(parent), segs = [], COLS = ["#8a5a3b", "#a7adb5", "#c7ccd2", "#d9dde2"], WD = [9, 7, 6, 5];
    for (var k = 0; k < 4; k++) segs.push([line(root_, [[0, 0], [0, 1]], "rgb(41,20,10)", WD[k] + 2, 0.2), line(root_, [[0, 0], [0, 1]], COLS[k], WD[k])]);
    var head = g(root_);
    PC.paper(head, PC.tornRect(id + "rollf", -6, -112, 12, 224, 0.8), "#6b6f7a", { depth: 0.6 });
    var paperHead = PC.paper(head, PC.tornRect(id + "roll", -24, -105, 48, 210, 1.4), p.head_color, { depth: 1.2 });
    head.appendChild(el("rect", { x: -16, y: -100, width: 6, height: 200, fill: "#ffffff", "fill-opacity": 0.25 }));
    var bandsP = p.bands ? S.props[p.bands] : null;
    return function (t) {
      var bx = S.v(id + ".base_x", t), by = S.v(id + ".base_y", t), tx = bx + S.v(id + ".tip_dx", t), ty = by + S.v(id + ".tip_dy", t);
      var L = Math.hypot(tx - bx, ty - by) || 1, ux = (tx - bx) / L, uy = (ty - by) / L;
      segs.forEach(function (s, k) {
        var sx = bx + ux * L / 4 * k, sy = by + uy * L / 4 * k, ex = bx + ux * L / 4 * (k + 1), ey = by + uy * L / 4 * (k + 1);
        s[0].setAttribute("d", "M" + f2(sx + 2) + " " + f2(sy + 4) + "L" + f2(ex + 2) + " " + f2(ey + 4));
        s[1].setAttribute("d", "M" + f2(sx) + " " + f2(sy) + "L" + f2(ex) + " " + f2(ey));
      });
      var col = p.head_color;
      if (bandsP) bandsP.params.colors.forEach(function (c, i) { var v = S.v(p.bands + ".p_" + (i + 1), t); if (v > 0 && v < 1) col = c; });
      paperHead.fill.setAttribute("fill", col); paperHead.fill.setAttribute("stroke", PC.shade(col, 0.35));
      head.setAttribute("transform", T(tx, ty, 0, 0.42 + 0.58 * clamp((L - 230) / 300)));
    };
  };

  DRAW.sun = function (parent, id, P, S) {
    var R = P.params.r, root_ = g(parent), glowE = PC.glow(root_, 0, 0, R * 2.3, "#ffd24a", 0.3), body = g(root_), parts = [], k;
    for (k = 0; k < 12; k++) parts.push([PC.paper(g(body, { transform: "rotate(" + (k * 30) + ")" }), PC.tornPoly(id + "ray" + k, [[-13, -R + 6], [13, -R + 6], [0, -R - 30]], 0.8), "#ffb733", { depth: 0.8 }), "#ffb733"]);
    parts.push([PC.paper(body, PC.tornCircle(id + "c1", R, 1.8), "#ffcf3a", { depth: 1.4 }), "#ffcf3a"]);
    parts.push([PC.paper(body, PC.tornCircle(id + "c2", R * 0.74, 1.6), "#ffe27a", { depth: 0.4 }), "#ffe27a"]);
    parts.push([PC.paper(body, PC.tornCircle(id + "c3", R * 0.42, 1.2), "#fff1b0", { depth: 0.3 }), "#fff1b0"]);
    var sp = []; for (k = 0; k < 40; k++) sp.push([(12 + k * 1.55 * R / 95) * Math.cos(k * 0.32), (12 + k * 1.55 * R / 95) * Math.sin(k * 0.32)]);
    var spiral = line(body, sp, "#f2a52a", 5, 0.55), x0 = S.v(id + ".x", 0);
    return function (t) {
      var x = S.v(id + ".x", t), y = S.v(id + ".y", t), power = S.v(id + ".power", t), dim = S.v(id + ".dim", t);
      var rot = S.v(id + ".rot", t) + (P.params.rolls ? (x - x0) / R : 0);
      root_.setAttribute("transform", T(x, y, 0, S.v(id + ".scale", t)));
      root_.setAttribute("opacity", f2(S.v(id + ".opacity", t)));
      body.setAttribute("transform", "rotate(" + f2(rot / D2R) + ")");
      glowE.setAttribute("r", f2(R * (2.3 + 0.9 * power)));
      glowE.setAttribute("opacity", f2(0.42 * power * lerp(1, 0.55, dim)));
      parts.forEach(function (q) { q[0].fill.setAttribute("fill", dim > 0 ? PC.mix(q[1], "#e08a5a", dim * 0.55) : q[1]); });
      spiral.setAttribute("stroke", dim > 0 ? PC.mix("#f2a52a", "#e08a5a", dim * 0.55) : "#f2a52a");
    };
  };

  DRAW.conveyor = function (parent, id, P, S) {
    var p = P.params, root_ = g(parent), y = S.v(id + ".y", 0), k;
    p.legs.forEach(function (lx) { PC.paper(root_, PC.tornRect(id + "leg" + lx, lx - 7, 12, 14, p.floor - y - 8, 0.8), "#6b6f7a", { depth: 0.8 }); });
    PC.paper(root_, PC.tornRect(id + "belt", p.x, 0, p.w, 22, 1.2), "#3d3a42", { depth: 1.2 });
    var dashes = [], rollers = [], nd = Math.round(p.w / 18.4), nr = Math.round(p.w / 40);
    for (k = 0; k < nd; k++) dashes.push(line(root_, [[0, 3], [-4, 9]], "#bfb8ad", 2, 0.55));
    for (k = 0; k < nr; k++) { var rg = g(root_); PC.paper(rg, PC.tornCircle(id + "rol" + k, 8, 0.5), "#9ba0a8", { depth: 0.3 }); line(rg, [[-6, 0], [6, 0]], "#40404d", 2, 0.8); rollers.push(rg); }
    return function (t) {
      root_.setAttribute("transform", T(0, S.v(id + ".y", t)));
      var off = S.v(id + ".offset", t), span = p.w - 4;
      dashes.forEach(function (d, k) { var dx = p.x + 3 + ((((k * 22 + off) % span) + span) % span); d.setAttribute("transform", "translate(" + f2(dx) + ",0)"); });
      rollers.forEach(function (r, k) { r.setAttribute("transform", T(p.x + 16 + k * 40, 11, off / 9 / D2R)); });
    };
  };

  DRAW.crane = function (parent, id, P, S) {
    var p = P.params, c = p.color, root_ = g(parent), J = p.jib_y, mx = p.mast_x, k;
    PC.paper(root_, PC.tornRect(id + "mast", mx - 23, J - 10, 46, p.floor - J + 6, 1.0), c, { depth: 1.2 });
    var zz = []; for (k = 0; k < Math.floor((p.floor - J) / 46); k++) { var yk = J + 6 + k * 46; zz.push([mx - 18, yk], [mx + 18, yk + 23]); } zz.push([mx - 18, J + 6 + k * 46]);
    line(root_, zz, PC.shade(c, -0.35), 3);
    PC.paper(root_, PC.tornRect(id + "base", mx - 48, p.floor - 26, 96, 30, 1), "#5d5f68", { depth: 1.0 });
    PC.paper(root_, PC.tornPoly(id + "peak", [[mx - 16, J - 8], [mx + 16, J - 8], [mx, J - 92]], 1), c, { depth: 1.0 });
    [p.jib_x0 + 12, p.jib_x1 - 28].forEach(function (cx) { line(root_, [[mx, J - 90], [cx, J + 2]], "#5a4a3a", 2.4); });
    PC.paper(root_, PC.tornRect(id + "jib", p.jib_x0, J, p.jib_x1 - p.jib_x0, 28, 1.2), c, { depth: 1.2 });
    var jz = [], nj = Math.floor((p.jib_x1 - p.jib_x0 - 12) / 29); for (k = 0; k < nj; k++) { var jx = p.jib_x0 + 6 + k * 29; jz.push([jx, J + 4], [jx + 14, J + 24]); } jz.push([p.jib_x0 + 6 + nj * 29, J + 4]);
    line(root_, jz, PC.shade(c, -0.35), 2.6);
    PC.paper(root_, PC.tornRect(id + "cab", mx + 24, J + 22, 66, 54, 1.0), "#e2553f", { depth: 1.0 });
    PC.paper(root_, PC.tornRect(id + "cabw", mx + 36, J + 32, 32, 24, 0.6), "#cfe0dc", { depth: 0.2 });
    PC.paper(root_, PC.tornRect(id + "cw", mx + 94, J - 6, 70, 46, 1.0), "#6b6f7a", { depth: 1.0 });
    var trolley = g(root_); PC.paper(trolley, PC.tornRect(id + "trolley", -22, J + 24, 44, 18, 0.8), "#5d5f68", { depth: 0.8 });
    var cables = [line(root_, [[0, 0], [0, 1]], "#3b3437", 2.6), line(root_, [[0, 0], [0, 1]], "#3b3437", 2.6)];
    var hook = g(root_); PC.paper(hook, PC.tornRect(id + "block", -15, -26, 30, 22, 0.6), "#e2553f", { depth: 0.8 });
    var arc = el("path", { fill: "none", stroke: "#4a4a52", "stroke-width": 7, "stroke-linecap": "round" }); hook.appendChild(arc);
    return function (t) {
      var tx = S.v(id + ".trolley", t), hy = S.v(id + ".hook_y", t), open = clamp(S.v(id + ".hook_open", t));
      trolley.setAttribute("transform", T(tx, 0));
      cables[0].setAttribute("d", "M" + f2(tx - 5) + " " + (J + 40) + "L" + f2(tx - 3) + " " + f2(hy - 22));
      cables[1].setAttribute("d", "M" + f2(tx + 5) + " " + (J + 40) + "L" + f2(tx + 3) + " " + f2(hy - 22));
      hook.setAttribute("transform", T(tx, hy));
      var ha = (30 + 50 * open) * D2R, ae = Math.PI - ha;
      arc.setAttribute("d", "M0 -4A12 12 0 " + (ae + Math.PI / 2 > Math.PI ? 1 : 0) + " 1 " + f2(12 * Math.cos(ae)) + " " + f2(8 + 12 * Math.sin(ae)));
    };
  };

  DRAW.burst = function (parent, id, P, S) {
    var ex = P.extra, root_ = g(parent);
    if (!ex.particles) return function () { root_.style.display = "none"; };
    var o = ex.origin, start = ex.start, glowE = PC.glow(root_, o[0], o[1], 160, "#fff3b0", 0);
    var sr = IT.rng(PC.hashKey(id + "sparks")), sparks = [];
    for (var k = 0; k < (P.params.sparks || 0); k++) sparks.push({ s: sparkle(root_), a: -Math.PI * (0.05 + 0.9 * sr()), v: 140 + 280 * sr(), life: 0.5 + 0.4 * sr(), size: 5 + 6 * sr() });
    var parts = ex.particles.map(function (q, i) {
      var trails = [sparkle(root_), sparkle(root_), sparkle(root_)], flash = sparkle(root_);
      var sg = g(root_), gl = PC.glow(sg, 0, 0, q.r * 3.2, "#fff3b0", 0.45), body = g(sg);
      PC.paper(body, PC.starPoly(id + "st" + i, q.r), "#fff2b3", { depth: 0.4 });
      return { q: q, trails: trails, flash: flash, g: sg, glow: gl, body: body };
    });
    function pos(q, t) {
      var p = q.path, n = p.length;
      if (t <= p[0][0]) return [p[0][1], p[0][2]];
      if (t >= p[n - 1][0]) return [p[n - 1][1], p[n - 1][2]];
      var lo = 0, hi = n; while (lo < hi) { var m = (lo + hi) >> 1; if (t < p[m][0]) hi = m; else lo = m + 1; }
      var a = p[lo - 1], b = p[lo], u = (t - a[0]) / ((b[0] - a[0]) || 1);
      return [lerp(a[1], b[1], u), lerp(a[2], b[2], u)];
    }
    return function (t) {
      var on = t >= start, b = t - start;
      root_.style.display = on ? "" : "none";
      if (!on) return;
      glowE.setAttribute("opacity", f2(0.6 * Math.max(0, 1 - b / 0.6)));
      sparks.forEach(function (s) { if (b < s.life) { var d = s.v * EASES.out(b / s.life); s.s.set(o[0] + d * Math.cos(s.a), o[1] + d * Math.sin(s.a), s.size, 1 - b / s.life); } else s.s.set(0, 0, 0, 0); });
      parts.forEach(function (P_) {
        var q = P_.q;
        if (t < q.t0) { P_.g.style.display = "none"; P_.trails.forEach(function (s) { s.set(0, 0, 0, 0); }); P_.flash.set(0, 0, 0, 0); return; }
        P_.g.style.display = "";
        var xy = pos(q, t), u = prog(t, q.t0, q.land), rot, tw;
        P_.trails.forEach(function (s, j) {
          var tj = t - 0.035 * (j + 1);
          if (u < 1 && tj > q.t0 && (t < q.t0 + 0.5 || t > q.ts)) { var pj = pos(q, tj); s.set(pj[0], pj[1], q.r * 0.5, 0.5 * (1 - (j + 1) / 4) * (1 - u)); } else s.set(0, 0, 0, 0);
        });
        if (u < 1) { rot = q.spin * (t - q.t0); tw = 1; P_.flash.set(0, 0, 0, 0); }
        else {
          rot = q.spin * (q.land - q.t0) + 0.1 * Math.sin(t * 1.3 + q.ph);
          tw = 1 + 0.25 * PC.damped(t - q.land, 2.5, 5) + 0.18 * Math.sin(t * q.w + q.ph);
          if (((t * 0.9 + q.ph) % 2.3) < 0.12) P_.flash.set(xy[0], xy[1], q.r * 1.6, 0.8); else P_.flash.set(0, 0, 0, 0);
        }
        P_.g.setAttribute("transform", T(xy[0], xy[1]));
        P_.glow.setAttribute("opacity", f2(0.45 * tw));
        P_.body.setAttribute("transform", "rotate(" + f2(rot / D2R) + ") scale(" + (Math.round(Math.pow(tw, 0.3) * 1000) / 1000) + ")");
      });
    };
  };

  DRAW.curtain = function (parent, id, P, S) {
    var W = S.W, p = P.params, gid = "grad-" + id;
    var gr = el("linearGradient", { id: gid, gradientUnits: "userSpaceOnUse", x1: 0, y1: 0, x2: 0, y2: 1 });
    gr.appendChild(el("stop", { offset: 0, "stop-color": p.top })); gr.appendChild(el("stop", { offset: 1, "stop-color": p.bottom }));
    S.defs.appendChild(gr);
    var root_ = g(parent), shadow = el("path", { fill: "rgb(20,13,31)", "fill-opacity": 0.18, transform: "translate(3,7)" }), body = el("path", { fill: "url(#" + gid + ")" });
    root_.appendChild(shadow); root_.appendChild(body);
    var r = PC.rngFor(id + "edge"), jit = []; for (var k = 0; k <= 28; k++) jit.push(r() * 10 - 5);
    return function (t) {
      var cy = S.v(id + ".y", t);
      root_.style.display = cy <= -40 ? "none" : "";
      if (cy <= -40) return;
      var pts = [[-20, -20], [W + 20, -20]];
      for (var k = 28; k >= 0; k--) pts.push([W + 20 - (W + 40) * (28 - k) / 28, cy + 14 * Math.sin(k * 1.3) + jit[k]]);
      var d = PC.polyD(pts); shadow.setAttribute("d", d); body.setAttribute("d", d);
      gr.setAttribute("y2", f2(Math.max(cy, 1)));
    };
  };

  DRAW.tint = function (parent, id, P, S) {
    var p = P.params, root_ = g(parent), W = S.W, H = S.H;
    var rect = el("rect", { x: -60, y: -60, width: W + 120, height: H + 120, fill: p.color, "fill-opacity": 0 });
    rect.style.mixBlendMode = "multiply"; root_.appendChild(rect);
    var glows = p.glows.map(function (q) { return [PC.glow(root_, q[0], q[1], q[2], q[3], 0), q[4]]; });
    return function (t) {
      var n = clamp(S.v(id + ".night", t));
      rect.setAttribute("fill-opacity", f2(p.alpha * n));
      root_.style.display = n > 0 ? "" : "none";
      glows.forEach(function (q) { q[0].setAttribute("opacity", f2(q[1] * n)); });
    };
  };

  DRAW.smoke = function (parent, id, P, S) {
    var p = P.params, root_ = g(parent), puffs = [], COUNT = 4, RATE = 0.32, DRIFT = 60;   // the factory's chimney
    for (var k = 0; k < COUNT; k++) { var pg = g(root_); puffs.push({ g: pg, piece: PC.paper(pg, PC.tornCircle(id + "puff" + k, p.r0, 1.6), p.color, { depth: 0.5 }) }); }
    return function (t) {
      var x = S.v(id + ".x", t), y = S.v(id + ".y", t);
      puffs.forEach(function (q, k) {
        var ph = ((t * RATE + k / COUNT) % 1 + 1) % 1, rr = Math.round((p.r0 + ph * (p.r1 - p.r0)) * 10) / 10;
        q.g.setAttribute("transform", T(x + ph * DRIFT + 10 * Math.sin(ph * 6 + k), y - ph * p.rise));
        PC.reshape(q.piece, PC.tornCircle(id + "puff" + k, rr, 1.6));
        q.g.setAttribute("opacity", f2(Math.sin(ph * Math.PI) * 0.85 * S.v(id + ".opacity", t)));
      });
    };
  };

  DRAW.scatter = function (parent, id, P, S) {
    var p = P.params, a = p.area, root_ = g(parent), r = IT.rng(PC.hashKey(id + "scatter" + p.seed));
    var cols = p.colors || (p.item === "flower" ? ["#f6e7c8", "#f3b0a4", "#f7d06a"] : ["#a84a35"]);
    for (var k = 0; k < p.count; k++) {
      var x = a[0] + r() * (a[2] - a[0]), y = a[1] + r() * (a[3] - a[1]), col = cols[Math.floor(r() * cols.length) % cols.length];
      if (p.item === "flower") {
        var fl = g(root_, { transform: T(x, y, 0, p.size) });
        for (var j = 0; j < 5; j++) PC.paper(g(fl, { transform: "rotate(" + (j * 72) + ")" }), PC.tornEllipse(id + "pet" + k, 5, 9), col, { depth: 0.3, edge: false });
        PC.paper(fl, PC.tornCircle(id + "fc" + k, 4), "#e0873a", { depth: 0.2, edge: false });
      } else root_.appendChild(el("rect", { x: f2(x), y: f2(y), width: 26 * p.size, height: 11 * p.size, fill: "none", stroke: col, "stroke-opacity": 0.55, "stroke-width": 2 }));
    }
    return function (t) { root_.setAttribute("opacity", f2(S.v(id + ".opacity", t))); };
  };

  // ---------------------------------------------------------------- characters
  function blink(t, off) { var ph = (t + off) % 3.4; return ph < 0.14 ? 1 - Math.abs(ph - 0.07) / 0.07 : 0; }
  function character(parent, id, C, S, index) {
    var look = {}; for (var k in C.look) look[k] = C.look[k];
    look.name = id;
    var seated = C.posture === "seated", stool = null;
    // seated: a paper stool under the pelvis (drawn behind him), thighs towards where he first faces
    var facing = S.v(id + ".turn", 0) < 0 ? -1 : 1;
    if (seated) {
      stool = g(parent);
      // the seat sticks out on his BACK side (the thighs and what he faces hide the front)
      var sx0 = facing > 0 ? -38 : -6;
      PC.paper(stool, PC.tornRect(id + "stool", sx0, -22, 44, 8, 0.6), "#7a4a2c", { depth: 1.0 });
      PC.paper(stool, PC.tornRect(id + "stoolL", sx0 + 3, -15, 6, 15, 0.4), "#5a3420", { depth: 0.6 });
      PC.paper(stool, PC.tornRect(id + "stoolR", sx0 + 35, -15, 6, 15, 0.4), "#5a3420", { depth: 0.6 });
    }
    var pup = PC.puppet(g(parent), { look: look, cx: S.v(id + ".x", 0), ground: C.ground, scale: C.scale });
    var off = 0.3 + 1.3 * index;
    return function (t) {
      var x = S.v(id + ".x", t), v = (S.v(id + ".x", t + 0.03) - S.v(id + ".x", t - 0.03)) / 0.06;
      var walk = seated ? 0 : clamp(Math.abs(v) / 60);
      pup.set({
        x: x, arms: [S.v(id + ".armL_u", t), S.v(id + ".armL_f", t), S.v(id + ".armR_u", t), S.v(id + ".armR_f", t)],
        lean: S.v(id + ".lean", t), shrug: S.v(id + ".shrug", t), turn: S.v(id + ".turn", t),
        gaze: [S.v(id + ".gaze_x", t), S.v(id + ".gaze_y", t)], mouth: S.v(id + ".mouth", t), brow: S.v(id + ".brow", t),
        openHands: S.v(id + ".open_hands", t), legs: 22 * Math.sin(x / 15) * walk, bob: -Math.abs(Math.sin(x / 15)) * 5 * walk,
        blink: blink(t, off), squash: 1 + 0.012 * Math.sin(t * 2.4 + off), seated: seated, facing: facing
      });
      if (stool) stool.setAttribute("transform", T(x, C.ground, 0, C.scale));
    };
  }

  // --------------------------------------------------------------------- mount
  function orderAt(base, layers, t) {
    var o = base.slice();
    layers.forEach(function (ev) {
      if (ev[0] > t) return;
      var i = o.indexOf(ev[1]); o.splice(i, 1);
      var j = o.indexOf(ev[3]);
      o.splice(ev[2] === "behind" ? j : j + 1, 0, ev[1]);
    });
    return o;
  }

  function mount(scene) {
    var W = scene.layout.width, H = scene.layout.height, chans = new Channels(scene.ch);
    var world = document.getElementById("world"), defs = document.querySelector("#world-svg defs");
    var S = { W: W, H: H, defs: defs, props: scene.props, v: function (k, t) { return chans.value(k, t); } };
    var nodes = {}, updates = [];
    scene.order.forEach(function (id) {
      var wrap = g(world, { "data-id": id });
      nodes[id] = wrap;
      if (scene.props[id]) updates.push(DRAW[scene.props[id].type](wrap, id, scene.props[id], S));
    });
    Object.keys(scene.chars).forEach(function (id) { updates.push(character(nodes[id], id, scene.chars[id], S, scene.order.indexOf(id))); });
    var st = scene.style, over = document.getElementById("over-svg"), fade = null;
    if (st.grain) document.getElementById("stage").insertBefore(PC.grainRaster(null, { seed: st.grain_seed, width: W, height: H, alpha: st.grain_alpha }), over);
    if (st.vignette) {
      var vg = el("radialGradient", { id: "pc-vignette", gradientUnits: "userSpaceOnUse", cx: W / 2, cy: H * 0.52, r: 0.567 * Math.hypot(W, H) });
      vg.appendChild(el("stop", { offset: 0.4, "stop-color": "#1f0d05", "stop-opacity": 0 }));
      vg.appendChild(el("stop", { offset: 1, "stop-color": "#1f0d05", "stop-opacity": 0.33 }));
      var od = el("defs", {}); od.appendChild(vg); over.appendChild(od);
      over.appendChild(el("rect", { x: 0, y: 0, width: W, height: H, fill: "url(#pc-vignette)" }));
    }
    if (st.fade_in > 0) { fade = el("rect", { x: 0, y: 0, width: W, height: H, fill: st.background, opacity: 1 }); over.appendChild(fade); }
    var layers = scene.layers || [], lastOrder = scene.order.join("|");
    function render(t) {
      if (layers.length) {
        var o = orderAt(scene.order, layers, t), key = o.join("|");
        if (key !== lastOrder) { o.forEach(function (id) { world.appendChild(nodes[id]); }); lastOrder = key; }
      }
      var cam = cameraAt(scene.camera, t, W, H);
      world.setAttribute("transform", "translate(" + W / 2 + "," + H / 2 + ") scale(" + cam[2].toFixed(4) + ") translate(" + (-cam[0]).toFixed(2) + "," + (-cam[1]).toFixed(2) + ")");
      for (var i = 0; i < updates.length; i++) updates[i](t);
      if (fade) fade.setAttribute("opacity", (t < st.fade_in ? 1 - EASES.io(t / st.fade_in) : 0).toFixed(4));
    }
    var tl = gsap.timeline({ paused: true });
    PC.drive(tl, scene.duration, render);
    return tl;
  }

  root.PaperScene = { mount: mount, Channels: Channels, cameraAt: cameraAt, EASES: EASES, flipPage: flipPage, DRAW_TYPES: Object.keys(DRAW) };
})(typeof window !== "undefined" ? window : globalThis);
