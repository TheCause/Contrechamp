"""What the installed ffmpeg can actually do.

Being on PATH is not enough: minimal builds (e.g. Homebrew's default `ffmpeg`
formula) lack libass/freetype, so `subtitles` and `drawtext` are missing and
every subtitle burn-in or FFmpeg text card fails at render time.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache

TEXT_FILTERS = ("drawtext", "subtitles")


@lru_cache(maxsize=1)
def available_filters() -> frozenset[str]:
    binary = shutil.which("ffmpeg")
    if not binary:
        return frozenset()
    proc = subprocess.run([binary, "-hide_banner", "-filters"],
                          capture_output=True, text=True, timeout=30)
    names = re.findall(r"^\s*[A-Z.|]{2,3}\s+(\w+)\s", proc.stdout or "", flags=re.MULTILINE)
    return frozenset(names)


def missing_filters(required: tuple[str, ...] = TEXT_FILTERS) -> list[str]:
    have = available_filters()
    return sorted(f for f in required if f not in have)


def warning() -> str:
    missing = missing_filters()
    if not missing:
        return ""
    return (
        f"ffmpeg: filters missing: {', '.join(missing)} — FFmpeg subtitle burn-in "
        "and text cards will fail. Install a build with libass + freetype: "
        "macOS `brew install ffmpeg-full` (keg-only: put "
        "$(brew --prefix)/opt/ffmpeg-full/bin first on PATH); Debian/Ubuntu "
        "`sudo apt install ffmpeg`; Windows: a 'full' build from gyan.dev or BtbN."
    )


def ocr_warning() -> str:
    """Preflight note when the on-screen text checks cannot run."""
    if shutil.which("tesseract"):
        return ""
    return (
        "tesseract: not installed — final_review cannot read on-screen text "
        "(captions, cards); renders with expected text will not 'pass'. Install: "
        "macOS `brew install tesseract tesseract-lang`; Debian/Ubuntu "
        "`sudo apt install tesseract-ocr tesseract-ocr-<lang>`; Windows: UB-Mannheim "
        "installer. Or set review.ocr_required: false in config.yaml."
    )
