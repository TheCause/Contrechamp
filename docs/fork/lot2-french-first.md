# Lot 2 — French first (fork)

Lot 1 made the final review measure instead of assume. Lot 2 makes a French
narrated video right by construction: the words on screen are the words of the
approved script, the voice says only that script, and the end of the video is
heard, not guessed.

## Part 1 — adopted from open upstream pull requests

Cherry-picked with their authors, then fixed until a real render passed:

| Upstream PR | What it fixes | Fork additions |
|---|---|---|
| #598 | caption word spacing, `staticFile()` `public/` prefix (every Remotion caption render failed), page breaks | sentence-end and SRT page breaks, French punctuation, OCR read-back of burned captions, FFmpeg fallback that says so |
| #577 | `audio_mixer` extract forced 16 kHz mono | real-ffmpeg test of the extracted file |
| #601 | the spending cap had no caller | fails closed, governs every paid estimate whatever the runtime, cap mode names the ceiling, refused entries closed |
| #608 (VoxCPM2 part) | local VoxCPM2 TTS on MLX | official engine (`make setup-voxcpm2`, or `VOXCPM2_PYTHON`), named local voices, `mode=ultimate` |

## Part 2 — tools from a channel's production method

Each tool ships with tests that fail on the real defect and stay silent on the
healthy case; the protections were mutated one by one, then reviewed
adversarially and replayed on a real render.

| Tool | Where | Rule it enforces |
|---|---|---|
| Script timing | `lib/script_timing.py`, `tools/subtitle/script_timing.py` | screen words come from the script, times from the voice; skipped passages are gaps, added words are extras, a script not said is never `pass` |
| Narration by chunks | `lib/narration.py`, `tools/audio/narrate_script.py` | the voice speaks only the text approved at the `script` stage; ~75-word chunks cut between sentences; stable ids; one chunk regenerated alone, as a new take; each chunk transcribed and checked |
| Named voices | `tools/audio/voxcpm2_tts.py` | `voices/<name>.wav/.txt/.json`, gitignored: a voice reference never enters the repository |
| Last sentence heard | `lib/render_checks.check_last_sentence`, final review | the end of the final mix is transcribed and must contain the last sentence; the last word must not sit 20 dB under the mix |
| Loudness | `lib/render_checks.loudness`, final review | integrated loudness under `review.loudness_min_lufs` (-26) is too quiet |
| Key-phrase cards | `lib/key_phrases.py`, `KeyPhraseCard` (Explainer overlay `key_phrase`) | a card is an exact script sentence, held 2 s, read back by OCR; captions under a card are removed and the page before it stops |
| Chapters | `lib/chapters.py` | chapter times on the final video, shifted by inserts; YouTube rules reported |
| Credits | `lib/credits.py`, final review | every third-party asset has a read licence and its credit; non-commercial is a warning until monetized |

## Calibrations measured, not guessed

- A healthy French last word sits -4.9 to 12.5 dB under the mix (15 real
  sentences); a fade that swallowed it, 35.7 dB. First threshold tried (8 dB)
  would have flagged 5 of 15 healthy endings.
- The loudness floor sits under broadcast R128 (-23 LUFS); a first -18 floor
  flagged compliant mixes.
- French transcripts split elisions and inversions (`l 'intention`,
  `passe -t -il`) and write spoken numbers in digits: before handling them, a
  correct narration scored 0.78 fidelity on its own script.

## Real run (render machine, 0 €)

A 35 s French explainer with a cloned voice: the narration refused a text
edited after approval; chunks heard at fidelity 1.0; the final review passed
the healthy render and flagged a copy with the voice cut under a noise bed
(heard: a hallucinated "Sous-titres réalisés par l'Amara.org") and a copy with
a 1.3 s fade.

## VoxCPM2 as the default French voice

- `make setup-voxcpm2` installs the official engine in `.venv-voxcpm` (Python
  3.10-3.12, `voxcpm` 2.0.3); `voxcpm2_tts` finds it there without any
  variable. `VOXCPM2_PYTHON` still wins, for an engine installed elsewhere.
  Code and weights are Apache-2.0 (`openbmb/VoxCPM2`, French among 30 languages).
- `narrate_script` uses VoxCPM2 when no engine is named. Without it, it
  narrates with Piper and says so under `data.fallback`; with neither, it
  refuses and names both. An engine named explicitly is never swapped.
- `CONTRECHAMP_NARRATION_VOICE` (per machine, in `.env`) names the voice used
  when none is given; the result reports it under `data.voice`.
- The rest of #608, MOSS-TTS Nano (100M, Apache-2.0, MLX only), is not taken:
  a second cloning engine for a need VoxCPM2 already covers.

## Known limits

- A single dropped word (a lost negation) stays under every threshold:
  listening to the narration remains part of the job.
- Human approval is still a checkpoint the agent writes; the fingerprint only
  proves the text did not change since.
- `caption_review` samples 4 frames; subtitle_check does not see captions
  passed as Remotion props.
