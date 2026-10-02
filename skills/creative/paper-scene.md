# Paper Scene — write a paper-cut scene as data (creative skill)

> Tool: `paper_scene` (`validate` · `compile` · `render`) · Format: `schemas/artifacts/paper_scene.schema.json` (`format: "paper_scene/1"`) · Engine: `ink-theater/paper-scene.js` on `paper-cut.js` · Design notes: `docs/fork/lot4-paper-scene.md`
> Look and engine: `skills/creative/paper-cut.md`. Sound: `sfx_synth`. Story check: `skills/meta/mute-review.md`.

**What it is.** You describe a paper-cut scene — props, characters, actions on a timeline, camera, sounds, story beats — as one JSON document. The compiler checks it before anything is rendered, then derives three outputs that agree by construction:

1. a HyperFrames project per layout (`<out>/<layout>/index.html`), rendered with the paper-cut style;
2. `sfx_events.json` — the input of `sfx_synth`, every sound placed at the resolved time of its action;
3. `story_beats.json` — the beats for the mute review (`mute_review sheets` / `verify`, or `edit_decisions.metadata.story_beats`).

You never write JavaScript. Motion comes from the named library below; a name that is not in it is an error that lists the known names.

## Flow

1. **Brief → beats first.** From the text brief, write the story beats: what a viewer must *see*, who does it, when (`{id, label, start, end, expected}`). If the scene runs under narration, put the phrase timings in `marks` and give each beat the `phrase` it illustrates.
2. **Set, cast, layouts.** Place props and characters in layout pixels (the camera at zoom 1 shows the whole layout). One `at` per element; `layouts.<name>.at` / `.params` override it for another layout (a 9:16 recomposition of a 16:9 scene).
3. **Actions.** One action per gesture or event, with `start`/`end`. Attach `sound` and `beat` to the action that causes them.
4. **`validate`** — fix every issue (the messages say what and where). Then **`compile`** (writes the outputs only when every check passes).
5. **`render`** on the render machine (Chrome + Node): `CONTRECHAMP_HYPERFRAMES_SPEC=hyperframes@0.8.105` pins the CLI. It renders each layout, synthesizes the sound with `sfx_synth` and muxes it (`<layout>_sound.mp4`). Launch long renders in the background and poll the log.
6. **Mute review — mandatory.** `mute_review` `sheets` with `beats: <out>/story_beats.json`, a *fresh* blind reviewer (brief + beats + sheets only), then `verify`. The checks below catch geometry and timing; they cannot tell whether the story reads. Fix and review again with a new reviewer.

```python
from tools.video.paper_scene import PaperScene
r = PaperScene().execute({"operation": "validate", "scene": "scene.json"})
r = PaperScene().execute({"operation": "compile", "scene": "scene.json", "output_dir": "out"})
r = PaperScene().execute({"operation": "render", "scene": "scene.json", "output_dir": "out"})  # render machine
```

## The document

```json
{
  "format": "paper_scene/1", "id": "my-scene", "brief": "One paragraph: what happens, who does it.",
  "duration": 20.0,
  "marks": {"p1": [0.0, 4.1], "p2": [4.1, 8.5]},
  "insert": {"offset": 75.82, "source": "narration"},
  "layouts": {"landscape": {"aspect": "16:9", "width": 1920, "height": 1080},
              "portrait": {"aspect": "9:16", "width": 1080, "height": 1920, "ui_safe_bottom": 0.2}},
  "style": {"background": "#e8e0cf", "grain": true, "grain_seed": 7, "grain_alpha": 0.55, "vignette": true, "fade_in": 0.45},
  "text_allowed": ["tour 1"],
  "order": ["wall", "desk", "worker"],
  "props": [{"id": "desk", "type": "group", "at": [1500, 760], "params": {"children": [...]},
             "anchors": {"top_left": [-150, -110]}, "init": {}, "layouts": {"portrait": {"at": [800, 1480]}}}],
  "characters": [{"id": "worker", "role": "the agent loop", "look": {"overall": "#3f7f9e"}, "posture": "standing",
                  "scale": 1.4, "at": [230, 760], "pose": {"turn": 0.6}}],
  "camera": {"landscape": [{"t": 0, "center": [960, 540], "zoom": 1.0}, {"t": "p2.start", "target": "desk.center", "zoom": 1.5}],
             "portrait": [{"t": 0, "center": [540, 960], "zoom": 1.0}]},
  "actions": [...],
  "beats": [{"id": "b1", "label": "...", "start": 0, "end": 2, "expected": "...", "phrase": "p1"}],
  "sound": {"peak_dbfs": -18.0, "seed": 11, "reverb_mix": 0.18, "events": [{"t": 3.0, "kind": "bell"}]}
}
```

