# Mute Review — Meta Skill

## When to Use

After a render, when the story must read **without sound and without words**: silent or wordless
pieces, character animation, any video whose brief lists story beats. The person who made the video
knows what each gesture means and cannot see it fresh; a separate reader, given only the images, can.
On a real 20 s paper-cut short, this review found the most serious defect — an accident that read as
the wrong character's fault — which the author had not seen. (Do not give this paragraph to a
reviewer of that short: hand over the "Reviewer protocol" section only.)

Two roles, never the same agent:

| Role | Does | Must not |
|------|------|----------|
| **Orchestrator** (the agent that made the video) | Declares the beats, builds the sheets, hands over the packet, runs the verifier | Read the sheets for the reviewer, hint at the answer, pass the script or code |
| **Mute reviewer** (a fresh agent) | Looks at the sheets, describes, then judges each beat | Hear the sound, see the script, the scene code or the edit decisions |

## Orchestrator protocol

1. **Declare the beats** in `edit_decisions.metadata.story_beats` (or a JSON file):
   `[{id, label, start, end, expected}]`, times in seconds, `start == end` for a point beat (a look).
   `expected` says what a viewer must *see*, including who does it ("the child, not the dog, spills the cup").
   Once declared, `final_review` records `checks.story_check` as `not_checked` and cannot `pass` until
   the mute review is folded in.
2. **Build the sheets**: `mute_review` tool, `operation: "sheets"`, with `input_path`, `output_dir` and
   `beats`. Result: sheets `sNN.png` (2 s each, 4 frames/s, 4×2 grid, cells 405×720 for 9:16), each cell
   labelled `#<cell>  <time>s` over its sheet id; dense sheets `d_<beat>_<n>.png` at 8 frames/s around every beat
   shorter than 0.8 s; and `sheets.json`, the index the verifier checks citations against. An extraction
   failure fails the tool — never accept a partial set.
3. **Hand over the packet** to a fresh agent: the brief, the beat list, the sheets (PNG) and this skill's
   "Reviewer protocol" section. Nothing else: no audio, no script, no scene code, no file names that
   reveal a version or a known defect (rename the folder if needed).
4. **Verify**: `mute_review` tool, `operation: "verify"`, with `beats` (all the declared beats),
   `review` (the reviewer's JSON), `sheet_index` (the **path** of the `sheets.json` the tool wrote) and
   `final_review_path`. The verifier writes `checks.story_check` and folds the status:
   - review not run, review breaking `mute_review.schema.json`, or reviewer not blind → `not_checked`
     (never `pass`);
   - `sheets.json` not written by the tool, sheet images missing next to it, or cut from another
     video (sha256 against `video_path`, default the final review's `output_path`) → `not_checked`;
   - a beat without a verdict, or a declared beat left out of the review → `not_checked`, `revise`;
   - a verdict citing a sheet or a cell that does not exist, or only cells outside the beat window
     (±0.5 s), or with no description, or a readable beat with no `who_does_what` → rejected, `revise`;
   - any `partiel` → `revise`; any `absent` or `contradicts` → `fail` (`recommended_action: re_author`).
   The story check only tightens the final review: it never lifts a `fail` or `revise` set elsewhere.
5. **Fix and re-review** with a *new* blind reviewer (the previous one has seen the old cut). A new
   review replaces the previous story issues.

**Limits, said plainly.** The reviewer's independence (no sound, no script or code, did not make the
video) is *self-declared*: the verifier reads the declaration, it cannot prove it. And the substance of
the judgement — what is visible, whether it matches `expected` — belongs to the reviewer: the verifier
checks the form of the answer (every beat, real cells, a description, who does what), not whether the
reviewer saw right. A clean `pass` means "a blind reader found every beat readable", nothing more.

## Reviewer protocol (give this section to the mute reviewer)

You receive a brief, a list of story beats and contact sheets. You get **no sound**, no script, no code.
You did not make this video. Your job is to say what a viewer actually sees, not what the brief hopes.

1. **Look at every sheet in time order first**, without the beat list in mind. Each cell is labelled
   `#<cell>  <time>s` with the sheet id below it (the sheet id is also the file name); cells are numbered left to right, then top to bottom.
2. **For each beat, describe before you judge.** Find the cells inside the beat window. Write what is
   visible there (`observed`), in plain words, as if to someone who cannot see the image.
3. **Name who does what** (`who_does_what`): for every action, the character or object that performs it,
   identified by what you can see (colour, position, prop) — "man in the grey coat raises his arm",
   "cup tips toward the left". Attribution errors are the most damaging story defects: an accident
   that lands on the wrong character changes whose fault it is. Check where objects fall, which side they leave from,
   who is facing what.
4. **Judge**, comparing what you described with the beat's `expected` text:
   - `lisible` — a viewer gets it without help, and it is what `expected` says;
   - `partiel` — it is there but ambiguous, too fast, too small or off-frame;
   - `absent` — not visible in the cells;
   - `contradicts` — clearly visible, but it tells something else than `expected`. **You must choose
     `contradicts` as soon as who does it, or the side or direction (where an object falls, where
     something comes from, who is hit), differs from `expected`** — even if everything is perfectly
     readable. Do not soften it into `partiel`: a viewer will understand the wrong story.
5. **Cite evidence**: at least one `{sheet, cell}` inside the beat window. Only cite cells that exist.
6. **Gestures between cells**: at 4 frames/s a gesture under ~0.8 s can fall between two cells. Before
   calling a short beat `absent`, check its dense sheet (`d_<beat>_<n>`). If you still suspect it is
   hidden between frames, say so in `between_cells_risk` rather than guessing.
7. **Anything else** that breaks the story (an object popping, a character jumping, a prop off-frame):
   add it to `other_findings` with sheet and cell.

Return JSON (schema `schemas/artifacts/mute_review.schema.json`):

```json
{
  "version": "1.0",
  "reviewer": {"id": "<your id>", "audio_access": false, "authored_video": false, "saw_script_or_code": false},
  "verdicts": [
    {
      "beat_id": "ball_dropped",
      "observed": "Cells #3-#5: the dog in the blue collar opens its mouth; the ball bounces once on the left of the bench.",
      "who_does_what": [{"who": "dog (blue collar)", "does": "drops the ball"},
                        {"who": "ball", "does": "bounces to the left of the bench"}],
      "verdict": "lisible",
      "evidence": [{"sheet": "s04", "cell": 3}, {"sheet": "s04", "cell": 5}]
    }
  ],
  "other_findings": []
}
```

Be honest about the declaration: if you heard the sound or saw the script, say `true` — the review
will not count, which is the correct outcome.
