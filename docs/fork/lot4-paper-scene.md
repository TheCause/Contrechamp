# Lot D — `paper_scene`, a description format for paper-cut scenes

Lots A, B and C gave Contrechamp a paper-cut style (`ink-theater/paper-cut.js`), synthesized
sound effects (`tools/audio/sfx_synth.py`) and a blind, sound-off story review
(`tools/analysis/mute_review.py`). Each one is fed by hand: a scene is a hand-written
`render(t)`, its sound events a hand-typed list, its story beats another hand-typed list. Three
lists that must agree, and nothing makes them agree.

Lot D adds one **declarative scene description** that an agent writes from a text brief, and a
**deterministic compiler** that derives the three from it:

1. a HyperFrames project per layout, rendered with `paper-cut.js`;
2. the event list of `sfx_synth`;
3. the `story_beats` of the mute review.

Before anything is rendered, the compiler checks the scene for the defects of the first
production that a description can reveal (§ 5 of the porting plan, the "traps").

## Where the format comes from

The format is derived from **two real requests**, nothing else:

- **U1 — the factory**: a 20 s wordless vertical short (1080×1920). A shutter opens, a worker
  pushes a sun out along a conveyor, another paints the sky band by band with a pole roller, a
  crane lifts the sun into the sky, the workers admire it; the green worker steps back, bumps a
  crate, the star jar on it falls on his side, the stars burst out, night falls, the workers
  look at each other and shrug. Hand-written reference implementation, three standing workers,
  one camera with key frames, sound events tied to a dictionary of moments.
- **U2 — the re-sent history**: a 19.62 s insert under the narration of an explainer episode on
  the cost of agent loops. A courier pushes a cart of pages to a seated reader; at every turn
  the whole pile goes back, so turn *n* carries *n* sheets, *n* flips, *n* coins, and a strip of
  height *n* is pinned on a board; the strips form a triangle, compared with a flat row of
  dashed "intuition" strips. 16:9 and 9:16 on one timeline, timings from the voice.

Every element below names the case that requires it. An element required by neither is not in
the format (see "Excluded").

## 1. The document

A scene is one JSON document (`schemas/artifacts/paper_scene.schema.json`, `format:
"paper_scene/1"`).

| Element | What it holds | Required by |
|---|---|---|
| `id`, `title`, `brief` | Name and the text brief (handed to the mute reviewer) | U1, U2 (both briefs feed a blind review) |
| `duration` | Seconds, or a reference to a narration mark (`"p24.end"`) | U1 (20 s), U2 (19.62 s = end of the last phrase) |
| `marks` | Named intervals from the narration track (`"p21": [4.08, 8.48]`) | U2 (beats cut to phrase timings) |
| `insert.offset` | Where the insert sits in the episode (carried to the outputs) | U2 (an insert at 75.82 s, not a standalone clip) |
| `layouts` | One or more frames sharing the timeline: `aspect`, `width`, `height`, `ui_safe_bottom` | U1 (one 9:16), U2 (16:9 + 9:16 of the same timeline) |
| `style` | Paper background, grain seed and strength, vignette, fade-in from the blank page | U1 (the look), U2 (same style) |
| `text_allowed` | The only on-screen texts allowed | U2 (no number the narration does not say) |
| `order` | Draw order of every prop and character, back to front | U1 (back plane / front plane, sun behind the hills), U2 (desk in front of the seated reader) |
| `props` | Typed props from a named library (§ 2) with position, parameters, anchors and per-layout overrides | U1, U2 |
| `characters` | Paper puppets: `role`, `look`, `posture`, `scale`, `at`, initial `pose`, per-layout overrides (§ 3) | U1 (three workers), U2 (two roles, one seated) |
| `camera` | Key frames per layout: time, centre (or a prop anchor as `target`), zoom | U1 (tight / wide / tight), U2 (wide → push-in on the desk → pull back and pan to the board, differently framed per layout) |
| `actions` | Named actions from a library (§ 4), with actor, target, time interval, parameters, sounds and an optional beat | U1, U2 |
| `repeat` blocks (inside `actions`) | One action cycle run N times over N spans; `$n` is the iteration number | U2 (turn *n* carries *n* sheets, *n* flips, *n* coins) |
| `beats` | Story beats declared directly (`id, label, start, end, expected`, optional `phrase`) | U2 (its highlights are declared); U1 derives them from actions |
| `sound` | `peak_dbfs` (the level of the whole layer), seed, reverb, free-standing events | U1 (peak −1 dBFS), U2 (a ducked layer under the voice: one gain for the insert) |