- **Times**: seconds, or a mark reference `"p2"`, `"p2.start"`, `"p2.end"`, `"p2.end-0.3"`. `duration` may be a mark (`"p24.end"`: the insert lasts as long as the voice).
- **`order`** lists every prop and character once, back to front. `layer` actions change it at a time.
- **`init`**: initial values of a prop's channels (x and y come from `at`): a crane's trolley, a jar's count, a strip's height 0, a line not drawn yet (`draw: 0`).
- **`sound.peak_dbfs`** is the level of the whole sound layer (−1 for a standalone short, about −18 for a layer ducked under a voice).
- **9:16 layouts must declare `ui_safe_bottom`**: 0.2 for Reels / Shorts / TikTok (the app covers the bottom fifth), 0 when nothing covers the frame.
- **A layout moves things, it does not change what happens or when**: one sound and one beat list serve every layout. Override positions per layout, never counts, durations or particle numbers (a `timing` error says which action differs).
- **Names and colours**: ids, layout names, marks, anchors, channels are plain names (`^[A-Za-z0-9_-]+$`); colours are `#rrggbb` or `#rgb`; numbers are finite. Anything else is refused before compilation.

## Props (`type` → what it draws; channels in *italics*; own anchors)

Every prop has *x y rot scale opacity* and the anchors `center top bottom left right origin` (bounding box), plus its `anchors` you declare.

| type | params | channels / anchors |
|---|---|---|
| `shape` | `shape`: `rect` (`rect: [x,y,w,h]`), `circle` (`r`), `ellipse` (`rx, ry`), `poly` (`points`), `gear` (`r, r_in, teeth`); `color`, `depth` (shadow), `amp` (torn edge), `alpha`, `edge`, `shadow`, `rolls: r` (turns with x), `spin` (idle rad/s), `key` (torn-edge seed) | *color* |
| `group` | `children`: shapes (each may have `at`, `rolls`), `spin` | — |
| `label` | `text`, `size`, `color` — centred handwriting with accents; must be in `text_allowed` | — |
| `path` | `points`, `closed`, `color`, `width`, `dashed`, `line_alpha`, `fill_color`, `fill_alpha` | *draw* (0→1 draws the line), *fill* |
| `glow` | `r`, `color` | *alpha* |
| `door` | `rect`, `color`, `slat_color`, `slats`, `inside_color`, `box_color` | *open*; `opening`, `sill` |
| `container` | `item` (`star`/`coin`), `w`, `h`, `capacity`, `glass`, `lid` (colour or null), `glow` | *count empty spill*; `mouth`, `inside`; origin = bottom centre (the tipping corner) |
| `stack` | `w`, `sheet` (thickness), `color`, `line`, `max` | *count flipped*; `top` follows the count; origin = bottom centre |
| `bars` | `n`, `width`, `gap`, `unit` (px per height unit), `color`, `dashed`, `labels` (one per bar), `label_size` | *h_i reveal_i* (i = 1..n); `slot_i`, `top_i`; origin = bottom left |
| `bands` | `colors` (bottom to top), `top0`, `band_h`, `overlap`, `strokes` | *p_i* (painted fraction) |
| `pole` | `rest` (tip offset), `head_color` | *base_x base_y tip_dx tip_dy*; `base`, `tip` |
| `sun` | `r`, `rolls` | *power dim* |
| `conveyor` | `x`, `w`, `legs`, `floor` (at = [0, belt_y]) | *offset* |
| `crane` | `mast_x`, `jib_y`, `jib_x0`, `jib_x1`, `floor` | *trolley hook_y hook_open*; `hook` |
| `burst` | `count`, `area`, `avoid` (rects / circles), `sizes`, `seed`, `spacing`, `column`, `spread`, `sparks` | started by `burst` |
| `curtain` | `top`, `bottom` (night sky colours) | *y* (its lower edge) |
| `tint` | `color`, `alpha`, `glows: [[x,y,r,color,alpha]]` | *night* (0→1) |
| `smoke` | `rise`, `r0`, `r1`, `color` (four idle puffs rising from `at`) | — |
| `scatter` | `area`, `count`, `item` (`flower`/`brick`), `seed`, `colors`, `size` | — |

