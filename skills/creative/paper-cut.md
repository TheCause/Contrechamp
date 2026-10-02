# Paper Cut — coloured cut-paper animation (creative skill)

> Style id: `paper-cut` · Engine: `ink-theater/paper-cut.js` (on top of `ink-theater/ink-theater.js`) · Runtime: HyperFrames (atelier)
> Sister style of `ink-sketch` (`skills/creative/ink-theater.md`): same engine, same determinism rules, colour paper instead of black ink.

**What it is:** a warm, tactile **cut-paper** world — flat coloured shapes with slightly torn edges, a soft doubled drop shadow, a light rim, paper grain — where small chunky characters act out a little story. Good for illustrative B-roll and wordless explainers. Not realistic footage (use the media bank for that). Runs on the `animation` pipeline in atelier mode; it is NOT its own pipeline.

**Writing a scene from a brief?** Describe it as data with `skills/creative/paper-scene.md` (`paper_scene` tool): the compiler checks it before the render and derives the sound events and the mute-review beats. The cheat-sheet below is for hand-written compositions.

## Engine cheat-sheet (`PaperCut`)

- **Shapes, stable per key** (seeded with `InkTheater.rng`, never `Math.random`): `tornPoly(key, corners, amp, step)`, `tornRect(key,x,y,w,h,amp)`, `tornCircle(key,r,amp)`, `tornEllipse(key,rx,ry,amp)`, `starPoly(key,r)`. Same key ⇒ same tears on every frame and every render.
- **Fill:** `paper(parent, shape, color, {depth, edge, shadow, alpha})` → a `<g>` with two shadow copies (offset (3,5)·depth and ×0.55 device px, kept in SCREEN space whatever the rotation/zoom above) + a light rim. `reshape(piece, shape)` re-cuts it. `capsule(parent, color, width)` → `.set([[x,y],…])` for paper strips (limbs, poles).
- **Light & texture:** `glow(parent,x,y,r,color,alpha)` (radial, opacity per frame); `grainRaster(container,{seed})` — a seeded noise canvas made once at load, `mix-blend-mode: overlay` (recommended); `grainTurbulence(svg)` — `feTurbulence` at a fixed seed (no boil).
- **Time:** `drive(tl, duration, render)` — one linear proxy tween; `render(t)` gets the absolute time and must be a pure function of it. `keyed(keys)` (each key inherits the previous one) + `sample(keys,t)` (smoothstep; numbers/arrays interpolate, strings switch half-way). `track(tl, keys, apply)` for a keyed sub-track (e.g. a face over a mocap body). `ease.{io,io5,out,in}`, `damped`, `prog`, `lerp`.

## Characters — paper skin on the puppet pose

`PaperCut.puppet(mount, {cx, ground, scale, look})` dresses the **same 16-point pose as `InkPuppet`** (hips, chest, neck, head, shoulders/elbows/hands, hips/knees/feet): filled capsule limbs, round hands, overalls, hard hat, face. It exposes `setPose` / `place`, so `InkPuppet.choreograph(tl, paperPup, [{clip:"wave"}])` drives it with real mocap (long mocap limbs: pass a `scale`).

- `PaperCut.workerPose({arms:[upL,foreL,upR,foreR], legs, lean, shrug, bob, squash})` builds a pose from joint angles (degrees, 0 = hanging down, + = towards screen right) with chunky proportions.
- **Expressions** — `pup.setFace({turn, gaze:[x,y], mouth, brow, blink, openHands})`: `turn` −1..1 turns the head (features shift, slight tilt); `gaze` y < 0 raises the **whole face**, not just the pupils (pupils alone are unreadable on a phone); `mouth` ∈ `smile|grin|o|flat|wavy`; `brow` 0..1 raises and tilts; `blink` 0..1; `openHands` > 0.3 shows open palms (shrug).
- `pup.set({x, ...pose angles, ...face})` = one call per frame from keyed scene data.

## Rules (from the first production with this look)

1. Key poses inherit: a value set early must not snap back later — use `keyed`, and any position overridden by code must rejoin the next key.
2. A character looking up: raise the face and turn the head, not only the pupils.
3. An arm reaching towards an object reads as "pushing it". The one at fault stands with his BACK to the object, and it falls on HIS side.
4. Ease-out along a path = the object is already far on frame 2. Show the source, a held beat, then the spread.
5. A prop outside the current camera frame does not exist — bound positions by the camera, not the page.
6. Show contacts (hook touching the load, hands on the sun).
7. Strong zoom changes need ≥ 1.2 s or they read as cuts.
8. 9:16 Reels: keep the bottom ~20 % free of action (UI overlay).

## Determinism & speed (measured on the render Mac, 1080×1920, 30 fps)

- Seeded shapes, no clocks, one `drive` per scene ⇒ the same frame renders to the same bytes, and a frame reached after seeking backwards or forwards is identical. **Write every attribute on every frame**: a value set only "when needed" (e.g. a colour only while dimmed) keeps a stale value after a backward seek. `setFace` sets the whole face (omitted fields = defaults), `drive`/`track` re-render on every seek.
- Check it with `python scripts/paper_cut_checks.py <composition> --out <dir>`: probe times over the whole duration visited ascending, descending and interleaved — (b) determinism, (c) seek-safety — behind a render gate (no uniform frame, the scene must move, no "seeks skipped"), plus (a) review sheets to compare by eye (`--sheet-offset 0.1` matches sheets cut with `ffmpeg fps=4`). Pin the CLI with `CONTRECHAMP_HYPERFRAMES_SPEC=hyperframes@<version>` to compare across days.
- **gsap**: the examples load the repo's vendored copy (`.agents/skills/music-to-video/references/motion-primitives/assets/gsap.min.js`, GSAP standard license) through a symlink — never add another copy (its license is not AGPL-compatible free software), never hot-link a CDN at render time.
- Shadows as copies + raster grain: 20 s (600 frames) in ~20 s wall. SVG-filter shadows double that (~41 s) — keep `PaperCut.config.shadowMode = "clones"` (default). Bench: `ink-theater/examples/paper-cut-sunrise/bench.sh`.

## Reference build

- `ink-theater/examples/paper-cut-sunrise/` — 5 s: the factory shutter opens, the sun rolls out onto the conveyor pushed by a worker, another beckons. `npx hyperframes lint ink-theater/examples/paper-cut-sunrise`.
