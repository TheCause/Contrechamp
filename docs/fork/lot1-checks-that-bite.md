# Lot 1 — Checks that bite

*Reference version (English). French translation: [`lot1-checks-that-bite.fr.md`](lot1-checks-that-bite.fr.md).*

*Fork `TheCause/OpenMontage`, 27 Sept 2026. Source: a test bench of 3 real productions (vertical
explainer, silent episode with a quote card, stills animatic), zero spend. 13 false "green"
results were found, all in `video_compose`'s final review (`_run_final_review`) or in tool
statuses.*

## The problem in one sentence

The final review **states** things it never **measures**: `unreadable_text`,
`broken_overlays` and `missing_assets` were hard-coded `False`; `music_present` became `True` as
soon as the mean level exceeded −50 dB; any issue outside a list of six keywords left the status at
`pass`; the black-frame test (PNG smaller than 2 KB) could never fire. Two different renders — one
broken, one good — received an **identical** `visual_spotcheck`.

## Principle

> A check that did not run **says so**. A check that ran shows **what it read**.
> `pass` only exists when every expected check ran and no issue is open.

## What changes

1. **Three states instead of two.** Visual/audio findings are `true | false | null`; `null` means
   *not checked*, with the reason in `not_checked: {field: reason}`. No reassuring defaults.
2. **One frame per edit segment** (middle of each `edit_decisions.cuts` entry, capped by
   `review.max_sampled_segments`), instead of 4 frames at 10/35/65/90 % that land anywhere.
3. **On-screen text is read, not assumed** (local OCR, tesseract). Every sampled frame is read
   through several preprocessing variants (plain gray, thresholds from `review.ocr_thresholds`).
   Glued words (a token of `review.glued_word_min_len`+ letters, or sentence punctuation with no
   space after it) make `unreadable_text: true`. Optional `edit_decisions.metadata.expected_text`
   (`start_seconds`, `end_seconds`, `text`, `exact`) is compared to what was read — `exact: true`
   for text that must not change by a character. The reading is written to `ocr_readings`.
   OCR language follows `edit_decisions.metadata.language` (`fr` → `fra`, `en` → `eng`, …).
4. **Seams and loop.** On continuity cuts (same source on both sides, or `cut.continuity: true`):
   SSIM (ffmpeg `ssim`) and mean-luminance jump across the cut. If
   `edit_decisions.metadata.loop` is true, the same measure between the last and first frames.
   Ordinary hard cuts between different shots are not measured.
5. **Music is measured**: share of digital silence (`silencedetect`) in the audio. A music bed
   leaves none between phrases. Only an issue when music was planned.
6. **Honest status.** `pass` = every expected check ran, no issue open. `revise` = at least one
   open issue (action `revise_edit`, or `re_render` for the historical critical keywords).
   `fail` = invalid container. A skipped transcript comparison only blocks when narration was
   planned; otherwise it is recorded under `not_checked`.
7. **Tool statuses that no longer lie.** `piper_tts` is `available` only with the binary (on PATH
   *or* next to the running interpreter) **and** at least one voice model
   (`$PIPER_VOICES_DIR`, `models/piper/`, `~/.piper/models`); `pixabay_music` is `degraded`
   (it scrapes a site that answered HTTP 403); preflight warns when ffmpeg lacks the `drawtext` /
   `subtitles` filters or when tesseract is missing. Tools expose `status_reason()`.

## Configuration

All thresholds live in `config.yaml` under `review:` (validated with bounds in
`lib/config_model.py` → `ReviewConfig`). Set `review.ocr_required: false` to run without
tesseract: the text checks are then recorded as not checked instead of blocking `pass`.

## Requirements

- **tesseract** (+ language data for your projects) for the text checks — optional with
  `ocr_required: false`. `make setup` checks for it and prints the install command per OS.
- **ffmpeg with libass + freetype** for subtitle burn-in and FFmpeg text cards (Homebrew's plain
  `ffmpeg` formula lacks them; use `ffmpeg-full`).

## Proof required (the checking rule)

Every check ships with a test that **must fail** on a real defect and a test where it **must stay
silent** on the healthy case; thresholds were mutated to confirm the tests bite. Fixtures are
synthetic (Pillow frames + ffmpeg): no production content enters the repository.

| Check | Must fail on | Must stay silent on |
|---|---|---|
| OCR text | caption with glued words, incl. grey + cyan text on a translucent box | same text properly spaced |
| OCR text | card whose text differs by one word (exact mode) | exact card |
| Seam | freeze frame 9 luminance points darker | exact freeze frame |
| Seam | freeze frame from another shot | same shot |
| Loop | last frame ≠ first | clean loop |
| Music | narration alone, gaps at digital silence | narration + bed |
| Status | one non-"critical" issue | render without issues |
| `null` | tesseract missing → `null` + reason, status `revise` | `ocr_required: false` |

## Result on real renders (27 Sept 2026, outside the repository)

Same renders, original review vs fork review:

| Real render | Original review | Fork review |
|---|---|---|
| Explainer, captions with glued words | `pass`, `unreadable_text: false`, `music_present: true` | `revise`, `unreadable_text: true`, `music_present: false` |
| Same explainer, captions fixed | `pass` (same verdict as the broken one) | `unreadable_text: false` |
| Silent episode, darkened freeze frame | `revise` (for "silence"), no seam measured | seam KO (SSIM 0.928, luminance +11), loop KO |
| Silent episode, wrong freeze frame | `revise` (same verdict) | seam KO (SSIM 0.826), loop KO |
| Silent episode, good render | `revise` (same verdict) | seam OK (0.991), loop OK (0.979) |

The original review gave **the same verdict** to the broken and the good render; the fork
separates both pairs. Healthy renders still end at `revise` for known reasons outside lot 1:
"silence" on an intentionally silent video (silent profile, lot 3), and a subtitle file not
found through a relative path (lot 2).

Empty checks found **in my own work** during the lot, and fixed: a test video whose image switch
did not happen at the declared time (seam tests passed on nothing); a glued-word threshold of 26
letters while real defects were 22 and 24; an "original code" baseline that actually ran the new
code. The 140 OCR threshold survives mutation (not required for detection) and is kept for
reading completeness — stated in the code.

## Out of lot 1

`CaptionOverlay` fix and captions aligned on the script (lot 2); silent / series / sacred-text
profiles (lot 3); `approval_policy` in the decision-log schema and a single edit file (lot 4).