## Characters

The paper worker (`PaperCut.puppet`): `look` = `skin shirt overall brow hat (colour or false) mustache glasses`; `posture` = `standing` | `seated` (legs bent, upper body lower: put a desk in front); `role` (who it stands for — carried into `story_beats.json`); `scale`; `at: [x, ground_y]`; `pose`: `arms [upL, foreL, upR, foreR]` (degrees, 0 = hanging, + = towards screen right), `lean`, `turn` (−1..1), `gaze [x, y]` (y < 0 looks up: the whole face rises), `mouth` (`smile grin o flat wavy`), `brow`, `shrug`, `open_hands`. Anchors: `hand_L hand_R head center feet`. Walking legs, blinks and breathing are automatic.

## Actions

Common fields: `do`, `id` (optional), `actor` (a character), `target` (`"id"` or `"id.anchor"`), `start`, `end`, `sound`, `beat`. A channel is driven by one action at a time; it keeps its last value afterwards; every action starts from where the channel is (values inherit, nothing snaps back).

| do | does | parameters |
|---|---|---|
| `pose` | eases body and face values | `set: {arms, lean, turn, gaze, mouth, brow, shrug, open_hands}`, `ease` |
| `shrug` | arms out, palms up, brows up, wavy mouth | `turn`, `gaze` |
| `walk_to` | walks to an x | `to` (number or anchor), `dx` |
| `push` | moves a prop along x, the actor behind with both hands on it (forward or back) | `to`, `dx`, `grip` (anchor), `offset` (actor x − prop x; default: where he stands), `lean` |
| `reach` | one hand to a target | `hand` `L`/`R` |
| `point_at` | arm stretched towards a target | `hand` |
| `look_at` | head and gaze towards a target | — |
| `wave` | oscillating arm(s) around the current pose | `hand` `L`/`R`/`both` (mirrored), `amp [upper, fore]`, `freq` |
| `clap` | hands clap | `amp`, `freq` |
| `flip` | flips N pages of a `stack`, gaze following; counts N items | `hand`, `count`, `amp` |
| `hold` | a `pole`'s base follows the hand | `hand` |
| `paint` | the held pole paints the `bands` one after the other (no `end`: computed) | `pole`, `step`, `dur`, `extend`, `retract` |
| `move` | a prop to a point / anchor, or `by: [dx, dy]` | `to`, `by`, `ease` |
| `attach` | a prop follows another prop's anchor | `to: "prop.anchor"`, `by` (own anchor, default `center`) |
| `animate` | eases any channel of a prop (numbers, `#rrggbb` colours) | `channel`, `to`, `ease` |
| `shake` | damped shake along x | `amp`, `freq`, `decay` |
| `tip_over` | a container wobbles, falls to `side` (−1 left / 1 right), lid off, empties | `side`, `wobble_from`, `land` (y or anchor), `slide`, `lid_to`, `shake` |
| `drop_in` | N items fall into a container one by one; counts N | `count`, `from` |
| `overflow` | N items spill over the rim; counts N | `count` |
| `burst` | a burst prop leaves a source (no `end`); counts its particles | `from` |
| `stack_add` | a sheet travels from a source onto a stack; counts 1 | `from` |
| `layer` | changes the draw order at `start` | `behind` / `in_front_of` |

`ease` ∈ `io` (default), `io5`, `out`, `in`, `lin`.