Times are seconds, or a mark reference: `"p23"` (its start), `"p23.start"`, `"p23.end"`,
`"p23.start+0.4"`. Inside a `repeat`, `start` and `end` are fractions (0..1) of the iteration
span, so shorter spans make faster turns (U2: "same loop, faster").

Positions are in the layout's own pixels (the camera at zoom 1 shows the whole layout). A prop
or a character has one `at`, and `layouts.<name>.at` (or `.params`) overrides it for one layout:
U2's 9:16 puts the board above the lane instead of beside it. Actions never carry layout
coordinates: they name props and anchors, so one action list serves every layout.

## 2. Prop library

Every prop has `id`, `type`, `at`, optional `params`, `anchors` (`{name: [dx, dy]}`) and
`layouts`. Every prop exposes the anchors `center`, `top`, `bottom`, `left`, `right` (its
bounding box), plus the type's own. Channels are the values actions animate.

| Type | Draws | Own channels / anchors | U1 | U2 |
|---|---|---|---|---|
| `shape` | One torn paper piece: `rect`, `circle`, `ellipse`, `poly`, `star`, `gear`; `color`, `depth`, `alpha`, `rolls` (rotates with x) | `x y rot scale opacity color` | hills, floor, factory walls, crate, lever, chimney | wall, desk, chair, board, lamp |
| `group` | Several pieces moving together (children in local coordinates) | as `shape` | factory front, sign, crane cab | cart (box + rolling wheels), desk with lamp |
| `label` | Handwritten text (bundled Patrick Hand, accents included) | `opacity` | — | `tour 1` … `tour 5` |
| `path` | A pencil line through points, drawn progressively, optional fill and dashes | `draw fill` | pencil lines on the blank sky | the triangle outline and its tint |
| `glow` | Radial light | `alpha` | sun, lit windows, jar | — |
| `door` | A panel that slides up inside an opening | `open` | the shutter | the hatch |
| `container` | Glass jar with items inside (`star` / `coin`), lid | `count tip empty`; anchors `mouth`, `inside` | the star jar that tips over | the coin jar that fills and overflows |
| `stack` | A pile of sheets whose height is a count | `count flipped`; anchor `top` | — | sheets on the cart and on the desk |
| `bars` | Strips side by side with heights, optional dashes, labels under | `h_i reveal_i`; anchors `slot_i`, `top_i` | — | the staircase of strips; the dashed ghost row |
| `bands` | Sky bands painted one after the other, alternate directions | `p_i` (painted fraction of band i) | the painted sky | — |
| `pole` | Telescopic pole with a roller head, held at the base | `base_x base_y tip_x tip_y`; anchors `base`, `tip` | the paint roller | — |
| `sun` | Paper sun with rays, spiral and glow, rolls along x | `power dim` | the sun | — |
| `conveyor` | Belt, legs and rollers moved by an offset | `offset` | the conveyor | — |
| `crane` | Mast, jib, trolley, cables and hook | `trolley hook_y hook_open`; anchor `hook` | the crane | — |
| `burst` | Particles that leave a source in a column, hold, then spread to their places | `t0` (start), seeded per particle | the stars | — |
| `curtain` | A torn night sky coming down | `y` | night falls | — |
| `tint` | Multiply tint over the frame + lit windows | `night` | night falls | — |
| `smoke` | Puffs rising from a point (idle, seeded) | — | chimney smoke | — |
| `scatter` | Seeded scatter of small pieces in a rectangle | — | flowers, bricks | — |

