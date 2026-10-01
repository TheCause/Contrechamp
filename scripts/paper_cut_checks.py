"""Render checks for a paper-cut (Ink Theater) composition.

    python scripts/paper_cut_checks.py ink-theater/examples/paper-cut-sunrise --out /tmp/pc-checks

Three checks, each rendered by `hyperframes snapshot` in a FRESH browser:

  (b) determinism  — the frame at T rendered twice (two processes) has the same sha256;
  (c) seek-safety  — the frame at T reached after seeking to a LATER time first
                     (one page: --at LATE,T) equals the frame at T reached directly;
  (a) review sheet — 8 frames at 4 fps per 2-second window, tiled 4x2 at 405x720,
                     to compare by eye with a reference sheet. Never auto-passes:
                     its verdict is always `not_checked` until a human/agent looks.

A check that could not run reports `not_checked`, never `pass`. Exit code 1 if
any of (b)/(c) fails, 2 if one could not run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

NPX = shutil.which("npx") or "npx"
# npm spec of the CLI; pin it (e.g. hyperframes@0.8.105) to compare renders across days.
HF_SPEC = os.environ.get("CONTRECHAMP_HYPERFRAMES_SPEC", "hyperframes")

# Runtime sources of non-determinism that a paper-cut engine/scene must never use.
FORBIDDEN_JS = re.compile(r"\bMath\.random\s*\(|\bDate\.now\s*\(|\bperformance\.now\s*\(|\bnew\s+Date\s*\(")


def forbidden_calls(source: str) -> list[str]:
    """Return the offending snippets (empty list = clean)."""
    return [m.group(0) for m in FORBIDDEN_JS.finditer(source)]


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compare(a: Path | None, b: Path | None) -> dict:
    """Verdict for two renders of what must be the same frame."""
    if a is None or b is None or not Path(a).is_file() or not Path(b).is_file():
        return {"verdict": "not_checked", "reason": "missing frame", "a": str(a), "b": str(b)}
    ha, hb = sha256(a), sha256(b)
    return {"verdict": "pass" if ha == hb else "fail", "a": str(a), "b": str(b), "sha_a": ha, "sha_b": hb}


def snapshot(project: Path, times: list[float], out: Path) -> list[Path]:
    """Capture frames at `times` (in that order, one page) — returns paths in order."""
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HYPERFRAMES_NO_TELEMETRY="1")
    cmd = [NPX, "--yes", HF_SPEC, "snapshot", str(project), "--at", ",".join(f"{t:g}" for t in times),
           "--no-end", "--describe", "false", "-o", str(out)]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"snapshot failed ({proc.returncode}): {proc.stderr[-2000:] or proc.stdout[-2000:]}")
    found = sorted(out.glob("frame-*-at-*.png"))
    if len(found) != len(times):
        raise RuntimeError(f"expected {len(times)} frames in {out}, found {len(found)}")
    return found


def review_sheet(frames: list[Path], dest: Path, cols: int = 4, tile=(405, 720)) -> Path:
    from PIL import Image
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile[0], rows * tile[1]), "white")
    for i, f in enumerate(frames):
        im = Image.open(f).convert("RGB").resize(tile, Image.LANCZOS)
        sheet.paste(im, ((i % cols) * tile[0], (i // cols) * tile[1]))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--at", type=float, default=2.5, help="frame time for (b) and (c)")
    ap.add_argument("--late", type=float, default=4.5, help="time visited before seeking back, for (c)")
    ap.add_argument("--sheets", default="0,2", help="start times of the 2-s review sheets (empty = none)")
    args = ap.parse_args(argv)

    report: dict = {"project": str(args.project)}
    try:
        first = snapshot(args.project, [args.at], args.out / "b1")[0]
        second = snapshot(args.project, [args.at], args.out / "b2")[0]
        report["b_determinism"] = compare(first, second)
    except Exception as e:  # noqa: BLE001 - report, never pass by default
        first = None
        report["b_determinism"] = {"verdict": "not_checked", "reason": str(e)}
    try:
        back = snapshot(args.project, [args.late, args.at], args.out / "c")[1]
        report["c_seek_back"] = compare(first, back)
    except Exception as e:  # noqa: BLE001
        report["c_seek_back"] = {"verdict": "not_checked", "reason": str(e)}
    sheets = []
    for s in [float(x) for x in args.sheets.split(",") if x.strip()]:
        try:
            fr = snapshot(args.project, [round(s + k * 0.25, 2) for k in range(8)], args.out / f"sheet{s:g}")
            sheets.append(str(review_sheet(fr, args.out / f"sheet_{int(s):02d}.png")))
        except Exception as e:  # noqa: BLE001
            sheets.append(f"not_checked: {e}")
    report["a_review_sheets"] = {"verdict": "not_checked", "reason": "compare by eye", "sheets": sheets}
    print(json.dumps(report, indent=2))
    verdicts = [report["b_determinism"]["verdict"], report["c_seek_back"]["verdict"]]
    return 1 if "fail" in verdicts else 2 if "not_checked" in verdicts else 0


if __name__ == "__main__":
    sys.exit(main())
