/*
 * Paper Cut — a second Ink Theater style (`paper-cut`): coloured cut-paper shapes
 * with torn edges, a doubled drop shadow in SCREEN space, a light rim, paper
 * grain and radial glows; plus a paper "skin" for the puppet that reads the same
 * 16-point poses as InkPuppet, and a small expression API (gaze, mouth, brows,
 * blink, head turn, open hands) driven by the timeline.
 *
 * Browser global `PaperCut`. Load AFTER ink-theater.js (it reuses InkTheater.rng
 * and InkTheater.el). Determinism contract (HyperFrames):
 *   - every "random-looking" edge comes from InkTheater.rng seeded by a string
 *     key, so a shape is identical on every frame and every render;
 *   - no Math.random, no clocks: the scene is a pure function of the timeline
 *     time (see `drive` / `track`), so seeking backwards gives the same image.
 */
(function (root) {
  "use strict";
  var IT = root.InkTheater;
  if (!IT) throw new Error("paper-cut.js needs ink-theater.js loaded first");
  var el = IT.el;

  // ---- configuration (set before building a scene) -----------------------------
  // shadowMode: "clones" (default — two offset copies, re-aimed in screen space)
  //             "filter" (one SVG filter per shape; measured slower, kept for benches)
  var config = { shadowMode: "clones" };

  // ---- seeded randomness ---------------------------------------------------------
  function hashKey(key) {                 // FNV-1a 32-bit over the key's characters
    var s = String(key), h = 0x811c9dc5;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return h >>> 0;
  }
  function rngFor(key) { return IT.rng(hashKey(key)); }
  function uniform(r, a, b) { return a + (b - a) * r(); }
  function gauss(r) {                     // Box-Muller, from the seeded stream
    var u = r() || 1e-12, v = r();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  // ---- easing & timing (ports of a reference Python implementation) ---------------------
  function clamp(x, a, b) { a = a == null ? 0 : a; b = b == null ? 1 : b; return Math.max(a, Math.min(b, x)); }
  function prog(t, t0, t1) { if (t1 <= t0) return t >= t1 ? 1 : 0; return clamp((t - t0) / (t1 - t0)); }
  function lerp(a, b, x) { return a + (b - a) * x; }
  var ease = {
    io: function (x) { x = clamp(x); return x * x * (3 - 2 * x); },
    io5: function (x) { x = clamp(x); return x * x * x * (x * (6 * x - 15) + 10); },
    out: function (x) { x = clamp(x); return 1 - Math.pow(1 - x, 3); },
    in: function (x) { x = clamp(x); return x * x * x; }
  };
  function damped(t, freq, decay) {
    if (t < 0) return 0;
    return Math.exp(-(decay == null ? 3 : decay) * t) * Math.cos(2 * Math.PI * (freq == null ? 2.2 : freq) * t);
  }

  // ---- colours -------------------------------------------------------------------
  function rgbOf(hex) {
    var h = String(hex).replace("#", "");
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  function toHex(c) {
    return "#" + c.map(function (v) { var s = Math.round(clamp(v, 0, 255)).toString(16); return s.length < 2 ? "0" + s : s; }).join("");
  }
  function shade(hex, k) {                // k<0 darkens, k>0 lightens
    var c = rgbOf(hex);
    return toHex(c.map(function (v) { return k < 0 ? v * (1 + k) : v + (255 - v) * k; }));
  }
  function mix(h1, h2, x) {
    var a = rgbOf(h1), b = rgbOf(h2);
    return toHex(a.map(function (v, i) { return lerp(v, b[i], x); }));
  }

  // ---- torn shapes (stable per key) -----------------------------------------------
  var cache = new Map();
  function memo(id, make) { var v = cache.get(id); if (!v) { v = make(); cache.set(id, v); } return v; }

  function tornEdge(p0, p1, r, step, amp, out) {
    var x0 = p0[0], y0 = p0[1], x1 = p1[0], y1 = p1[1], L = Math.hypot(x1 - x0, y1 - y0);
    var n = Math.max(1, Math.floor(L / step));
    var nx = L ? -(y1 - y0) / L : 0, ny = L ? (x1 - x0) / L : 0;
    for (var i = 0; i < n; i++) {
      var u = i / n, j = i ? uniform(r, -amp, amp) : 0;
      out.push([x0 + (x1 - x0) * u + nx * j, y0 + (y1 - y0) * u + ny * j]);
    }
  }
  // Polygon with slightly irregular edges; identical for the same key & corners.
  function tornPoly(key, corners, amp, step) {
    amp = amp == null ? 1.8 : amp; step = step || 13;
    return memo("p|" + key + "|" + amp + "|" + step + "|" + corners.join(";"), function () {
      var r = rngFor(key), pts = [];
      for (var i = 0; i < corners.length; i++) tornEdge(corners[i], corners[(i + 1) % corners.length], r, step, amp, pts);
      return pts;
    });
  }
  function tornRect(key, x, y, w, h, amp) { return tornPoly(key, [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], amp); }
  function tornEllipse(key, rx, ry, amp, n) {
    amp = amp == null ? 1.4 : amp;
    n = n || Math.max(18, Math.floor(Math.PI * (rx + ry) / 9));
    return memo("e|" + key + "|" + rx + "|" + ry + "|" + amp + "|" + n, function () {
      var r = rngFor(key), pts = [];
      for (var i = 0; i < n; i++) {
        var a = 2 * Math.PI * i / n;
        pts.push([(rx + uniform(r, -amp, amp)) * Math.cos(a), (ry + uniform(r, -amp, amp)) * Math.sin(a)]);
      }
      return pts;
    });
  }
  function tornCircle(key, rad, amp, n) {
    return tornEllipse(key, rad, rad, amp, n || Math.max(18, Math.floor(2 * Math.PI * rad / 9)));
  }
  function starPoly(key, rad, inner, amp) {
    inner = inner == null ? 0.45 : inner; amp = amp == null ? 0.8 : amp;
    return memo("s|" + key + "|" + rad + "|" + inner + "|" + amp, function () {
      var r = rngFor(key), pts = [];
      for (var i = 0; i < 10; i++) {
        var rr = (i % 2 === 0 ? rad : rad * inner) + uniform(r, -amp, amp), a = -Math.PI / 2 + i * Math.PI / 5;
        pts.push([rr * Math.cos(a), rr * Math.sin(a)]);
      }
      return pts;
    });
  }
  function r2(x) { return Math.round(x * 100) / 100; }
  function polyD(pts) {
    var d = "M" + r2(pts[0][0]) + " " + r2(pts[0][1]);
    for (var i = 1; i < pts.length; i++) d += "L" + r2(pts[i][0]) + " " + r2(pts[i][1]);
    return d + "Z";
  }

  // ---- the paper fill: doubled drop shadow (screen space) + rim -------------------
  var SHADOW_RGB = "41,20,10";            // (0.16, 0.08, 0.04) of the reference implementation
  var defsEl = null;
  function defs() {
    if (defsEl) return defsEl;
    var svg = document.querySelector("svg[data-paper-cut]") || document.querySelector("svg");
    defsEl = svg.querySelector("defs") || svg.insertBefore(el("defs", {}), svg.firstChild);
    return defsEl;
  }
  function shadowFilter(depth) {
    var id = "pc-shadow-" + String(Math.round(depth * 100));
    if (!document.getElementById(id)) {
      var f = el("filter", { id: id, x: "-10%", y: "-10%", width: "130%", height: "130%", "color-interpolation-filters": "sRGB" });
      [[1.0, 0.10, "a"], [0.55, 0.13, "b"]].forEach(function (s) {
        f.appendChild(el("feOffset", { in: "SourceAlpha", dx: 3 * depth * s[0], dy: 5 * depth * s[0], result: "o" + s[2] }));
        f.appendChild(el("feFlood", { "flood-color": "rgb(" + SHADOW_RGB + ")", "flood-opacity": s[1], result: "f" + s[2] }));
        f.appendChild(el("feComposite", { in: "f" + s[2], in2: "o" + s[2], operator: "in", result: "s" + s[2] }));
      });
      var m = el("feMerge", {});
      ["sa", "sb", "SourceGraphic"].forEach(function (n) { m.appendChild(el("feMergeNode", { in: n })); });
      f.appendChild(m);
      defs().appendChild(f);
    }
    return id;
  }

  // paper(parent, ptsOrD, color, {depth, edge, shadow, alpha}) -> <g>
  // Shadows are two offset copies; their offset is kept in SCREEN space by
  // `updateShadows()` (called once per frame by `drive`), whatever the rotation
  // or zoom of the groups above them.
  function paper(parent, shape, color, o) {
    o = o || {};
    var depth = o.depth == null ? 1 : o.depth, alpha = o.alpha == null ? 1 : o.alpha;
    var d = typeof shape === "string" ? shape : polyD(shape);
    var g = el("g", {});
    var shadow = o.shadow !== false && depth > 0;
    var fill = el("path", { d: d, fill: color, "fill-opacity": alpha });
    if (shadow && config.shadowMode === "clones") {
      [[1.0, 0.10], [0.55, 0.13]].forEach(function (s) {
        var sh = el("path", { d: d, fill: "rgb(" + SHADOW_RGB + ")", "fill-opacity": r2(s[1] * alpha * 1000) / 1000 });
        sh.__pcOff = [3 * depth * s[0], 5 * depth * s[0]];
        registerShadow(sh, parent);
        g.appendChild(sh);
      });
    } else if (shadow) {
      fill.setAttribute("filter", "url(#" + shadowFilter(depth) + ")");
    }
    if (o.edge !== false) {
      fill.setAttribute("stroke", shade(color, 0.35));
      fill.setAttribute("stroke-opacity", 0.45 * alpha);
      fill.setAttribute("stroke-width", 1.3);
      fill.setAttribute("stroke-linejoin", "round");
      fill.setAttribute("vector-effect", "non-scaling-stroke");
    }
    g.appendChild(fill);
    g.fill = fill;
    if (parent) parent.appendChild(g);
    return g;
  }
  // Re-cut a paper piece made by `paper()` to a new outline (same key → same tears).
  function reshape(piece, shape) {
    var d = typeof shape === "string" ? shape : polyD(shape), ps = piece.querySelectorAll("path");
    for (var i = 0; i < ps.length; i++) ps[i].setAttribute("d", d);
    return piece;
  }
  // A paper "strip" (stroked capsule) with its own screen-space shadow.
  function capsule(parent, color, width, o) {
    o = o || {};
    var g = el("g", {});
    var sh = el("path", { fill: "none", stroke: "rgb(" + SHADOW_RGB + ")", "stroke-opacity": o.shadowAlpha || 0.18,
      "stroke-width": width, "stroke-linecap": "round", "stroke-linejoin": "round" });
    sh.__pcOff = o.offset || [2.0, 3.5];
    registerShadow(sh, parent);
    var body = el("path", { fill: "none", stroke: color, "stroke-width": width, "stroke-linecap": "round", "stroke-linejoin": "round" });
    g.appendChild(sh); g.appendChild(body);
    if (parent) parent.appendChild(g);
    return {
      g: g, body: body, shadow: sh,
      set: function (pts) { var d = "M" + pts.map(function (p) { return r2(p[0]) + " " + r2(p[1]); }).join("L"); sh.setAttribute("d", d); body.setAttribute("d", d); }
    };
  }

  // Shadow registry: every shadow copy remembers its frame (the group it is drawn
  // in). Once per frame we read each frame's CTM and set the copy's local offset so
  // that, on screen, it is always (dx, dy) device pixels down-right — exactly what
  // a raster reference implementation does in device space.
  var shadowFrames = [];
  function registerShadow(sh, frame) {
    sh.setAttribute("transform", "translate(" + sh.__pcOff[0] + "," + sh.__pcOff[1] + ")");
    if (!frame) return;
    if (!frame.__pcShadows) { frame.__pcShadows = []; shadowFrames.push(frame); }
    frame.__pcShadows.push(sh);
  }
  function updateShadows() {
    for (var i = 0; i < shadowFrames.length; i++) {
      var f = shadowFrames[i], m = f.getCTM && f.getCTM();
      if (!m) continue;
      var key = r2(m.a * 1000) + "," + r2(m.b * 1000) + "," + r2(m.c * 1000) + "," + r2(m.d * 1000);
      if (f.__pcKey === key) continue;
      f.__pcKey = key;
      var det = m.a * m.d - m.b * m.c || 1;
      for (var j = 0; j < f.__pcShadows.length; j++) {
        var sh = f.__pcShadows[j], dx = sh.__pcOff[0], dy = sh.__pcOff[1];
        var lx = (m.d * dx - m.c * dy) / det, ly = (-m.b * dx + m.a * dy) / det;
        sh.setAttribute("transform", "translate(" + r2(lx) + "," + r2(ly) + ")");
      }
    }
  }

  // Radial glow (centre alpha 1 → 0.35 at 45% → 0); per-frame strength = opacity.
  function glow(parent, x, y, rad, color, alpha) {
    var id = "pc-glow-" + color.replace("#", "");
    if (!document.getElementById(id)) {
      var g = el("radialGradient", { id: id });
      [[0, 1], [0.45, 0.35], [1, 0]].forEach(function (s) { g.appendChild(el("stop", { offset: s[0], "stop-color": color, "stop-opacity": s[1] })); });
      defs().appendChild(g);
    }
    var c = el("circle", { cx: x, cy: y, r: rad, fill: "url(#" + id + ")", opacity: alpha == null ? 0.6 : alpha });
    if (parent) parent.appendChild(c);
    return c;
  }

  // ---- paper grain ---------------------------------------------------------------
  // Raster overlay (recommended): a seeded noise canvas made ONCE at load, composited
  // with mix-blend-mode: overlay — ported from a reference Python implementation.
  function grainRaster(container, o) {
    o = o || {};
    var W = o.width || 1080, H = o.height || 1920, r = IT.rng(o.seed || 7);
    var N = W * H, v = new Float32Array(N), x, y, i;
    var bw = Math.ceil(W / 6), bh = Math.ceil(H / 6), big = new Float32Array(bw * bh);
    var fw = Math.ceil(W / 12), fh = Math.ceil(H / 2), fib = new Float32Array(fw * fh);
    for (i = 0; i < N; i++) v[i] = 0.5 * gauss(r);
    for (i = 0; i < big.length; i++) big[i] = 0.35 * gauss(r);
    for (i = 0; i < fib.length; i++) fib[i] = 0.25 * gauss(r);
    var sum = 0, sq = 0;
    for (y = 0; y < H; y++) for (x = 0; x < W; x++) {
      i = y * W + x;
      v[i] += big[((y / 6) | 0) * bw + ((x / 6) | 0)] + fib[((y / 2) | 0) * fw + ((x / 12) | 0)];
      sum += v[i]; sq += v[i] * v[i];
    }
    var mean = sum / N, sd = Math.sqrt(sq / N - mean * mean) || 1, k = o.contrast || 9;
    var cv = document.createElement("canvas");
    cv.width = W; cv.height = H;
    var ctx = cv.getContext("2d"), img = ctx.createImageData(W, H), px = img.data;
    for (i = 0; i < N; i++) {
      var gr = Math.max(0, Math.min(255, Math.round(128 + (v[i] - mean) / sd * k)));
      px[4 * i] = px[4 * i + 1] = px[4 * i + 2] = gr; px[4 * i + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    cv.style.cssText = "position:absolute;left:0;top:0;width:" + W + "px;height:" + H + "px;pointer-events:none;" +
      "mix-blend-mode:" + (o.blend || "overlay") + ";opacity:" + (o.alpha == null ? 0.55 : o.alpha);
    if (container) container.appendChild(cv);
    return cv;
  }
  // SVG alternative: feTurbulence at a FIXED seed (no boil). Measured slower.
  function grainTurbulence(svg, o) {
    o = o || {};
    var W = o.width || 1080, H = o.height || 1920;
    var f = el("filter", { id: "pc-grain", x: 0, y: 0, width: "100%", height: "100%", "color-interpolation-filters": "sRGB" });
    f.appendChild(el("feTurbulence", { type: "fractalNoise", baseFrequency: o.frequency || "0.85 0.6", numOctaves: 2, seed: o.seed || 7, stitchTiles: "stitch" }));
    f.appendChild(el("feColorMatrix", { type: "matrix", values: "0.33 0.33 0.33 0 0  0.33 0.33 0.33 0 0  0.33 0.33 0.33 0 0  0 0 0 0 1" }));
    f.appendChild(el("feComponentTransfer", {}, [
      el("feFuncR", { type: "linear", slope: 0.55, intercept: 0.22 }),
      el("feFuncG", { type: "linear", slope: 0.55, intercept: 0.22 }),
      el("feFuncB", { type: "linear", slope: 0.55, intercept: 0.22 })
    ]));
    svg.querySelector("defs").appendChild(f);
    var rect = el("rect", { x: 0, y: 0, width: W, height: H, filter: "url(#pc-grain)", opacity: o.alpha == null ? 0.55 : o.alpha });
    rect.style.mixBlendMode = o.blend || "overlay";
    svg.appendChild(rect);
    return rect;
  }

  // ---- keyed poses & seek-safe drivers -----------------------------------------
  // keyed([[t, {...}], ...]): every key inherits the previous key's values (so a
  // value set by an early key does not silently snap back later).
  function keyed(keys) {
    var out = [], cur = {};
    keys.forEach(function (k) { var n = {}, p; for (p in cur) n[p] = cur[p]; for (p in k[1]) n[p] = k[1][p]; cur = n; out.push([k[0], n]); });
    return out;
  }
  function copy(o) { var n = {}; for (var p in o) n[p] = Array.isArray(o[p]) ? o[p].slice() : o[p]; return n; }
  // sample(keys, t): smoothstep between keys; numbers & arrays interpolate, strings switch at half-way.
  function sample(keys, t) {
    if (t <= keys[0][0]) return copy(keys[0][1]);
    for (var i = 0; i < keys.length - 1; i++) {
      var t0 = keys[i][0], t1 = keys[i + 1][0];
      if (t <= t1) {
        var x = t1 > t0 ? ease.io((t - t0) / (t1 - t0)) : 1, a = keys[i][1], b = keys[i + 1][1], r = {};
        for (var k in b) {
          var va = k in a ? a[k] : b[k], vb = b[k];
          if (typeof vb === "number") r[k] = lerp(va, vb, x);
          else if (Array.isArray(vb)) r[k] = vb.map(function (q, j) { return lerp(va[j], q, x); });
          else r[k] = x > 0.5 ? vb : va;
        }
        return r;
      }
    }
    return copy(keys[keys.length - 1][1]);
  }
  // drive(tl, duration, render): one linear proxy tween over [start, start+duration];
  // render(t) is called with the ABSOLUTE time, then shadows are re-aimed. Write
  // render as a pure function of t and the scene is seek-safe by construction.
  function drive(tl, duration, render, o) {
    o = o || {};
    var start = o.start || 0, proxy = { u: 0 };
    function at(t) { render(t); updateShadows(); }
    at(start);
    tl.to(proxy, { u: 1, duration: duration, ease: "none", onUpdate: function () { at(start + proxy.u * duration); } }, start);
    return tl;
  }
  // track(tl, keys, apply, {start, end}): sample keyed values on the timeline —
  // e.g. drive a puppet's face while InkPuppet.choreograph drives its body.
  function track(tl, keys, apply, o) {
    o = o || {};
    var start = o.start != null ? o.start : keys[0][0], end = o.end != null ? o.end : keys[keys.length - 1][0];
    var proxy = { u: 0 }, dur = Math.max(1e-3, end - start);
    function at(t) { apply(sample(keys, t), t); updateShadows(); }
    at(start);
    tl.to(proxy, { u: 1, duration: dur, ease: "none", onUpdate: function () { at(start + proxy.u * dur); } }, start);
    return tl;
  }

  // ---- the paper puppet ------------------------------------------------------------
  // Pose = the InkPuppet 16-point format (hips, chest, neck, head, shL/elL/haL,
  // shR/elR/haR, hipL/knL/ftL, hipR/knR/ftR, rootY, groundY), y down, origin at the
  // hips or the ground. `workerPose` builds one from joint ANGLES with the chunky
  // proportions of the reference worker character; mocap clips (long limbs) also drive it.
  function vec(aDeg, L) { var a = aDeg * Math.PI / 180; return [L * Math.sin(a), L * Math.cos(a)]; }
  function add(p, q) { return [p[0] + q[0], p[1] + q[1]]; }
  function workerPose(p) {
    p = p || {};
    var arms = p.arms || [-12, -6, 12, 6], legs = p.legs || 0, sh = p.shrug || 0, sq = p.squash || 1;
    var lean = (p.lean || 0) * Math.PI / 180, cl = Math.cos(lean), sl = Math.sin(lean);
    function up(pt) { return [pt[0] * cl - pt[1] * sl, (pt[0] * sl + pt[1] * cl) * sq]; }   // upper body: lean about the feet
    function low(pt) { return [pt[0], pt[1] * sq]; }
    var po = { rootY: p.bob || 0, groundY: 0 };
    po.hips = up([0, -42]); po.chest = up([0, -70]); po.neck = up([0, -102]); po.head = up([0, -132 + sh * 3]);
    [["L", -1, arms[0], arms[1]], ["R", 1, arms[2], arms[3]]].forEach(function (a) {
      var s = [a[1] * 26, -94 - sh * 13], e = add(s, vec(a[2], 25)), h = add(e, vec(a[3], 24));
      po["sh" + a[0]] = up(s); po["el" + a[0]] = up(e); po["ha" + a[0]] = up(h);
    });
    [["L", -1, legs], ["R", 1, -legs]].forEach(function (l) {
      var hp = [l[1] * 11, -42], f = add(hp, vec(l[2], 38));
      po["hip" + l[0]] = low(hp); po["kn" + l[0]] = low([(hp[0] + f[0]) / 2, (hp[1] + f[1]) / 2]); po["ft" + l[0]] = low(f);
    });
    return po;
  }

  var MOUTHS = {
    smile: { d: "M6.93 -2A8 8 0 0 1 -6.93 -2", fill: false },
    grin: { d: "M-9 -2C-6 9 6 9 9 -2Z", fill: true },
    o: { d: "M-4.3 0A4.3 5.38 0 1 0 4.3 0A4.3 5.38 0 1 0 -4.3 0Z", fill: true },
    flat: { d: "M-6 0L6 0", fill: false },
    wavy: { d: "M-8 0C-4 -4 -1 3 2 0C4 -3 7 2 8 0", fill: false }
  };
  var DEFAULT_FACE = { turn: 0, gaze: [0, 0], mouth: "smile", brow: 0, blink: 0, openHands: 0 };

  // puppet(mount, {cx, ground, scale, look}) — same driving surface as an InkPuppet
  // (setPose / place / outer), so InkPuppet.choreograph(tl, paperPup, [...]) works.
  // look: {name, skin, shirt, overall, brow, hat, mustache, headR}
  function puppet(mount, opts) {
    opts = opts || {};
    var look = opts.look || {}, key = look.name || "pup";
    var skin = look.skin || "#f2c6a0", shirt = look.shirt || "#f4e9d8", over = look.overall || "#3f7f9e";
    var hat = look.hat === false ? null : (look.hat || "#f2c230"), browC = look.brow || "#5a3a2a", HR = look.headR || 30;
    var outer = el("g", {}), body = el("g", {});
    outer.appendChild(body); mount.appendChild(outer);

    // legs + feet
    var legs = {}, feet = {};
    ["L", "R"].forEach(function (s) {
      legs[s] = capsule(body, shade(over, -0.12), 14);
      var f = el("g", {}); body.appendChild(f);
      paper(f, tornEllipse(key + "foot" + s, 11, 6), "#4a3428", { depth: 0.6 });
      feet[s] = f;
    });
    // torso (drawn in canonical worker coordinates, mapped onto hips→neck)
    var torso = el("g", {}); body.appendChild(torso);
    var shirtP = paper(torso, tornPoly(key + "shirt", [[-28, -102], [28, -102], [30, -66], [-30, -66]]), shirt, { depth: 1 });
    paper(torso, tornPoly(key + "over", [[-21, -86], [21, -86], [31, -42], [29, -36], [-29, -36], [-31, -42]]), over, { depth: 0.8 });
    var straps = el("g", {}); torso.appendChild(straps);
    var strapL = el("path", { stroke: shade(over, -0.2), "stroke-width": 4, fill: "none" }), strapR = el("path", { stroke: shade(over, -0.2), "stroke-width": 4, fill: "none" });
    straps.appendChild(strapL); straps.appendChild(strapR);
    [-1, 1].forEach(function (sd) { straps.appendChild(el("circle", { cx: sd * 15, cy: -82, r: 2.6, fill: "#f4e3b5" })); });
    paper(torso, tornPoly(key + "pocket", [[-10, -74], [10, -74], [9, -60], [-9, -60]]), shade(over, 0.12), { depth: 0.3 });
    // arms + hands (in front of the torso)
    var arms = {}, hands = {}, thumbs = {};
    ["L", "R"].forEach(function (s) {
      arms[s] = { up: capsule(body, shirt, 14), fore: capsule(body, shade(shirt, 0.06), 12) };
      var h = el("g", {}); body.appendChild(h);
      thumbs[s] = capsule(h, skin, 5);
      hands[s] = { g: h, open: paper(h, tornCircle(key + "hand" + s + "o", 9.5), skin, { depth: 0.5 }), closed: paper(h, tornCircle(key + "hand" + s, 8), skin, { depth: 0.5 }) };
    });
    // head
    var head = el("g", {}); body.appendChild(head);
    var ears = {};
    [-1, 1].forEach(function (sd) { var e = el("g", {}); head.appendChild(e); paper(e, tornEllipse(key + "ear" + sd, 6, 8), shade(skin, -0.07), { depth: 0.4 }); ears[sd] = e; });
    paper(head, tornCircle(key + "head", HR), skin, { depth: 1 });
    var face = el("g", {}); head.appendChild(face);
    var feat = el("g", {}); face.appendChild(feat);
    var cheeks = [-1, 1].map(function (sd) { var c = el("circle", { cx: sd * 16, cy: 9, r: 6, fill: "rgb(242,115,107)", "fill-opacity": 0.32 }); feat.appendChild(c); return c; });
    var eyes = {}, brows = {};
    [-1, 1].forEach(function (sd) {
      var eg = el("g", {}); feat.appendChild(eg);
      paper(eg, tornEllipse(key + "eye" + sd, 6.2, 7.4, 0.5), "#fffaf0", { depth: 0.25, edge: false });
      var pupil = el("g", {}); eg.appendChild(pupil);
      pupil.appendChild(el("circle", { cx: 0, cy: 0, r: 3.5, fill: "#2a1d18" }));
      pupil.appendChild(el("circle", { cx: 1.1, cy: -1.2, r: 1.1, fill: "#ffffff", "fill-opacity": 0.9 }));
      eyes[sd] = { g: eg, pupil: pupil };
      brows[sd] = el("path", { stroke: browC, "stroke-width": 3.2, "stroke-linecap": "round", fill: "none" });
      feat.appendChild(brows[sd]);
    });
    var nose = el("g", {}); feat.appendChild(nose);
    paper(nose, tornCircle(key + "nose", 4.6, 0.4), shade(skin, -0.1), { depth: 0.3, edge: false });
    var mus = null;
    if (look.mustache) {
      mus = el("g", {}); feat.appendChild(mus);
      paper(mus, tornPoly(key + "mus", [[-13, 0], [-4, -4], [0, -2], [4, -4], [13, 0], [6, 4], [0, 2], [-6, 4]]), "#6b4430", { depth: 0.3 });
    }
    var mouth = el("g", {}); feat.appendChild(mouth);
    var mouths = {};
    Object.keys(MOUTHS).forEach(function (m) {
      var spec = MOUTHS[m];
      var pth = el("path", spec.fill ? { d: spec.d, fill: "#5a2620" } : { d: spec.d, fill: "none", stroke: "#5a2620", "stroke-width": 2.8, "stroke-linecap": "round" });
      pth.style.display = "none"; mouth.appendChild(pth); mouths[m] = pth;
    });
    var hatG = null, brim = null;
    if (hat) {
      hatG = el("g", {}); face.appendChild(hatG);
      var dome = []; for (var i = 0; i < 15; i++) dome.push([31 * Math.cos(Math.PI + i * Math.PI / 14), 24 * Math.sin(Math.PI + i * Math.PI / 14)]);
      paper(hatG, tornPoly(key + "dome", dome), hat, { depth: 1 });
      brim = paper(hatG, tornPoly(key + "brim", [[-37, -2], [37, -2], [36, 5], [-36, 5]]), shade(hat, -0.08), { depth: 0.6 });
      paper(hatG, tornPoly(key + "ridge", [[-4, -24], [4, -24], [4, -2], [-4, -2]]), shade(hat, 0.25), { depth: 0.2, edge: false });
    }

    var state = { pose: workerPose({}), face: copy(DEFAULT_FACE), shrugSeen: null, turnSeen: null };
    function T(x, y, rot, sx, sy) {
      return "translate(" + r2(x) + "," + r2(y) + ")" + (rot ? " rotate(" + r2(rot) + ")" : "") + (sx != null ? " scale(" + r2(sx * 1000) / 1000 + "," + r2((sy == null ? sx : sy) * 1000) / 1000 + ")" : "");
    }
    function draw() {
      var po = state.pose, f = state.face, turn = f.turn || 0, gx = f.gaze[0], gy = f.gaze[1];
      // legs
      ["L", "R"].forEach(function (s) {
        var side = s === "L" ? -1 : 1;
        legs[s].set([po["hip" + s], po["kn" + s], po["ft" + s]]);
        feet[s].setAttribute("transform", T(po["ft" + s][0] + side * 3 + turn * 4, po["ft" + s][1] + 2));
      });
      // torso frame: canonical (0,-42)→hips, (0,-102)→neck
      var dx = po.neck[0] - po.hips[0], dy = po.neck[1] - po.hips[1], L = Math.hypot(dx, dy) || 1;
      var rot = Math.atan2(dx, -dy) * 180 / Math.PI, k = L / 60;
      torso.setAttribute("transform", T(po.hips[0], po.hips[1], rot, k) + " translate(0,42)");
      // shrug read back from the points: how far the shoulders rose in the torso frame
      var a = -rot * Math.PI / 180, mid = [(po.shL[0] + po.shR[0]) / 2 - po.hips[0], (po.shL[1] + po.shR[1]) / 2 - po.hips[1]];
      var shY = (mid[0] * Math.sin(a) + mid[1] * Math.cos(a)) / k - 42, sh = clamp((-94 - shY) / 13, 0, 1);
      sh = Math.round(sh * 50) / 50;
      if (sh !== state.shrugSeen) {
        state.shrugSeen = sh;
        var top = -102 - sh * 10;
        shirtP.querySelectorAll("path").forEach(function (p) { p.setAttribute("d", polyD(tornPoly(key + "shirt", [[-28, top], [28, top], [30, -66], [-30, -66]]))); });
        strapL.setAttribute("d", "M-16 -86L-20 " + (top + 2)); strapR.setAttribute("d", "M16 -86L20 " + (top + 2));
      }
      // arms & hands
      ["L", "R"].forEach(function (s) {
        var side = s === "L" ? -1 : 1, sp = po["sh" + s], ep = po["el" + s], hp = po["ha" + s];
        arms[s].up.set([sp, ep]); arms[s].fore.set([ep, hp]);
        hands[s].g.setAttribute("transform", T(hp[0], hp[1]));
        var open = f.openHands > 0.3;
        hands[s].open.style.display = open ? "" : "none"; hands[s].closed.style.display = open ? "none" : "";
        if (open) {
          var fa = Math.atan2(hp[0] - ep[0], hp[1] - ep[1]) * 180 / Math.PI + side * 70;
          thumbs[s].set([[0, 0], vec(fa, 9)]); thumbs[s].g.style.display = "";
        } else thumbs[s].g.style.display = "none";
      });
      // head: turn tilts it; looking up raises the whole face (not just the pupils)
      var tilt = turn * 6 + Math.min(0, gy) * 3 * (turn >= 0 ? 1 : -1);
      head.setAttribute("transform", T(po.head[0], po.head[1], tilt + rot));
      [-1, 1].forEach(function (sd) { ears[sd].setAttribute("transform", T(sd * 29 - turn * 4, 2)); });
      face.setAttribute("transform", T(0, -10 * Math.max(0, -gy)));
      var fx = turn * 14;
      var open_ = Math.max(0.08, 1 - (f.blink || 0));
      [-1, 1].forEach(function (sd) {
        var ex = fx + sd * 10.5;
        eyes[sd].g.setAttribute("transform", T(ex, -2, 0, 1, open_));
        eyes[sd].pupil.setAttribute("transform", T(gx * 3.4 + turn * 1.2, gy * 3.2));
        var by = -13 - (f.brow || 0) * 5, tl = sd * (f.brow || 0) * 2.5;
        brows[sd].setAttribute("d", "M" + r2(ex - 5.5) + " " + r2(by + tl) + "L" + r2(ex + 5.5) + " " + r2(by - tl));
      });
      // cheeks follow the turn
      cheeks[0].setAttribute("cx", r2(fx - 16)); cheeks[1].setAttribute("cx", r2(fx + 16));
      nose.setAttribute("transform", T(fx * 1.35, 5));
      if (mus) mus.setAttribute("transform", T(fx * 1.3, 11));
      mouth.setAttribute("transform", T(fx * 1.2, look.mustache ? 16 : 14));
      Object.keys(mouths).forEach(function (m) { mouths[m].style.display = m === f.mouth ? "" : "none"; });
      if (hatG) {
        hatG.setAttribute("transform", T(turn * 3, -18));
        var tn = Math.round(turn * 100) / 100;
        if (tn !== state.turnSeen) {
          state.turnSeen = tn;
          var bd = polyD(tornPoly(key + "brim", [[-37 + tn * 4, -2], [37 + tn * 4, -2], [36 + tn * 6, 5], [-36 + tn * 2, 5]]));
          brim.querySelectorAll("path").forEach(function (p) { p.setAttribute("d", bd); });
        }
      }
    }
    var scale = opts.scale || 1;
    var pup = {
      outer: outer, cx: opts.cx != null ? opts.cx : 540, ground: opts.ground != null ? opts.ground : 1600, scale: scale,
      setPose: function (po) { state.pose = po; draw(); },
      // setFace({turn, gaze:[x,y], mouth: smile|grin|o|flat|wavy, brow, blink, openHands})
      setFace: function (fc) { for (var p in fc) if (fc[p] != null) state.face[p] = fc[p]; draw(); },
      place: function (groundY, rootY) {
        outer.setAttribute("transform", T(pup.cx, pup.ground + ((rootY || 0) - (groundY || 0)) * pup.scale, 0, pup.scale));
      },
      // one call for keyed scene data: {x, arms, legs, lean, shrug, bob, squash, turn, gaze, mouth, brow, blink, openHands}
      set: function (p) {
        if (p.x != null) pup.cx = p.x;
        state.face = { turn: p.turn || 0, gaze: p.gaze || [0, 0], mouth: p.mouth || "smile", brow: p.brow || 0, blink: p.blink || 0, openHands: p.openHands || 0 };
        var po = workerPose(p);
        state.pose = po; draw();
        pup.place(0, (p.bob || 0) / pup.scale);   // bob is in scene pixels, like the reference implementation
      }
    };
    pup.setPose(state.pose); pup.place(0, 0);
    return pup;
  }

  root.PaperCut = {
    config: config, hashKey: hashKey, rngFor: rngFor,
    clamp: clamp, prog: prog, lerp: lerp, ease: ease, damped: damped,
    shade: shade, mix: mix,
    tornPoly: tornPoly, tornRect: tornRect, tornCircle: tornCircle, tornEllipse: tornEllipse, starPoly: starPoly, polyD: polyD,
    paper: paper, reshape: reshape, capsule: capsule, glow: glow, updateShadows: updateShadows,
    grainRaster: grainRaster, grainTurbulence: grainTurbulence,
    keyed: keyed, sample: sample, drive: drive, track: track,
    workerPose: workerPose, puppet: puppet, MOUTHS: Object.keys(MOUTHS)
  };
})(window);
