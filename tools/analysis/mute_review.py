"""Mute story review: contact sheets for a blind reader, and the verifier.

``sheets``: cut a render into contact sheets of 2 s at 4 frames/s (a 4x2
grid, cells 405x720 for 9:16), each cell labelled with its sheet, number and
time. Short story beats (under ~0.8 s) also get dense sheets at 8 frames/s,
because a brief gesture can fall between two cells at 4 frames/s. Frames are
extracted with ``frame_sampler`` (timestamps strategy). Every requested frame
must come back: a missing frame is an error, never a smaller sheet.

``verify``: check the blind reviewer's verdicts against the beats and the
sheet index (lib/mute_review.py) and, when given a final_review file, fold
the result into ``checks.story_check``.

The reading itself is done by a separate agent following
skills/meta/mute-review.md: without the sound, without the script or the
scene code, and not the agent that made the video.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

from lib import mute_review as mr
from tools.analysis.frame_sampler import FrameSampler
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolStability,
    ToolTier,
)

MANIFEST_NAME = "sheets.json"
SHEET_FILE = re.compile(r"^(s\d{2,}(_\d{3})?|d_[A-Za-z0-9_-]+_\d+)\.png$")


def _load_json_arg(value: Any, what: str) -> Any:
    """Inline object/list, or a path to a JSON file."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        path = Path(value)
        if not path.is_file():
            raise mr.MuteReviewError(f"{what} file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    raise mr.MuteReviewError(f"{what} must be an object, a list or a JSON file path")


class MuteReview(BaseTool):
    name = "mute_review"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "analysis"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = ["cmd:ffmpeg", "cmd:ffprobe", "python:PIL"]
    install_instructions = "Install FFmpeg (https://ffmpeg.org/download.html) and Pillow."
    agent_skills = ["ffmpeg"]

    capabilities = ["story_contact_sheets", "verify_mute_review"]
    best_for = [
        "checking that each story beat of a silent or wordless video reads on its own",
        "giving a blind reviewer timed evidence (sheet + cell) to cite",
    ]
    not_good_for = ["judging sound, narration or lip-sync", "long videos (one frame per 0.25 s)"]

    input_schema = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {"type": "string", "enum": ["sheets", "verify"]},
            "input_path": {"type": "string", "description": "sheets: the rendered video"},
            "output_dir": {"type": "string", "description": "sheets: where sheets + sheets.json go"},
            "beats": {
                "description": (
                    "Story beats {id, label, start, end, expected}, inline or a JSON file path "
                    "(a list, or an object with a 'beats' list). sheets: short beats get dense "
                    "sheets. verify: required."
                ),
            },
            "fps": {"type": "number", "default": 4},
            "dense_fps": {"type": "number", "default": 8},
            "seconds_per_sheet": {"type": "number", "default": 2},
            "columns": {"type": "integer", "default": 4},
            "rows": {"type": "integer", "default": 2},
            "cell_long_side": {"type": "integer", "default": 720},
            "short_beat_seconds": {"type": "number", "default": mr.SHORT_BEAT_SECONDS},
            "review": {
                "description": "verify: the reviewer's mute_review artifact (inline or path). Absent = not run.",
            },
            "sheet_index": {
                "type": "string",
                "description": "verify: path of the sheets.json written by the sheets operation (sheet images next to it).",
            },
            "video_path": {
                "type": "string",
                "description": "verify: the render the sheets were cut from (default: final_review output_path); its sha256 must match sheets.json.",
            },
            "final_review_path": {
                "type": "string",
                "description": "verify: final_review JSON to update with checks.story_check (written in place).",
            },
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=1024, vram_mb=0, disk_mb=500)
    idempotency_key_fields = ["operation", "input_path", "beats", "fps", "dense_fps"]
    side_effects = ["writes contact sheets and sheets.json to output_dir", "verify: rewrites final_review_path"]
    user_visible_verification = ["Open a sheet: every cell carries its time; dense sheets cover each short beat"]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        op = inputs.get("operation")
        start = time.time()
        try:
            if op == "sheets":
                result = self._sheets(inputs)
            elif op == "verify":
                result = self._verify(inputs)
            else:
                return ToolResult(success=False, error=f"Unknown operation: {op!r}")
        except mr.MuteReviewError as e:
            return ToolResult(success=False, error=str(e))
        result.duration_seconds = round(time.time() - start, 2)
        return result

    # ------------------------------------------------------------ sheets

    def _probe(self, path: Path) -> dict[str, Any]:
        proc = self.run_command([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "json", str(path),
        ])
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        if not streams:
            raise mr.MuteReviewError(f"no readable video stream in {path}")
        # The picture's own length: the container also counts a longer sound track.
        try:
            duration = float(streams[0].get("duration") or 0)
        except ValueError:
            duration = 0.0
        if duration <= 0:  # some containers (mkv, webm) give no stream duration: count frames
            proc = self.run_command([
                "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                "-show_entries", "stream=nb_read_frames,avg_frame_rate", "-of", "json", str(path),
            ])
            st = (json.loads(proc.stdout or "{}").get("streams") or [{}])[0]
            num, _, den = str(st.get("avg_frame_rate", "0/1")).partition("/")
            rate = float(num) / float(den) if den and float(den) else 0.0
            duration = int(st.get("nb_read_frames") or 0) / rate if rate else 0.0
        if duration <= 0:
            raise mr.MuteReviewError(f"no measurable video duration in {path}")
        return {"width": int(streams[0]["width"]), "height": int(streams[0]["height"]),
                "duration": duration}

    @staticmethod
    def sheet_id(t0: float) -> str:
        """``s04`` for a sheet starting at 4 s, ``s04_500`` at 4.5 s (always unique)."""
        whole, frac = divmod(round(t0 * 1000), 1000)
        return f"s{whole:02d}" if frac == 0 else f"s{whole:02d}_{frac:03d}"

    @staticmethod
    def plan_sheets(
        duration: float,
        beats: list[dict[str, Any]],
        fps: float = 4,
        dense_fps: float = 8,
        seconds_per_sheet: float = 2,
        cells: int = 8,
        short_beat_seconds: float = mr.SHORT_BEAT_SECONDS,
    ) -> list[dict[str, Any]]:
        """Sheets to build: regular ones every ``seconds_per_sheet``, then
        dense ones covering each short beat, padded on both sides to full sheets."""
        sheets: list[dict[str, Any]] = []
        n_regular = math.ceil(duration / seconds_per_sheet - 1e-9)
        for k in range(n_regular):
            t0 = k * seconds_per_sheet
            times = [round(t0 + c / fps, 3) for c in range(cells) if t0 + c / fps < duration]
            sheets.append({"id": MuteReview.sheet_id(t0), "kind": "regular", "times": times})
        for beat in beats:
            if beat["end"] > duration + 1e-6:
                raise mr.MuteReviewError(
                    f"beat {beat['id']!r} ends at {beat['end']:g}s, after the end of the video "
                    f"({duration:.3f}s): fix the beat list or the render")
            if not mr.is_short(beat, short_beat_seconds):
                continue
            # Frames covering the beat, then margins on both sides until the
            # last sheet is full (no half-empty dense sheet).
            first = math.floor(beat["start"] * dense_fps + 1e-9)
            last = math.ceil(beat["end"] * dense_fps - 1e-9)
            n_max = math.ceil(duration * dense_fps - 1e-9) - 1  # last frame index before the end
            count = last - first + 1
            extra = math.ceil(count / cells) * cells - count
            first -= math.ceil(extra / 2)
            last += extra // 2
            if first < 0:
                last, first = last - first, 0
            if last > n_max:
                first, last = max(0, first - (last - n_max)), n_max
            times = [round(k / dense_fps, 3) for k in range(first, last + 1)]
            for n in range(0, len(times), cells):
                sheets.append({
                    "id": f"d_{beat['id']}_{n // cells + 1}",
                    "kind": "dense",
                    "beat_id": beat["id"],
                    "times": times[n:n + cells],
                })
        return sheets

    def _sheets(self, inputs: dict[str, Any]) -> ToolResult:
        from PIL import Image, ImageDraw, ImageFont

        if not inputs.get("input_path"):
            return ToolResult(success=False, error="sheets needs input_path")
        video = Path(inputs["input_path"])
        if not video.is_file():
            return ToolResult(success=False, error=f"Input not found: {video}")
        fps = float(inputs.get("fps", 4))
        dense_fps = float(inputs.get("dense_fps", 8))
        sps = float(inputs.get("seconds_per_sheet", 2))
        cols, rows = int(inputs.get("columns", 4)), int(inputs.get("rows", 2))
        if round(sps * fps) != cols * rows:
            return ToolResult(success=False, error=(
                f"{sps} s at {fps} frames/s is {sps * fps:g} frames, not {cols}x{rows} cells"))
        beats = mr.load_beats(_load_json_arg(inputs["beats"], "beats")) if inputs.get("beats") else []
        out_dir = Path(inputs.get("output_dir") or video.parent / f"{video.stem}_sheets")
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            info = self._probe(video)
        except Exception as e:  # ffprobe error or garbage output
            return ToolResult(success=False, error=f"cannot read {video}: {e}")
        plan = self.plan_sheets(info["duration"], beats, fps, dense_fps, sps, cols * rows,
                                float(inputs.get("short_beat_seconds", mr.SHORT_BEAT_SECONDS)))
        times = sorted({t for s in plan for t in s["times"]})

        frames_dir = out_dir / ".frames"
        # A frame or a sheet left by an earlier run would pass for a fresh one.
        for stale in frames_dir.glob("frame_*.png"):
            stale.unlink()
        self._purge_sheets(out_dir)
        sampled = FrameSampler().execute({
            "input_path": str(video), "strategy": "timestamps", "timestamps": times,
            "output_dir": str(frames_dir), "format": "png",
        })
        if not sampled.success:
            return ToolResult(success=False, error=f"frame extraction failed: {sampled.error}")
        by_time = {round(f["timestamp_seconds"], 3): Path(f["path"]) for f in sampled.data["frames"]}
        missing = [t for t in times if t not in by_time or not by_time[t].is_file()]
        if missing:
            return ToolResult(success=False, error=(
                f"{len(missing)} of {len(times)} frames could not be extracted "
                f"(first at {missing[0]:.3f}s); no sheet written"))

        long_side = int(inputs.get("cell_long_side", 720))
        w, h = info["width"], info["height"]
        cw, ch = (round(long_side * w / h), long_side) if h >= w else (long_side, round(long_side * h / w))
        gap = 4
        try:
            font = ImageFont.load_default(size=max(14, ch // 26))
        except TypeError:  # Pillow < 10.1: fixed-size bitmap font
            font = ImageFont.load_default()
        manifest: dict[str, Any] = {
            "generator": mr.GENERATOR,
            "video": video.name,
            "video_sha256": mr._sha256(video), "duration": round(info["duration"], 3),
            "fps": fps, "dense_fps": dense_fps, "seconds_per_sheet": sps,
            "grid": f"{cols}x{rows}", "cell_size": f"{cw}x{ch}",
            "cell_numbering": "1..N, row-major, left to right then top to bottom",
            "sheets": {},
        }
        written: list[str] = []
        for sheet in plan:
            canvas = Image.new("RGB", (cols * cw + (cols - 1) * gap, rows * ch + (rows - 1) * gap), "white")
            draw = ImageDraw.Draw(canvas)
            cells = []
            for i, t in enumerate(sheet["times"]):
                try:
                    with Image.open(by_time[t]) as im:
                        frame = im.convert("RGB").resize((cw, ch), Image.LANCZOS)
                except Exception as e:
                    return ToolResult(success=False, error=f"frame at {t:.3f}s is unreadable: {e}")
                x, y = (i % cols) * (cw + gap), (i // cols) * (ch + gap)
                canvas.paste(frame, (x, y))
                # time and cell first (never cut), sheet id below, shrunk to fit
                stamp = f"{t:.2f}" if abs(t * 100 - round(t * 100)) < 1e-6 else f"{t:.3f}"
                lines = [(f"#{i + 1}  {stamp}s", font),
                         (sheet["id"], self._fitting_font(draw, sheet["id"], cw - 28, font))]
                top = y + 8
                boxes = []
                for text, f in lines:
                    boxes.append((text, f, top, draw.textbbox((x + 8, top), text, font=f)))
                    top = boxes[-1][3][3] + 6
                draw.rectangle((x + 2, y + 4, max(b[3][2] for b in boxes) + 6, top), fill=(0, 0, 0))
                for text, f, ty, _ in boxes:
                    draw.text((x + 8, ty), text, fill=(255, 255, 255), font=f)
                cells.append({"cell": i + 1, "t": t})
            path = out_dir / f"{sheet['id']}.png"
            canvas.save(path)
            written.append(str(path))
            entry = {"path": path.name, "kind": sheet["kind"], "cells": cells}
            if sheet.get("beat_id"):
                entry["beat_id"] = sheet["beat_id"]
            manifest["sheets"][sheet["id"]] = entry
        manifest_path = out_dir / MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        return ToolResult(
            success=True,
            data={
                "output_dir": str(out_dir),
                "manifest": str(manifest_path),
                "sheet_count": len(plan),
                "dense_sheets": [s["id"] for s in plan if s["kind"] == "dense"],
                "frame_count": len(times),
                "sheets": manifest["sheets"],
            },
            artifacts=written + [str(manifest_path)],
        )

    @staticmethod
    def _purge_sheets(out_dir: Path) -> None:
        """Remove the sheets and index of an earlier run in ``out_dir``."""
        old = out_dir / MANIFEST_NAME
        names: set[str] = set()
        if old.is_file():
            try:
                names = {s["path"] for s in json.loads(old.read_text(encoding="utf-8"))["sheets"].values()}
            except (ValueError, KeyError, TypeError, AttributeError):
                names = set()
            old.unlink()
        for p in out_dir.glob("*.png"):
            if p.name in names or SHEET_FILE.match(p.name):
                p.unlink()

    @staticmethod
    def _fitting_font(draw: Any, text: str, max_width: int, font: Any) -> Any:
        """The largest default font (up to ``font``'s size) that fits ``max_width``."""
        from PIL import ImageFont

        size = int(getattr(font, "size", 0) or 0)
        while size > 10 and draw.textlength(text, font=font) > max_width:
            size -= 2
            font = ImageFont.load_default(size=size)
        return font

    # ------------------------------------------------------------ verify

    def _verify(self, inputs: dict[str, Any]) -> ToolResult:
        if not inputs.get("beats"):
            return ToolResult(success=False, error="verify needs the story beats")
        beats = _load_json_arg(inputs["beats"], "beats")
        review = _load_json_arg(inputs["review"], "review") if inputs.get("review") else None
        # Only the sheets.json file the sheets operation wrote is accepted (a
        # hand-typed index is refused by the verifier, as not_checked).
        index = inputs.get("sheet_index")
        final = None
        if inputs.get("final_review_path"):
            path = Path(inputs["final_review_path"])
            if not path.is_file():
                return ToolResult(success=False, error=f"final_review not found: {path}")
            final = json.loads(path.read_text(encoding="utf-8"))
        video = inputs.get("video_path") or (final or {}).get("output_path")
        story_check = mr.verify(beats, review, index, video)
        data: dict[str, Any] = {"story_check": story_check, "status": story_check["status"]}
        artifacts: list[str] = []
        if final is not None:
            from schemas.artifacts import validate_artifact

            updated = mr.apply_to_final_review(final, story_check)
            validate_artifact("final_review", updated)
            path.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
            data["final_review_status"] = updated["status"]
            artifacts.append(str(path))
        return ToolResult(success=True, data=data, artifacts=artifacts)
