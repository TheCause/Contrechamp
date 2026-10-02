// Evaluates every channel and the camera of a compiled paper scene with the
// RENDER's evaluator (ink-theater/paper-scene.js), at the given times.
//   node eval_channels.js <paper-scene.js> <compiled.json> <times.json>  -> JSON on stdout
// Used by the paper_scene tests (tests/tools) to compare with the Python evaluator.
"use strict";
const fs = require("fs");
const vm = require("vm");
const [src, compiledPath, timesPath] = process.argv.slice(2);
const ctx = {
  InkTheater: { el: () => ({}), rng: () => () => 0.5 },
  PaperCut: {
    mix: (a, b, x) => {
      const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
      const A = p(a), B = p(b);
      return "#" + A.map((v, i) => Math.round(Math.max(0, Math.min(255, v + (B[i] - v) * x))).toString(16).padStart(2, "0")).join("");
    },
  },
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(src, "utf8"), ctx);
const scene = JSON.parse(fs.readFileSync(compiledPath, "utf8"));
const times = JSON.parse(fs.readFileSync(timesPath, "utf8"));
const ch = new ctx.PaperScene.Channels(scene.ch);
const out = { channels: {}, camera: [] };
for (const key of Object.keys(scene.ch)) out.channels[key] = times.map((t) => ch.value(key, t));
out.camera = times.map((t) => ctx.PaperScene.cameraAt(scene.camera, t, scene.layout.width, scene.layout.height));
process.stdout.write(JSON.stringify(out));
