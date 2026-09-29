"""Credits registry: read the licence BEFORE using an asset, and credit it.

A production method's rules, as a check:
- every third-party asset used in the video has an entry (title, author,
  licence, source) in the project's credits registry;
- a licence that requires attribution is credited on screen or in the
  description;
- a non-commercial licence (press kits are often "editorial, non
  commercial") is a warning while the channel is not monetized and an issue
  once it is;
- an unknown licence is an issue: it has to be read, not assumed.
Registry: {"monetized": bool, "entries": [{asset, title, author, license,
source_url, on_screen, in_description}]}.
"""
from __future__ import annotations

import re
from typing import Any

# licence id -> (display, attribution required, commercial use allowed)
LICENSES: dict[str, tuple[str, bool, bool]] = {
    "cc0": ("CC0", False, True),
    "public-domain": ("Public domain", False, True),
    "cc-by-2.0": ("CC BY 2.0", True, True),
    "cc-by-3.0": ("CC BY 3.0", True, True),
    "cc-by-4.0": ("CC BY 4.0", True, True),
    "cc-by-sa-2.0": ("CC BY-SA 2.0", True, True),
    "cc-by-sa-3.0": ("CC BY-SA 3.0", True, True),
    "cc-by-sa-4.0": ("CC BY-SA 4.0", True, True),
    "cc-by-nc-4.0": ("CC BY-NC 4.0", True, False),
    "cc-by-nc-sa-4.0": ("CC BY-NC-SA 4.0", True, False),
    "editorial": ("Editorial use only", True, False),
    "apache-2.0": ("Apache-2.0", True, True),
    "mit": ("MIT", True, True),
    "ofl-1.1": ("SIL OFL 1.1", True, True),
    "pexels": ("Pexels licence", False, True),
    "pixabay": ("Pixabay licence", False, True),
    "own": ("Own work", False, True),
    "generated": ("Generated", False, True),
}


def _licence_id(raw: Any) -> str:
    """'CC BY-SA 4.0', 'cc_by_4.0', 'CC-BY 4.0' -> 'cc-by-sa-4.0' / 'cc-by-4.0'."""
    t = re.sub(r"[\s_]+", "-", str(raw).strip().lower())
    t = re.sub(r"-+", "-", t)
    return {"cc-zero": "cc0", "cc0-1.0": "cc0", "apache-2": "apache-2.0", "ofl": "ofl-1.1"}.get(t, t)


def check(registry: dict[str, Any], used_assets: list[str]) -> dict[str, Any]:
    monetized = bool(registry.get("monetized"))
    entries = {e["asset"]: e for e in registry.get("entries", [])}
    issues, warnings, lines = [], [], []
    for asset in used_assets:
        e = entries.get(asset)
        if e is None:
            issues.append(f"{asset}: no credit entry (read its licence and add it to the registry)")
            continue
        lic = LICENSES.get(_licence_id(e.get("license", "")))
        if lic is None:
            issues.append(f"{asset}: unknown licence {e.get('license')!r} (read it and name it)")
            continue
        display, attribution, commercial = lic
        credited = bool(e.get("on_screen") or e.get("in_description"))
        if attribution and not credited:
            issues.append(f"{asset}: {display} requires attribution, on screen or in the description")
        if not commercial:
            if monetized:
                issues.append(f"{asset}: {display} is non-commercial and the channel is monetized")
            else:
                warnings.append(f"{asset}: {display} is non-commercial: valid only while the channel is not monetized")
        if attribution or e.get("in_description"):
            lines.append(" — ".join(x for x in (e.get("title"), e.get("author"), display) if x)
                         + (f" ({e['source_url']})" if e.get("source_url") else ""))
    return {"issues": issues, "warnings": warnings, "description": "\n".join(lines)}
