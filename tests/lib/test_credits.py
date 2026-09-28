"""Credits registry: every third-party asset used has a read licence and its credit."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib import credits as cr  # noqa: E402


def _reg(*entries, monetized=False):
    return {"monetized": monetized, "entries": list(entries)}


PHOTO = {"asset": "presse/gilligan.jpg", "title": "Vince Gilligan", "author": "Gage Skidmore",
         "license": "CC-BY-SA-2.0", "source_url": "https://commons.wikimedia.org/x", "on_screen": True}
POSTER = {"asset": "presse/affiche.jpg", "title": "Pluribus poster", "author": "Apple TV+",
          "license": "editorial", "in_description": True}
VOICE = {"asset": "voice:fr_FR-siwis-medium", "title": "Piper voice siwis", "author": "SIWIS database",
         "license": "CC-BY-4.0", "in_description": True}


def test_credited_assets_pass_and_build_the_description_block():
    r = cr.check(_reg(PHOTO, VOICE), ["presse/gilligan.jpg", "voice:fr_FR-siwis-medium"])
    assert r["issues"] == []
    assert "Vince Gilligan — Gage Skidmore — CC BY-SA 2.0" in r["description"]


def test_an_asset_without_entry_is_an_issue():
    r = cr.check(_reg(PHOTO), ["presse/gilligan.jpg", "presse/inconnue.jpg"])
    assert any("presse/inconnue.jpg" in i and "no credit entry" in i for i in r["issues"])


def test_attribution_licence_without_credit_is_an_issue():
    silent = dict(VOICE, in_description=False)
    r = cr.check(_reg(silent), ["voice:fr_FR-siwis-medium"])
    assert any("requires attribution" in i for i in r["issues"])


def test_unknown_licence_is_an_issue():
    r = cr.check(_reg(dict(PHOTO, license="some-custom")), ["presse/gilligan.jpg"])
    assert any("unknown licence" in i for i in r["issues"])


def test_non_commercial_is_a_warning_until_the_channel_is_monetized():
    r = cr.check(_reg(POSTER), ["presse/affiche.jpg"])
    assert r["issues"] == [] and any("not monetized" in w for w in r["warnings"])
    r = cr.check(_reg(POSTER, monetized=True), ["presse/affiche.jpg"])
    assert any("non-commercial" in i for i in r["issues"])
