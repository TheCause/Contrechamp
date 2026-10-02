"""Render checks for a paper-cut (Ink Theater) composition.

    python scripts/paper_cut_checks.py ink-theater/examples/paper-cut-sunrise --out /tmp/pc-checks

Probe times are spread over the WHOLE composition (default: 6 times at the centres
of 6 equal slices of data-duration, or --times). Each session is a fresh browser
(`hyperframes snapshot`) that visits the probe times in a given order:

  A  ascending   (forward seeks)        A2 ascending again (second process)
  B  descending  (backward seeks)       C  interleaved: first, last, second, ... (jumps both ways)

  (b) determinism — A and A2 give the same sha256 at every probe time;
  (c) seek-safety — B and C give, at every probe time, the same sha256 as A;
  (a) review sheets — 8 frames at 4 fps per 2-s window, tiled 4x2 at 405x720, to
      compare by eye with a reference sheet; always `not_checked` (a human looks).

A check can only `pass` on frames that prove the scene really rendered:
  - no frame may be (nearly) uniform: grayscale std-dev >= MIN_STD (a blank page,
    or a page whose scripts failed to load, is not a render);
  - the scene must move: every probe time gives a different image;
  - hyperframes must not report that seeks were skipped ("No player API");
  - the composition sources must not call Math.random / Date.now / performance.now.
A check that could not run is `not_checked`, never `pass`. Exit code: 1 if any
check fails, 2 if one could not run, 0 otherwise. The report records the
hyperframes version used.
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

# Calibrated on real 1080x1920 frames of paper-cut-sunrise (grayscale std-dev 35-51)
# versus a frame rendered with gsap unreachable / the opening fade fully on (std-dev 0.0).
MIN_STD = 8.0

# Runtime sources of non-determinism that a paper-cut engine/scene must never use.
FORBIDDEN_JS = re.compile(r"\bMath\.random\s*\(|\bDate\.now\s*\(|\bperformance\.now\s*\(|\bnew\s+Date\s*\(")
SEEK_SKIPPED = re.compile(r"No player API|seeks will be skipped", re.I)


def forbidden_calls(source: str) -> list[str]:
    """Return the offending snippets (empty list = clean)."""
    return [m.group(0) for m in FORBIDDEN_JS.finditer(source)]


def seek_warnings(output: str) -> list[str]:
    """Lines of hyperframes output saying the timeline was NOT seeked."""
    return [ln.strip() for ln in output.splitlines() if SEEK_SKIPPED.search(ln)]


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frame_std(path: Path) -> float:
    from PIL import Image, ImageStat
    return ImageStat.Stat(Image.open(path).convert("L")).stddev[0]


def blank_frames(paths: list[Path], min_std: float = MIN_STD) -> list[str]:
    """Frames too uniform to be a render (returned as 'name (std x.x)')."""
    out = []
    for p in paths:
        s = frame_std(p)
        if s < min_std:
            out.append(f"{Path(p).name} (std {s:.1f})")
    return out


def compare(a: Path | None, b: Path | None) -> dict:
    """Byte-level verdict for two renders of what must be the same frame."""
    if a is None or b is None or not Path(a).is_file() or not Path(b).is_file():
        return {"verdict": "not_checked", "reason": "missing frame", "a": str(a), "b": str(b)}
    ha, hb = sha256(a), sha256(b)
    row = {"verdict": "pass" if ha == hb else "fail", "a": str(a), "b": str(b), "sha_a": ha, "sha_b": hb}
    if ha != hb:
        row.update(pixel_diff(a, b))   # diagnostic only: the verdict stays byte-exact
    return row


def pixel_diff(a: Path, b: Path) -> dict:
    """How different two frames are (count of differing pixels, max channel delta, bbox)."""
    try:
        from PIL import Image, ImageChops
        ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
        if ia.size != ib.size:
            return {"pixels_differing": None, "note": f"size {ia.size} vs {ib.size}"}
        d = ImageChops.difference(ia, ib)
        gray = d.convert("L").point(lambda v: 255 if v else 0)
        return {"pixels_differing": gray.histogram()[255], "max_channel_delta": max(hi for _, hi in d.getextrema()),
                "bbox": d.getbbox()}
    except Exception as e:  # noqa: BLE001
        return {"pixels_differing": None, "note": str(e)}


def compare_sessions(ref: dict, other: dict, times: list[float]) -> dict:
    """Same-time comparison of two sessions ({time: path}); one mismatch fails."""
    rows = {f"{t:g}": compare(ref.get(t), other.get(t)) for t in times}
    verdicts = {r["verdict"] for r in rows.values()}
    verdict = "fail" if "fail" in verdicts else "not_checked" if "not_checked" in verdicts else "pass"
    bad = [k for k, r in rows.items() if r["verdict"] != "pass"]
    return {"verdict": verdict, "mismatch_at": bad, "frames": rows}


def gate(frames: dict, times: list[float]) -> dict:
    """Prove the session really rendered a moving scene before any `pass`."""
    paths = [frames[t] for t in times]
    blank = blank_frames(paths)
    if blank:
        return {"verdict": "fail", "reason": "uniform frame(s) — scene not rendered: " + ", ".join(blank)}
    if len({sha256(p) for p in paths}) < len(paths):
        return {"verdict": "fail", "reason": "identical frames at different times — scene did not move"}
    return {"verdict": "pass"}


def hf_version() -> str:
    try:
        p = subprocess.run([NPX, "--yes", HF_SPEC, "--version"], capture_output=True, text=True, timeout=300)
        return (p.stdout.strip().splitlines() or ["?"])[-1] if p.returncode == 0 else f"unknown (exit {p.returncode})"
    except Exception as e:  # noqa: BLE001
        return f"unknown ({e})"


def composition_duration(project: Path) -> float:
    m = re.search(r'data-composition-id="[^"]*"[^>]*data-duration="([\d.]+)"', (project / "index.html").read_text(encoding="utf-8"))
    if not m:
        raise ValueError("no data-duration on the root composition")
    return float(m.group(1))


def snapshot(project: Path, times: list[float], out: Path) -> dict:
    """Capture frames at `times` in that order (one page). Returns {time: path}."""
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HYPERFRAMES_NO_TELEMETRY="1")
    cmd = [NPX, "--yes", HF_SPEC, "snapshot", str(project), "--at", ",".join(f"{t:g}" for t in times),
           "--no-end", "--describe", "false", "-o", str(out)]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"snapshot failed ({proc.returncode}): {proc.stderr[-2000:] or proc.stdout[-2000:]}")
    warn = seek_warnings((proc.stdout or "") + "\n" + (proc.stderr or ""))
    if warn:
        raise RuntimeError("hyperframes did not seek the timeline: " + " | ".join(warn))
    found = sorted(out.glob("frame-*-at-*.png"))
    if len(found) != len(times):
        raise RuntimeError(f"expected {len(times)} frames in {out}, found {len(found)}")
    return dict(zip(times, found))


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


def interleave(times: list[float]) -> list[float]:
    out, lo, hi = [], 0, len(times) - 1
    while lo <= hi:
        out.append(times[lo])
        if hi != lo:
            out.append(times[hi])
        lo, hi = lo + 1, hi - 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--times", default="", help="comma-separated probe times (default: 6 spread over the duration)")
    ap.add_argument("--sheets", default="0,2", help="start times of the 2-s review sheets (empty = none)")
    ap.add_argument("--sheet-offset", type=float, default=0.1,
                    help="shift of each sheet frame (s); 0.1 matches reference sheets made with ffmpeg fps=4")
    args = ap.parse_args(argv)

    report: dict = {"project": str(args.project), "hyperframes": hf_version(), "min_std": MIN_STD}
    sources = [p for p in args.project.iterdir() if p.suffix in (".js", ".html") and p.name != "gsap.min.js"]
    hits = {p.name: forbidden_calls(p.read_text(encoding="utf-8")) for p in sources}
    hits = {k: v for k, v in hits.items() if v}
    report["static_no_runtime_randomness"] = {"verdict": "fail" if hits else "pass", "hits": hits}

    try:
        dur = composition_duration(args.project)
        times = [float(x) for x in args.times.split(",") if x.strip()] or [round(dur * (k + 0.5) / 6, 3) for k in range(6)]
        times = sorted(set(times))
        report["times"] = times
        A = snapshot(args.project, times, args.out / "A")
        report["render_gate"] = gate(A, times)
        A2 = snapshot(args.project, times, args.out / "A2")
        B = snapshot(args.project, list(reversed(times)), args.out / "B")
        C = snapshot(args.project, interleave(times), args.out / "C")
        ok = report["render_gate"]["verdict"] == "pass"
        b = compare_sessions(A, A2, times)
        c_back, c_jump = compare_sessions(A, B, times), compare_sessions(A, C, times)
        cv = {c_back["verdict"], c_jump["verdict"]}
        c = {"verdict": "fail" if "fail" in cv else "not_checked" if "not_checked" in cv else "pass",
             "backward": c_back, "interleaved": c_jump}
        if not ok:   # never pass a comparison of frames that are not a render
            b = {"verdict": "fail", "reason": "render gate failed", "detail": b}
            c = {"verdict": "fail", "reason": "render gate failed", "detail": c}
        report["b_determinism"], report["c_seek_safety"] = b, c
    except Exception as e:  # noqa: BLE001 - report, never pass by default
        for k in ("render_gate", "b_determinism", "c_seek_safety"):
            report.setdefault(k, {"verdict": "not_checked", "reason": str(e)})

    sheets = []
    for s in [float(x) for x in args.sheets.split(",") if x.strip()]:
        try:
            ts = [round(s + args.sheet_offset + k * 0.25, 3) for k in range(8)]
            fr = snapshot(args.project, ts, args.out / f"sheet{s:g}")
            sheets.append(str(review_sheet([fr[t] for t in ts], args.out / f"sheet_{int(s):02d}.png")))
        except Exception as e:  # noqa: BLE001
            sheets.append(f"not_checked: {e}")
    report["a_review_sheets"] = {"verdict": "not_checked", "reason": "compare by eye", "sheets": sheets}
    print(json.dumps(report, indent=2))
    verdicts = [report[k]["verdict"] for k in ("static_no_runtime_randomness", "render_gate", "b_determinism", "c_seek_safety")]
    return 1 if "fail" in verdicts else 2 if "not_checked" in verdicts else 0


if __name__ == "__main__":
    sys.exit(main())
