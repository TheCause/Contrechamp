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

**A layout moves things; it does not change what happens or when.** One sound track and one beat
list serve every layout (U2: the 16:9 and the 9:16 play under the same voice). The compiler
compiles each layout, then compares, action by action, the counted items (pages, coins,
particles) and the computed ends: a layout override that changes them (a burst with fewer
particles, a band more to paint) is a `timing` error. The alternative — one sound and one beat
list per layout — was rejected: the narration is the same for both cuts, and a 9:16 that tells a
different story from its 16:9 is a defect, not a feature.

**Values are untrusted.** Every key that becomes a folder or an identifier (scene id, layout,
mark, anchor, channel, repeat, action and beat ids) is a plain name `^[A-Za-z0-9_-]+$`; every
colour is `#rrggbb` or `#rgb` (expanded); every number is finite; counts, periods, pitch steps
and the duration have sanity bounds (no value can make the compiler loop for minutes). The
compiled data is embedded with `<`, `>` and `&` escaped, and no name of the scene appears outside
it. A scene that still breaks the compiler fails closed (`vocabulary` error), it never passes.

## 2. Prop library

Every prop has `id`, `type`, `at`, optional `params`, `anchors` (`{name: [dx, dy]}`) and
`layouts`. Every prop exposes the anchors `center`, `top`, `bottom`, `left`, `right` (its
bounding box), plus the type's own. Channels are the values actions animate.

| Type | Draws | Own channels / anchors | U1 | U2 |
|---|---|---|---|---|
| `shape` | One torn paper piece: `rect`, `circle`, `ellipse`, `poly`, `gear` (`teeth`, `r_in`: U1's gear); `color`, `depth`, `alpha`, `rolls` (rotates with x: U1's sun, U2's cart wheels), `spin` (idle rotation: U1's gear turning all along, not a story action) | `x y rot scale opacity color` | hills, floor, factory walls, crate, lever, chimney, gear | wall, desk, chair, board, lamp |
| `group` | Several pieces moving together (children in local coordinates) | as `shape` | factory front, sign, crane cab | cart (box + rolling wheels), desk with lamp |
| `label` | Handwritten text (bundled Patrick Hand, accents included), `text`, `size`, `color`, centred | `opacity` | — | `tour 1` … `tour 5` |
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
| `smoke` | Puffs rising from a point (idle; four puffs at the chimney's rate): `rise`, `r0`, `r1`, `color` | — | chimney smoke | — |
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
| `vocabulary` | — | unknown type, verb, parameter, anchor, channel, sound kind (lists the known names); a bad value (name, colour, non-finite number, wrong type, out of bounds) |
| `references` | — | an actor, target or ordered id that does not exist |
| `timing` | — | a time outside `[0, duration]`, `start > end`, an unknown mark, a sound after the end; a layout that changes counts or durations |
| `limb_overlap` | — | two actions driving the same channel (same arm, same face, same position) at once |
| `jump` | 1 | a value that jumps when an action takes over (a push from where the actor is not, a pose with no duration) |
| `off_frame` | 5 | the place of an action outside the current camera frame of a layout; without any action, a character outside the frame at a camera key, or a prop outside its layout |
| `contact` | 6 | a hand or hook that does not reach what it grabs, pushes, pins or hooks — measured against the anchor and against the object's own box (a declared anchor floating away from the object cannot fake a contact) |
| `zoom_speed` | 7 | camera moves that fit in less than 1.2 s and add up to a scale change of ×1.25 or more, however holds or reversals cut them (a single move longer than 1.2 s is the slow change the trap asks for) |
| `ui_safe_bottom` | 8 | the place of an action, or a character at a camera key, in the bottom band of a 9:16 layout (declared per layout; required for 9:16) |
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
- Parameters of the first draft that neither case uses, removed after review: shape `star`,
  shape `stroke` / `stroke_width` / `fill: false`, smoke `count` / `rate` / `drift` (fixed to the
  chimney's values), label `align` / `rotate`.

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

## 8. Second pass — what the first real author of U2 could not express or make read

A separate agent wrote U2 with the skill alone, rendered it in both layouts, and two blind
reviewers read it (0 absent, 0 contradicted, but the idea came through only halfway). Each
addition below answers one of their findings, and nothing more.

| Addition | U2 finding |
|---|---|
| Arithmetic on `$n` in a repeat: numbers, `$n`, `+ - * /`, `()`, `min`, `max` — a closed parser, no `eval`, no names, calls, attributes or powers | the lean, the loop duration and the pitch grow with the turn; the author had to write turn 4 out in full |
| An action's `layouts.<name>` may change `target`, `to`, `from`, `dx`, `grip` only; a prop's `layouts.<name>` may also move a declared anchor and set `init` | the 9:16 needs another point (the board is above, not beside); timing and counts stay shared, so the same-story check still holds |
| `point_at` straightens the body, aims the whole arm, turns head and eyes; check `pointing` (≤ 10° off, held 0.4 s, ≥ 30° from vertical: a nearly vertical arm still read as a hand on the head on the re-render) | both reviewers read the final designation as a wave or a hand on the head, aimed at the hatch or the cart |
| `seated`: pelvis at seat height, thighs horizontal towards the first facing, knees bent, a stool | the reader looked standing and small, a leg sticking out from behind the desk |
| Check `min_size`: a counted object ≥ 24 px and ≥ 6 px thick on screen; coins scale with the jar width | sheets and coins were a few pixels in the wide shots: the counts, the core of the idea, did not read |
| Check: no beat starts during the opening fade (`style.fade_in`, 0 allowed) | the 0.2 s fade ate a third of the first beat |

**What changes for an existing scene:** a `point_at` now drives `lean`, `turn` and the gaze
(a `look_at` or `pose` on those during the point is a `limb_overlap` error); the pointing arm
must stay on target 0.4 s; a seated character's hands sit 4 units lower (seat drop 22, was
18) and a stool appears; coins are drawn at 0.36 × the jar width (unchanged for the default
50); a beat starting before `fade_in` (default 0.45 s) is an error; counted sheets thinner
than 6 px or coins under 24 px on screen are errors.