**Repeat — "turn n carries n items".**
```json
{"repeat": {"name": "turn", "spans": [[0.6, 3.0], [3.0, 5.6], [5.6, 7.6]]},
 "actions": [
   {"do": "stack_add", "target": "sheets", "from": "hatch.opening", "start": 0.0, "end": 0.12},
   {"do": "flip", "actor": "reader", "target": "sheets", "count": "$n", "start": 0.47, "end": 0.68,
    "sound": [{"kind": "click", "per_item": true}]},
   {"do": "drop_in", "target": "jar", "count": "$n", "from": "jar.above", "start": 0.55, "end": 0.75,
    "sound": [{"kind": "marimba", "per_item": true, "freq": 392, "pitch_step": 2}]}]}
```
Inside a repeat, `start`/`end` are fractions of the iteration's span (shorter spans = faster turns); `"$n"` is the iteration number, also inside strings (`"channel": "h_$n"`). Ids get `_n`. When one iteration must differ (the camera is elsewhere, the last turn stays), write that turn out after the repeat.

**Sound on an action.** `{"kind", "gain", "pan", ...sfx_synth parameters}` plus one placement: `at` (s after the start of the action's window), `every` (a period over the window, phase `at`), or `per_item` (one per counted item; `pitch_step` semitones up for each next one). Kinds: `click thud metal bell marimba motor swish sparkle pad`.

**Beat on an action.** `"beat": {"id", "label", "expected"}` takes the action's window (or give `start`/`end`). Scene-level `beats` are for beats that are not one action (and carry `phrase`).

## Checks before the render (and how to fix them)

| check | fails when | fix |
|---|---|---|
| `vocabulary`, `references`, `timing` | unknown name, missing id/anchor, a time outside the scene | the message lists the known names |
| `limb_overlap` | two actions drive the same channel at once (same arm, same position) | sequence them, or use the other hand |
| `jump` | a value jumps when an action takes over: a push from where the actor is not, a pose with no duration | walk him there first; give the pose a duration |
| `off_frame` | the place of an action is outside the current camera frame; a character outside the frame at a camera key; a prop outside its layout (a position inherited from another layout) | move the action, the character or the camera; give the prop a position for that layout |
| `contact` | a hand or hook stays more than 8 px from what it holds, pushes, pins or hooks — from the anchor and from the object's own box | bring the actor closer, lower the target, change the grip (an anchor drawn away from the object does not count) |
| `zoom_speed` | camera moves that fit in less than 1.2 s add up to a scale change of ×1.25 or more (holds and reversals do not split them) | one move of 1.2 s or more per strong change: otherwise it reads as a cut |
| `ui_safe_bottom` | an action in the bottom band of a 9:16 frame | raise the set in that layout |
| `beat_in_phrase` | a beat not inside the phrase it illustrates | the picture follows the voice, not the reverse |
| `text_policy` | an on-screen text not in `text_allowed` | counts are shown by objects, not figures |

## Traps only the image shows (mute review)

These cannot be checked from the description; look for them on the sheets and ask the blind reviewer:

- **A look carried by the pupils alone is unreadable on a phone.** Turn the head (`turn`) and raise the whole face (`gaze` y < 0), and hold it ≥ 0.4 s.
- **An arm stretched towards an object reads as "he pushes it"**, whatever the story says. The one at fault stands with his back to the object, and it falls on his side (`tip_over` `side`).
- **An eased-out path puts the object far away on the second frame** (it seems to appear there). Show the source, a held beat, then the spread (the `burst` does: column, hold, dispersion).
- **Cause and effect must be on screen together** (new sheet → taller pile → more flips → more coins → taller strip): the camera must frame both ends of the chain.

## Determinism and limits

The compiler is deterministic (same scene → same bytes); seeds are fixed; the render evaluates the same channels as the checks (a test compares the JavaScript and Python evaluators). Not in the format: free code, characters other than the paper worker, mocap clips, hard cuts, dialogue or lip-sync, music, ambience beds, automatic 16:9 → 9:16 recomposition. Measured on the render Mac: a 20 s 1080×1920 scene renders in about 20 s, sound included.