A type, a parameter, an anchor or a channel name that is not in the library is an **error that
lists the known names**.

## 3. Characters

The paper puppet of lot A (`PaperCut.puppet`), on the same 16-point pose:

- `look`: `skin`, `shirt`, `overall`, `brow`, `hat` (colour, or `false`), `mustache` (U1: three
  workers, one with a moustache), `glasses` (U2: the reader's round glasses).
- `posture`: `standing` (U1, U2's courier) or `seated` — legs bent under a seat (U2's reader).
- `role`: a short name of what the character stands for (U2: "the agent loop", "the model"),
  carried into the beats so a blind reviewer can be asked "who does what".
- `scale`, `at: [x, ground_y]`, initial `pose` (arms, lean, turn, gaze, mouth, brow, shrug,
  open hands).

Character anchors: `hand_L`, `hand_R`, `head`, `center`, `feet`.

## 4. Action library

Common fields: `do` (the verb), `actor`, `target` (`"prop"` or `"prop.anchor"`), `start`,
`end`, verb parameters, `sound` (list), `beat` (optional). Channels are driven by at most one
action at a time; a channel keeps its last value until the next action that drives it, and
every action starts from the value the channel has at its start (so key values inherit).

| Verb | Does | U1 | U2 |
|---|---|---|---|
| `pose` | Eases body and face values (arms, lean, turn, gaze, mouth, brow, shrug, open hands) | all key poses of the workers | strain (lean, raised brows), mouth "o", hands on the desk |
| `shrug` | The shrug pose (arms out, palms up, brows up, wavy mouth) | the ending | the courier's shrug |
| `walk_to` | Walks to an x (number or anchor) with a walk cycle | to the lever; stepping back into the crate | — (the courier always pushes) |
| `push` | Moves a prop along x; the actor follows behind with hands on it | the sun pushed out | the cart, to the desk and back |
| `reach` | One hand goes to a target anchor (two-segment arm) | the hand on the lever | pinning a strip; the reader's hand on the pile |
| `point_at` | Arm stretched towards a target | — | pointing at the flat row, then the triangle |
| `look_at` | Turns head and gaze towards a target | — | the reader glances at the jar |
| `wave` | Oscillating arm around its current pose | "come on" beckoning, "up, up" signalling | — |
| `clap` | Both hands clap | applause | — |
| `flip` | Flips N pages of a stack, gaze following | — | the reader re-reads the whole pile |
| `hold` | A prop's base follows the actor's hand | the pole held | — |
| `paint` | The held pole paints the bands one after the other | the sky | — |
| `move` | A prop moves to a target (anchor or point), eased | the sun goes up, then sets | — |
| `attach` | A prop follows another prop's anchor | the sun on the hook | the pile riding the cart |
| `animate` | Eases any library channel of a prop | shutter, crane, lever, night, sun power | strip heights, ghost strips, triangle line and fill, hatch |
| `shake` | Damped shake | the crate when bumped | — |
| `tip_over` | A container falls to one side, lid off, items out | the star jar | — |
| `drop_in` | N items fall into a container, one by one | — | coins into the jar |
| `overflow` | Items spill over the rim | — | the overflowing jar |
| `burst` | A burst prop leaves from a source anchor | the stars from the jar | — |
| `stack_add` | A new sheet travels from a source onto a stack | — | the sheet out of the hatch onto the cart |
| `layer` | Changes the draw order at a time | the sun moves behind the hills | — |

**Sound on actions.** `sound: [{kind, at | every | per_item, gain, pan, …}]` with the
`sfx_synth` kinds and parameters. `at` is an offset (s) from the action's start; `every` places
an event every period over the action; `per_item` places one per item the action counts (pages
flipped, coins dropped), and `pitch_step` (semitones) raises the pitch of each next item. U1
needs `at` and `every` (shutter clicks, crane ratchet, applause); U2 needs `per_item` and
`pitch_step` (one click per page, one marimba note per coin, pitch rising with the count).

**Beats.** An action may carry `beat: {id, label, expected}` (its window is the action's,
unless `start`/`end` are given): the factory's ten beats come out of its actions. A scene may
also declare `beats` directly, each with an optional `phrase` (a mark) it must lie inside:
U2's highlights.

## 5. Checks before the render

`validate` runs every check; a scene with an error does not compile. Each check comes with a
test that fails on a real defect and one where it stays silent on the healthy case.

| Check | Trap (§ 5) | Detects |
|---|---|---|
| `vocabulary` | — | unknown type, verb, parameter, anchor, channel, sound kind (lists the known names) |
| `references` | — | an actor, target or ordered id that does not exist |
| `timing` | — | a time outside `[0, duration]`, `start > end`, an unknown mark, a sound after the end |
| `limb_overlap` | — | two actions driving the same channel (same arm, same face, same position) at once |
| `jump` | 1 | a value that jumps when an action takes over or lets go (a push from where the actor is not, an oscillation released mid-swing, an attach before the contact) |
| `off_frame` | 5 | the place of an action outside the current camera frame of a layout |
| `contact` | 6 | a hand or hook that does not reach what it grabs, pushes, pins or hooks |
| `zoom_speed` | 7 | a strong zoom change (ratio ≥ 1.25) in less than 1.2 s |
| `ui_safe_bottom` | 8 | the place of an action in the bottom band of a 9:16 layout (declared per layout; required for 9:16) |
| `beat_in_phrase` | U2 | a beat that does not lie inside the phrase it illustrates |
| `text_policy` | U2 | an on-screen text that is not in `text_allowed` |

Traps 2 (a look carried by the pupils only), 3 (an arm towards an object reads as pushing it)
and 4 (an eased-out path makes the object already far on the second frame) are judged on the
image: they stay with the mute review, and the skill says so.

## 6. Excluded (required by neither case)

- Free code or expressions in the scene: the scene is data; motion comes from the library.
- Characters other than the paper worker puppet, and mocap clips (`InkPuppet.choreograph`
  remains available outside the format).
- Per-iteration value tables in `repeat` (`$n` alone covers U2).
- Hard cuts between shots: both cases use one camera with moves.
- Dialogue, lip-sync, subtitles, music: U1 is wordless, U2 plays under its own narration and bed.
- U1's continuous room ambience (filtered noise + hum): `sfx_synth` has no such kind.
- Sound pan that follows an object: both cases set pan by hand.
- Automatic recomposition from 16:9 to 9:16: per-layout positions are explicit.
- Decorative micro-motions of the reference (sun sway on the hook, sun bobbing in the sky).

## 7. The three outputs (contract)

`compile` resolves marks and repeats, runs the checks, and writes into an output folder:

1. **`<layout>/`** for each layout: `index.html` (the resolved scene embedded as data, one
   `PaperCut.drive` timeline, `data-duration` = the scene duration, `data-width/height` = the
   layout), `ink-theater.js`, `paper-cut.js`, `paper-scene.js` (byte copies of the repository
   engine), `gsap.min.js` (a link to the repository's vendored copy, never a new copy) and the
   font when labels are used. Rendered by `npx hyperframes render`.
2. **`sfx_events.json`**: `{duration_seconds, peak_dbfs, seed, reverb_mix, events}` — the
   input of `sfx_synth`, every event placed by the resolved time of its action.
3. **`story_beats.json`**: `{brief, beats}` — `[{id, label, start, end, expected}]`, the input
   of `mute_review` (`sheets` and `verify`) and of `edit_decisions.metadata.story_beats`.

Plus `report.json` (every check, with what it measured) and `scene.resolved.json`.

Coherence is by construction: the three outputs read the same resolved action times. The
compiler is deterministic: the same scene gives the same bytes. The motion is evaluated by one
set of rules written twice (Python for the checks, JavaScript for the render); a test runs the
JavaScript evaluator on the compiled factory and compares it with Python's values.

`render` (render machine) runs `hyperframes render` per layout, then `sfx_synth` on
`sfx_events.json` and muxes the WAV under each video. The mute review is not optional: the
compiler's beats go to `mute_review sheets`, then to a blind reviewer.
